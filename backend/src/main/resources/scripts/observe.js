return (function() {
  var result = { url: location.href, title: document.title, elements: [] };
  var selector = [
    'a','button','input','textarea','select','option','summary',
    '[role="button"]','[role="link"]','[role="menuitem"]','[role="option"]',
    '[role="checkbox"]','[role="radio"]','[role="switch"]','[role="tab"]',
    '[role="search"]','[contenteditable=""]','[contenteditable="true"]',
    '[onclick]','[tabindex]',
    '[class*="btn"]','[class*="submit"]','[class*="login"]','[class*="search"]',
    '[class*="close"]','[class*="confirm"]','[class*="cancel"]'
  ].join(', ');

  function isVisible(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    var style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function attrs(el) {
    var out = {};
    if (el.id) out.id = el.id;
    if (el.name) out.name = el.name;
    if (el.type) out.type = el.type;
    if (el.placeholder) out.placeholder = el.placeholder;
    if ('value' in el && el.value !== undefined && el.value !== null && String(el.value).length) {
      out.value = String(el.value).substring(0, 100);
    }
    var href = el.getAttribute && el.getAttribute('href');
    if (href) out.href = href.substring(0, 100);
    var ariaLabel = el.getAttribute && el.getAttribute('aria-label');
    if (ariaLabel) out.ariaLabel = ariaLabel;
    var dataTitle = el.getAttribute && el.getAttribute('data-title');
    if (dataTitle) out.dataTitle = dataTitle;
    var title = el.getAttribute && el.getAttribute('title');
    if (title) out.title = title;
    var role = el.getAttribute && el.getAttribute('role');
    if (role) out.role = role;
    if (el.disabled) out.disabled = true;
    if (el.checked !== undefined && el.checked !== null) out.checked = !!el.checked;
    return out;
  }

  function pushElement(el, ox, oy, scope) {
    if (!isVisible(el)) return;
    var rect = el.getBoundingClientRect();
    var text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').substring(0, 80);
    if (!text && 'value' in el && el.value !== undefined && el.value !== null) {
      text = String(el.value).trim().replace(/\s+/g, ' ').substring(0, 80);
    }
    result.elements.push({
      tag: el.tagName.toLowerCase(), text: text, attrs: attrs(el),
      x: Math.round(ox + rect.left + rect.width / 2),
      y: Math.round(oy + rect.top + rect.height / 2),
      scope: scope
    });
  }

  function walk(root, ox, oy, scope, seenRoots) {
    if (!root || seenRoots.has(root)) return;
    seenRoots.add(root);
    var matches = [];
    try { matches = Array.from(root.querySelectorAll(selector)); } catch (e) {}
    for (var i = 0; i < matches.length; i++) pushElement(matches[i], ox, oy, scope);
    var all = [];
    try { all = Array.from(root.querySelectorAll('*')); } catch (e) {}
    for (var j = 0; j < all.length; j++) {
      var host = all[j];
      if (host.shadowRoot) {
        var hostRect = host.getBoundingClientRect();
        walk(host.shadowRoot, ox + hostRect.left, oy + hostRect.top, scope + ' > shadow<' + host.tagName.toLowerCase() + '>', seenRoots);
      }
      if (host.tagName === 'IFRAME') {
        try {
          var doc = host.contentDocument;
          if (doc && doc.documentElement) {
            var frameRect = host.getBoundingClientRect();
            walk(doc, ox + frameRect.left, oy + frameRect.top, scope + ' > iframe<' + (host.id || host.name || host.src || 'frame') + '>', seenRoots);
          }
        } catch (e) {}
      }
    }
  }

  try {
    var links = document.querySelectorAll('a[target="_blank"]');
    for (var t = 0; t < links.length; t++) links[t].removeAttribute('target');
  } catch(e) {}

  walk(document, 0, 0, 'document', new Set());

  var vw = window.innerWidth, vh = window.innerHeight;
  var inView = result.elements.filter(function(e) { return e.x >= 0 && e.x <= vw && e.y >= 0 && e.y <= vh; });
  var outView = result.elements.filter(function(e) { return e.x < 0 || e.x > vw || e.y < 0 || e.y > vh; });
  result.elements = inView.concat(outView).slice(0, 120);
  result.elementCount = result.elements.length;
  result.totalFound = inView.length + outView.length;
  result.viewportSize = { width: vw, height: vh };
  result.visibleText = document.body ? document.body.innerText.substring(0, 2000) : '';
  return result;
})();
