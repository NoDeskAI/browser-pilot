OBSERVE_SCRIPT = r"""
return (function() {
  var result = { url: location.href, title: document.title, elements: [] };
  var selector = [
    'a',
    'button',
    'input',
    'textarea',
    'select',
    'option',
    'summary',
    'img',
    '[role="button"]',
    '[role="link"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="switch"]',
    '[role="tab"]',
    '[contenteditable=""]',
    '[contenteditable="true"]',
    '[onclick]',
    '[tabindex]'
  ].join(', ');

  var seenEls = new Set();
  var VW = window.innerWidth || 1280, VH = window.innerHeight || 720;

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
    var role = el.getAttribute && el.getAttribute('role');
    if (role) out.role = role;
    if (el.disabled) out.disabled = true;
    if (el.checked !== undefined && el.checked !== null) out.checked = !!el.checked;
    if (el.tagName === 'IMG') {
      var alt = el.getAttribute('alt');
      if (alt) out.alt = alt;
      var src = el.getAttribute('src');
      if (src) out.src = src.substring(0, 100);
    }
    return out;
  }

  function pushElement(el, ox, oy, scope) {
    if (seenEls.has(el)) return;
    if (!isVisible(el)) return;
    seenEls.add(el);
    var rect = el.getBoundingClientRect();
    var text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').substring(0, 80);
    if (!text && 'value' in el && el.value !== undefined && el.value !== null) {
      text = String(el.value).trim().replace(/\s+/g, ' ').substring(0, 80);
    }
    result.elements.push({
      tag: el.tagName.toLowerCase(),
      text: text,
      attrs: attrs(el),
      x: Math.round(ox + rect.left + rect.width / 2),
      y: Math.round(oy + rect.top + rect.height / 2),
      scope: scope
    });
  }

  var CLICKABLE_TAGS = {DIV:1,SPAN:1,LI:1,TD:1,TH:1,P:1,H1:1,H2:1,H3:1,H4:1,H5:1,H6:1,LABEL:1,I:1,EM:1,STRONG:1,IMG:1,CANVAS:1,SVG:1};

  function walk(root, ox, oy, scope, seenRoots) {
    if (!root || seenRoots.has(root)) return;
    seenRoots.add(root);

    var matches = [];
    try { matches = Array.from(root.querySelectorAll(selector)); } catch (e) {}
    for (var i = 0; i < matches.length; i++) {
      pushElement(matches[i], ox, oy, scope);
    }

    var all = [];
    try { all = Array.from(root.querySelectorAll('*')); } catch (e) {}
    for (var j = 0; j < all.length; j++) {
      var host = all[j];

      if (!seenEls.has(host) && CLICKABLE_TAGS[host.tagName] && result.elements.length < 300) {
        try {
          var cs = window.getComputedStyle(host);
          if (cs.cursor === 'pointer') {
            var rect = host.getBoundingClientRect();
            if (rect.width > 5 && rect.height > 5 && rect.right > 0 && rect.bottom > 0 && rect.left < VW && rect.top < VH) {
              pushElement(host, ox, oy, scope);
            }
          }
        } catch(e) {}
      }

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

  walk(document, 0, 0, 'document', new Set());
  result.visibleText = document.body ? document.body.innerText.substring(0, 2000) : '';
  result.viewportOffset = window.__nwb_vp_offset || { x: 0, y: 0 };
  return result;
})();
"""

CLICK_ELEMENT_SCRIPT = r"""
return (function(selector) {
  function search(root) {
    var found = null;
    try {
      if (root.querySelector) {
        found = root.querySelector(selector);
        if (found) return found;
      }
    } catch (e) {}

    var all = [];
    try { all = Array.from(root.querySelectorAll('*')); } catch (e) {}
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.shadowRoot) {
        var shadowFound = search(el.shadowRoot);
        if (shadowFound) return shadowFound;
      }
      if (el.tagName === 'IFRAME') {
        try {
          var doc = el.contentDocument;
          if (doc && doc.documentElement) {
            var frameFound = search(doc);
            if (frameFound) return frameFound;
          }
        } catch (e) {}
      }
    }
    return null;
  }

  var el = search(document);
  if (!el) return { found: false };
  if (el.scrollIntoView) el.scrollIntoView({ block: 'center', inline: 'center' });
  if (el.focus) el.focus();
  if (el.click) el.click();
  var rect = el.getBoundingClientRect();
  return {
    found: true,
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').substring(0, 80),
    x: Math.round(rect.left + rect.width / 2),
    y: Math.round(rect.top + rect.height / 2)
  };
})(arguments[0]);
"""

_STEALTH_SCRIPT_CACHE: str | None = None


def get_stealth_script() -> str:
    """Load stealth.js from the repo. Cached after first call."""
    global _STEALTH_SCRIPT_CACHE
    if _STEALTH_SCRIPT_CACHE is not None:
        return _STEALTH_SCRIPT_CACHE
    import pathlib
    candidates = [
        pathlib.Path(__file__).resolve().parents[3] / "services" / "selenium-chrome" / "stealth-ext" / "stealth.js",
    ]
    for p in candidates:
        if p.exists():
            _STEALTH_SCRIPT_CACHE = p.read_text()
            return _STEALTH_SCRIPT_CACHE
    _STEALTH_SCRIPT_CACHE = ""
    return _STEALTH_SCRIPT_CACHE
