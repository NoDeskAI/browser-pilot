return (function(searchText, exactMatch) {
  if (!searchText || typeof searchText !== 'string') return { found: false, searched: searchText, error: 'searchText is empty or null' };
  var candidates = [];

  function isVisible(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    var style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden') return false;
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.bottom > 0;
  }

  function getTexts(el) {
    var texts = [];
    var directText = '';
    for (var j = 0; j < el.childNodes.length; j++) {
      if (el.childNodes[j].nodeType === Node.TEXT_NODE) directText += el.childNodes[j].textContent;
    }
    if (directText.trim()) texts.push(directText.trim());
    var ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) texts.push(ariaLabel.trim());
    var dataTitle = el.getAttribute('data-title');
    if (dataTitle) texts.push(dataTitle.trim());
    var title = el.getAttribute('title');
    if (title) texts.push(title.trim());
    var dataTip = el.getAttribute('data-tip') || el.getAttribute('data-tooltip');
    if (dataTip) texts.push(dataTip.trim());
    if (texts.length === 0 && el.children.length === 0) {
      var inner = (el.innerText || '').trim();
      if (inner) texts.push(inner);
    }
    return texts;
  }

  function check(el, ox, oy) {
    if (!isVisible(el)) return;
    var texts = getTexts(el);
    for (var t = 0; t < texts.length; t++) {
      var text = texts[t];
      var matched = false;
      if (exactMatch) { matched = text === searchText; }
      else { matched = text.indexOf(searchText) >= 0 || searchText.indexOf(text) >= 0; }
      if (matched) {
        var score = Math.abs(text.length - searchText.length);
        var rect = el.getBoundingClientRect();
        candidates.push({ el: el, text: text, ox: ox, oy: oy, rect: rect, score: score });
        break;
      }
    }
  }

  function walk(root, ox, oy, seen) {
    if (!root || seen.has(root)) return;
    seen.add(root);
    var all = [];
    try { all = Array.from(root.querySelectorAll('*')); } catch(e) {}
    for (var i = 0; i < all.length; i++) {
      check(all[i], ox, oy);
      if (all[i].shadowRoot) {
        var r = all[i].getBoundingClientRect();
        walk(all[i].shadowRoot, ox + r.left, oy + r.top, seen);
      }
      if (all[i].tagName === 'IFRAME') {
        try {
          var doc = all[i].contentDocument;
          if (doc) { var r2 = all[i].getBoundingClientRect(); walk(doc, ox + r2.left, oy + r2.top, seen); }
        } catch(e) {}
      }
    }
  }

  try { var blanks = document.querySelectorAll('a[target="_blank"]'); for (var b = 0; b < blanks.length; b++) blanks[b].removeAttribute('target'); } catch(e) {}

  walk(document, 0, 0, new Set());
  if (candidates.length === 0) return { found: false, searched: searchText };

  candidates.sort(function(a, b) { return a.score - b.score; });
  var best = candidates[0];
  var el = best.el;
  try { if (el.removeAttribute) el.removeAttribute('target'); var p = el.closest ? el.closest('a') : null; if (p) p.removeAttribute('target'); } catch(e) {}
  if (el.scrollIntoView) el.scrollIntoView({ block: 'center', inline: 'center' });
  if (el.focus) el.focus();
  if (arguments[2] !== false && el.click) el.click();
  var rect = el.getBoundingClientRect();
  return {
    found: true, tag: el.tagName.toLowerCase(), text: best.text.substring(0, 80),
    matchedIn: best.text === (el.getAttribute('aria-label')||'').trim() ? 'aria-label' : best.text === (el.getAttribute('data-title')||'').trim() ? 'data-title' : 'text',
    x: Math.round(best.ox + rect.left + rect.width / 2),
    y: Math.round(best.oy + rect.top + rect.height / 2),
    allMatches: candidates.length
  };
})(arguments[0], arguments[1], arguments[2]);
