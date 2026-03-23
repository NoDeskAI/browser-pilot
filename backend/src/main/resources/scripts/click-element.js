return (function(selector) {
  function search(root) {
    var found = null;
    try { if (root.querySelector) { found = root.querySelector(selector); if (found) return found; } } catch (e) {}
    var all = [];
    try { all = Array.from(root.querySelectorAll('*')); } catch (e) {}
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.shadowRoot) { var shadowFound = search(el.shadowRoot); if (shadowFound) return shadowFound; }
      if (el.tagName === 'IFRAME') {
        try { var doc = el.contentDocument; if (doc && doc.documentElement) { var frameFound = search(doc); if (frameFound) return frameFound; } } catch (e) {}
      }
    }
    return null;
  }

  try { var blanks = document.querySelectorAll('a[target="_blank"]'); for (var b = 0; b < blanks.length; b++) blanks[b].removeAttribute('target'); } catch(e) {}

  var el = search(document);
  if (!el) return { found: false };
  try { if (el.removeAttribute) el.removeAttribute('target'); var p = el.closest ? el.closest('a') : null; if (p) p.removeAttribute('target'); } catch(e) {}
  if (el.scrollIntoView) el.scrollIntoView({ block: 'center', inline: 'center' });
  if (el.focus) el.focus();
  if (el.click) el.click();
  var rect = el.getBoundingClientRect();
  return {
    found: true, tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').substring(0, 80),
    x: Math.round(rect.left + rect.width / 2),
    y: Math.round(rect.top + rect.height / 2)
  };
})(arguments[0]);
