(function () {
  if (window.__domDiffActive) return;
  window.__domDiffActive = true;

  let _nextId = 1;
  const _ids = new WeakMap();
  const nid = (n) => {
    if (!_ids.has(n)) _ids.set(n, _nextId++);
    return _ids.get(n);
  };

  const BASE = document.baseURI;
  const URL_ATTRS = new Set([
    'src', 'href', 'action', 'poster', 'data',
    'formaction', 'cite', 'background',
  ]);

  function abs(u) {
    if (!u || /^(data|blob|javascript|#)/.test(u)) return u;
    try { return new URL(u, BASE).href; } catch { return u; }
  }

  function cssUrls(s) {
    return s.replace(/url\(\s*(['"]?)([^)'"]+)\1\s*\)/g, (m, q, u) => {
      if (/^(data|blob):/.test(u)) return m;
      return `url(${q}${abs(u)}${q})`;
    });
  }

  function ser(n) {
    if (!n) return null;
    const t = n.nodeType;

    if (t === 3) {
      let d = n.data;
      if (n.parentElement && n.parentElement.tagName === 'STYLE') d = cssUrls(d);
      return { t: 3, id: nid(n), d };
    }

    if (t === 8) return { t: 8, id: nid(n), d: n.data };

    if (t === 1) {
      const tag = n.tagName.toLowerCase();
      if (tag === 'script' || tag === 'noscript') return null;

      const a = {};
      for (let i = 0; i < n.attributes.length; i++) {
        const at = n.attributes[i];
        let v = at.value;
        if (URL_ATTRS.has(at.name)) v = abs(v);
        else if (at.name === 'style') v = cssUrls(v);
        else if (at.name === 'srcset') {
          v = v.split(',').map((s) => {
            const p = s.trim().split(/\s+/);
            if (p[0]) p[0] = abs(p[0]);
            return p.join(' ');
          }).join(', ');
        }
        a[at.name] = v;
      }

      const c = [];
      for (let i = 0; i < n.childNodes.length; i++) {
        const s = ser(n.childNodes[i]);
        if (s) c.push(s);
      }

      const r = { t: 1, id: nid(n), tag, a, c };
      if (n.namespaceURI && n.namespaceURI !== 'http://www.w3.org/1999/xhtml') {
        r.ns = n.namespaceURI;
      }
      return r;
    }

    if (t === 10) {
      return { t: 10, id: nid(n), name: n.name, pub: n.publicId || '', sys: n.systemId || '' };
    }

    return null;
  }

  const snapshot = { url: location.href, html: ser(document.documentElement) };
  try {
    window.__domDiffEmit(JSON.stringify({ type: 'snapshot', data: snapshot }));
  } catch (e) {
    console.error('[dom-diff-recorder] snapshot send failed:', e);
  }

  let buf = [];
  let flushTimer = null;

  function flush() {
    flushTimer = null;
    if (buf.length === 0) return;
    const ops = buf;
    buf = [];
    try {
      window.__domDiffEmit(JSON.stringify({ type: 'mutations', data: ops }));
    } catch {}
  }

  const observer = new MutationObserver((mutations) => {
    for (const m of mutations) {
      if (m.type === 'childList') {
        for (const node of m.addedNodes) {
          const s = ser(node);
          if (!s) continue;
          buf.push({
            op: 'add',
            pid: nid(m.target),
            ref: m.nextSibling && _ids.has(m.nextSibling) ? _ids.get(m.nextSibling) : null,
            n: s,
          });
        }
        for (const node of m.removedNodes) {
          if (_ids.has(node)) buf.push({ op: 'rm', id: _ids.get(node) });
        }
      }
      if (m.type === 'attributes') {
        let v = m.target.getAttribute(m.attributeName);
        if (v !== null) {
          if (URL_ATTRS.has(m.attributeName)) v = abs(v);
          else if (m.attributeName === 'style') v = cssUrls(v);
        }
        buf.push({ op: 'attr', id: nid(m.target), k: m.attributeName, v });
      }
      if (m.type === 'characterData') {
        let d = m.target.data;
        if (m.target.parentElement && m.target.parentElement.tagName === 'STYLE') d = cssUrls(d);
        buf.push({ op: 'text', id: nid(m.target), d });
      }
    }
    if (!flushTimer) flushTimer = setTimeout(flush, 50);
  });

  observer.observe(document, {
    childList: true,
    attributes: true,
    characterData: true,
    subtree: true,
  });

  document.addEventListener('input', (e) => {
    const el = e.target;
    if (!el || !_ids.has(el)) return;
    if ('value' in el) {
      buf.push({ op: 'prop', id: _ids.get(el), k: 'value', v: el.value });
    }
    if ('checked' in el) {
      buf.push({ op: 'prop', id: _ids.get(el), k: 'checked', v: el.checked });
    }
    if (!flushTimer) flushTimer = setTimeout(flush, 50);
  }, true);

  document.addEventListener('change', (e) => {
    const el = e.target;
    if (!el || !_ids.has(el)) return;
    if (el.tagName === 'SELECT' && 'value' in el) {
      buf.push({ op: 'prop', id: _ids.get(el), k: 'value', v: el.value });
      if (!flushTimer) flushTimer = setTimeout(flush, 50);
    }
  }, true);

  let scrollTimer = null;
  window.addEventListener('scroll', () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      try {
        window.__domDiffEmit(JSON.stringify({
          type: 'scroll',
          data: { x: window.scrollX, y: window.scrollY },
        }));
      } catch {}
    }, 100);
  }, { passive: true });
})()
