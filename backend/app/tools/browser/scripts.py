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
    '[role="button"]',
    '[role="link"]',
    '[role="menuitem"]',
    '[role="option"]',
    '[role="checkbox"]',
    '[role="radio"]',
    '[role="switch"]',
    '[contenteditable=""]',
    '[contenteditable="true"]',
    '[onclick]',
    '[tabindex]'
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
      tag: el.tagName.toLowerCase(),
      text: text,
      attrs: attrs(el),
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
    for (var i = 0; i < matches.length; i++) {
      pushElement(matches[i], ox, oy, scope);
    }

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

  walk(document, 0, 0, 'document', new Set());
  result.visibleText = document.body ? document.body.innerText.substring(0, 2000) : '';
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

STEALTH_SCRIPT = r"""(function(){
  'use strict';
  function mn(fn,name){fn.toString=function(){return 'function '+name+'() { [native code] }'};return fn;}

  try{Object.defineProperty(Object.getPrototypeOf(navigator),'webdriver',{get:mn(function(){return undefined},'get webdriver'),configurable:true})}catch(e){}

  try{
    Object.getOwnPropertyNames(window).forEach(function(p){if(/^cdc_/.test(p))try{delete window[p]}catch(e){}});
    Object.getOwnPropertyNames(document).forEach(function(p){if(/^\$cdc_/.test(p))try{Object.defineProperty(document,p,{get:function(){return undefined},configurable:true})}catch(e){}});
  }catch(e){}

  try{
    if(!window.chrome)window.chrome={};
    if(!window.chrome.runtime){
      window.chrome.runtime={
        connect:mn(function(){return{onDisconnect:{addListener:function(){}},onMessage:{addListener:function(){}},postMessage:function(){}};},'connect'),
        sendMessage:mn(function(a,b,c){if(typeof c==='function')c();},'sendMessage'),
      };
    }
    if(!window.chrome.csi)window.chrome.csi=mn(function(){return{}},'csi');
    if(!window.chrome.loadTimes)window.chrome.loadTimes=mn(function(){return{}},'loadTimes');
  }catch(e){}

  try{Object.defineProperty(navigator,'plugins',{get:mn(function(){
    return{0:{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',length:1},1:{name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',length:1},2:{name:'Native Client',filename:'internal-nacl-plugin',description:'',length:2},length:3,item:function(i){return this[i]||null},namedItem:function(n){for(var i=0;i<3;i++)if(this[i]&&this[i].name===n)return this[i];return null},refresh:function(){}};
  },'get plugins'),configurable:true})}catch(e){}

  try{Object.defineProperty(Object.getPrototypeOf(navigator),'languages',{get:mn(function(){return['zh-CN','zh','en-US','en']},'get languages'),configurable:true})}catch(e){}
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'hardwareConcurrency',{get:mn(function(){return 8},'get hardwareConcurrency'),configurable:true})}catch(e){}
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'deviceMemory',{get:mn(function(){return 8},'get deviceMemory'),configurable:true})}catch(e){}

  try{
    ['WebGLRenderingContext','WebGL2RenderingContext'].forEach(function(c){
      if(!window[c])return;var orig=window[c].prototype.getParameter;
      window[c].prototype.getParameter=mn(function(p){
        if(p===0x9245)return'Intel Inc.';if(p===0x9246)return'Intel Iris OpenGL Engine';return orig.call(this,p);
      },'getParameter');
    });
  }catch(e){}

  try{
    var oq=navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query=mn(function(d){
      if(d.name==='notifications')return Promise.resolve({state:'prompt',onchange:null});return oq(d);
    },'query');
  }catch(e){}

  try{
    var sv={width:1920,height:1080,availWidth:1920,availHeight:1040,colorDepth:24,pixelDepth:24};
    Object.keys(sv).forEach(function(k){Object.defineProperty(screen,k,{get:function(){return sv[k]},configurable:true})});
  }catch(e){}

  try{
    Object.defineProperty(window,'outerWidth',{get:function(){return window.innerWidth},configurable:true});
    Object.defineProperty(window,'outerHeight',{get:function(){return window.innerHeight+85},configurable:true});
  }catch(e){}

  try{
    var origTDU=HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL=mn(function(){
      try{var c=this.getContext('2d');if(c&&this.width>0&&this.height>0){var d=c.getImageData(0,0,1,1);d.data[3]=d.data[3]^1;c.putImageData(d,0,0)}}catch(e){}
      return origTDU.apply(this,arguments);
    },'toDataURL');
  }catch(e){}
})()"""
