import type { Plugin } from 'vite'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { streamText, tool, stepCountIs, type ModelMessage } from 'ai'
import { createOpenAI } from '@ai-sdk/openai'
import { createAnthropic } from '@ai-sdk/anthropic'
import { z } from 'zod'
import { SERVICE_MAP, dockerCompose, getStatuses, log as sharedLog } from './lib/docker'

const SELENIUM_BASE = 'http://localhost:4444'

function log(msg: string, ...args: unknown[]) {
  sharedLog('ai-agent', msg, ...args)
}

// ---------------------------------------------------------------------------
// Selenium WebDriver session management
// ---------------------------------------------------------------------------

let sessionId: string | null = null
let sessionLock: Promise<string> | null = null
let activeRequestSignal: AbortSignal | null = null

async function wdFetch(urlPath: string, init?: RequestInit, timeoutMs = 30_000): Promise<any> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const signal = activeRequestSignal
      ? AbortSignal.any([controller.signal, activeRequestSignal])
      : controller.signal
    const resp = await fetch(`${SELENIUM_BASE}${urlPath}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
      signal,
    })
    const data: any = await resp.json()
    if (data.value?.error) {
      throw new Error(`WebDriver ${data.value.error}: ${data.value.message}`)
    }
    return data.value
  } catch (e: any) {
    if (e.name === 'AbortError') throw new Error(`WebDriver timeout (${timeoutMs}ms): ${urlPath}`)
    throw e
  } finally {
    clearTimeout(timer)
  }
}

async function captureScreenshot(sid: string): Promise<string | null> {
  try {
    return await wdFetch(`/session/${sid}/screenshot`, undefined, 10_000)
  } catch {
    return null
  }
}

async function cleanupStaleSession(existingId: string) {
  try {
    await fetch(`${SELENIUM_BASE}/session/${existingId}`, { method: 'DELETE' })
    log(`Cleaned up stale session: ${existingId}`)
  } catch {}
}

async function findExistingSession(): Promise<string | null> {
  try {
    const resp = await fetch(`${SELENIUM_BASE}/status`)
    const status: any = await resp.json()
    const nodes = status?.value?.nodes || []
    for (const node of nodes) {
      for (const slot of node.slots || []) {
        if (slot.session?.sessionId) return slot.session.sessionId
      }
    }
  } catch {}
  return null
}

async function ensureSessionImpl(): Promise<string> {
  if (sessionId) {
    try {
      await wdFetch(`/session/${sessionId}/url`, undefined, 5000)
      return sessionId
    } catch {
      sessionId = null
    }
  }

  const existingId = await findExistingSession()
  if (existingId) {
    log(`Found existing session: ${existingId}, reusing it`)
    sessionId = existingId
    try {
      await wdFetch(`/session/${sessionId}/url`, undefined, 5000)
      return sessionId
    } catch {
      log(`Existing session dead, cleaning up`)
      await cleanupStaleSession(existingId)
      sessionId = null
    }
  }

  log('Creating new WebDriver session...')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15_000)
  try {
    const resp = await fetch(`${SELENIUM_BASE}/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        capabilities: {
          alwaysMatch: {
            browserName: 'chrome',
            'goog:chromeOptions': {
              args: [
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--window-size=1920,1080',
                '--lang=zh-CN',
              ],
              excludeSwitches: ['enable-automation'],
              useAutomationExtension: false,
            },
          },
        },
      }),
      signal: controller.signal,
    })
    const data: any = await resp.json()
    sessionId = data.value.sessionId
    log(`WebDriver session created: ${sessionId}`)

    // Set window rect for consistent viewport
    try {
      await wdFetch(`/session/${sessionId}/window/rect`, {
        method: 'POST',
        body: JSON.stringify({ width: 1920, height: 1080 }),
      }, 5000)
    } catch {}

    // Inject stealth anti-detection scripts
    await injectStealth(sessionId)
  } catch (e: any) {
    if (e.name === 'AbortError') throw new Error('WebDriver session creation timed out (15s). Selenium might be busy.')
    throw e
  } finally {
    clearTimeout(timer)
  }

  return sessionId!
}

async function ensureSession(): Promise<string> {
  if (sessionLock) return sessionLock
  sessionLock = ensureSessionImpl().finally(() => { sessionLock = null })
  return sessionLock
}

async function switchToLatestTab(sid: string): Promise<void> {
  try {
    const handles: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000)
    if (handles && handles.length > 1) {
      const current = await wdFetch(`/session/${sid}/window`, undefined, 5000)
      const latest = handles[handles.length - 1]
      if (current !== latest) {
        log(`Switching from tab ${current} to latest tab ${latest} (${handles.length} tabs open)`)
        await wdFetch(`/session/${sid}/window`, {
          method: 'POST',
          body: JSON.stringify({ handle: latest }),
        }, 5000)
      }
    }
  } catch (e: any) {
    log(`switchToLatestTab failed: ${e.message}`)
  }
}

async function closeOtherTabs(sid: string): Promise<void> {
  try {
    const handles: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000)
    if (handles && handles.length > 1) {
      const current = await wdFetch(`/session/${sid}/window`, undefined, 5000)
      for (const h of handles) {
        if (h !== current) {
          await wdFetch(`/session/${sid}/window`, { method: 'POST', body: JSON.stringify({ handle: h }) }, 5000)
          await wdFetch(`/session/${sid}/window`, { method: 'DELETE' }, 5000)
        }
      }
      await wdFetch(`/session/${sid}/window`, { method: 'POST', body: JSON.stringify({ handle: current }) }, 5000)
      log(`Closed ${handles.length - 1} extra tab(s)`)
    }
  } catch (e: any) {
    log(`closeOtherTabs failed: ${e.message}`)
  }
}

const KEY_MAP: Record<string, string> = {
  Enter: '\uE007', Tab: '\uE004', Escape: '\uE00C', Backspace: '\uE003',
  Delete: '\uE017', Space: '\uE00D',
  ArrowUp: '\uE013', ArrowDown: '\uE014', ArrowLeft: '\uE012', ArrowRight: '\uE011',
  Home: '\uE011', End: '\uE010', PageUp: '\uE00E', PageDown: '\uE00F',
}

// ---------------------------------------------------------------------------
// RAG context
// ---------------------------------------------------------------------------

const SYSTEM_PROMPT = `你是 NoDeskPane 项目的 AI 助手，具备浏览器操控和 Docker 管理能力。

## 项目背景
NoDeskPane 在网页中实时显示并操控运行在 Docker 容器内的远程浏览器。当前使用 Selenium Grid 方案，用户通过 noVNC 实时观看你的所有操作。

## 可用 Docker 方案 ID
selenium

## 核心规则 — 必须严格遵守

### 绝对禁止幻觉
- **你必须通过调用工具来执行任何浏览器操作，绝对不能只用文字描述你做了什么。**
- **如果你没有调用 browser_navigate / browser_click / browser_type 等工具，那你就没有执行任何操作。不要假装你做了。**
- **每次操作后必须调用 browser_observe 验证结果，并基于 observe 返回的真实数据向用户汇报。**
- 如果工具返回了错误（ok: false 或 error 字段），必须如实告知用户操作失败，不要编造成功的结果。
- 如果 Selenium 服务未运行或连接失败，告诉用户需要先启动 selenium 服务，不要假装操作成功。

### 操作流程
1. 确认 Selenium 服务运行中（必要时 docker_start selenium）
2. 操作前先 browser_observe 获取页面结构
3. 执行具体操作（navigate/click/type 等）
4. 操作后再次 browser_observe 确认结果
5. 根据 observe 返回的**真实页面数据**回复用户

### 工具使用规范
- 优先 browser_click_element + CSS 选择器（精确）
- browser_click 坐标点击作为备选
- 输入前先点击目标输入框
- 用中文回答用户`

// ---------------------------------------------------------------------------
// Browser tools (Selenium WebDriver)
// ---------------------------------------------------------------------------

// JS snippet injected into browser to extract page structure
const OBSERVE_SCRIPT = `
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
`

const CLICK_ELEMENT_SCRIPT = `
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
    text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').substring(0, 80),
    x: Math.round(rect.left + rect.width / 2),
    y: Math.round(rect.top + rect.height / 2)
  };
})(arguments[0]);
`

// ---------------------------------------------------------------------------
// Stealth: anti-bot evasion script (injected via CDP on every new document)
// ---------------------------------------------------------------------------

const STEALTH_SCRIPT = `(function(){
  'use strict';
  function mn(fn,name){fn.toString=function(){return 'function '+name+'() { [native code] }'};return fn;}

  // 1. navigator.webdriver → undefined
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'webdriver',{get:mn(function(){return undefined},'get webdriver'),configurable:true})}catch(e){}

  // 2. Remove ChromeDriver cdc_ indicators
  try{
    Object.getOwnPropertyNames(window).forEach(function(p){if(/^cdc_/.test(p))try{delete window[p]}catch(e){}});
    Object.getOwnPropertyNames(document).forEach(function(p){if(/^\\$cdc_/.test(p))try{Object.defineProperty(document,p,{get:function(){return undefined},configurable:true})}catch(e){}});
  }catch(e){}

  // 3. chrome.runtime / csi / loadTimes
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

  // 4. navigator.plugins (realistic Chrome set)
  try{Object.defineProperty(navigator,'plugins',{get:mn(function(){
    return{0:{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',length:1},1:{name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',length:1},2:{name:'Native Client',filename:'internal-nacl-plugin',description:'',length:2},length:3,item:function(i){return this[i]||null},namedItem:function(n){for(var i=0;i<3;i++)if(this[i]&&this[i].name===n)return this[i];return null},refresh:function(){}};
  },'get plugins'),configurable:true})}catch(e){}

  // 5. navigator.languages
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'languages',{get:mn(function(){return['zh-CN','zh','en-US','en']},'get languages'),configurable:true})}catch(e){}

  // 6. navigator.hardwareConcurrency → 8
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'hardwareConcurrency',{get:mn(function(){return 8},'get hardwareConcurrency'),configurable:true})}catch(e){}

  // 7. navigator.deviceMemory → 8
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'deviceMemory',{get:mn(function(){return 8},'get deviceMemory'),configurable:true})}catch(e){}

  // 8. WebGL vendor / renderer → Intel
  try{
    ['WebGLRenderingContext','WebGL2RenderingContext'].forEach(function(c){
      if(!window[c])return;var orig=window[c].prototype.getParameter;
      window[c].prototype.getParameter=mn(function(p){
        if(p===0x9245)return'Intel Inc.';if(p===0x9246)return'Intel Iris OpenGL Engine';return orig.call(this,p);
      },'getParameter');
    });
  }catch(e){}

  // 9. Permissions API
  try{
    var oq=navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query=mn(function(d){
      if(d.name==='notifications')return Promise.resolve({state:'prompt',onchange:null});return oq(d);
    },'query');
  }catch(e){}

  // 10. Screen dimensions → 1920×1080
  try{
    var sv={width:1920,height:1080,availWidth:1920,availHeight:1040,colorDepth:24,pixelDepth:24};
    Object.keys(sv).forEach(function(k){Object.defineProperty(screen,k,{get:function(){return sv[k]},configurable:true})});
  }catch(e){}

  // 11. window.outerWidth / outerHeight match inner
  try{
    Object.defineProperty(window,'outerWidth',{get:function(){return window.innerWidth},configurable:true});
    Object.defineProperty(window,'outerHeight',{get:function(){return window.innerHeight+85},configurable:true});
  }catch(e){}

  // 12. Canvas toDataURL fingerprint noise (1-bit alpha flip on single pixel)
  try{
    var origTDU=HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL=mn(function(){
      try{var c=this.getContext('2d');if(c&&this.width>0&&this.height>0){var d=c.getImageData(0,0,1,1);d.data[3]=d.data[3]^1;c.putImageData(d,0,0)}}catch(e){}
      return origTDU.apply(this,arguments);
    },'toDataURL');
  }catch(e){}
})()`

async function injectStealth(sid: string): Promise<void> {
  // 1. CDP: inject on every future page load
  try {
    await wdFetch(`/session/${sid}/goog/cdp/execute`, {
      method: 'POST',
      body: JSON.stringify({
        cmd: 'Page.addScriptToEvaluateOnNewDocument',
        params: { source: STEALTH_SCRIPT },
      }),
    }, 5000)
    log('Stealth: addScriptToEvaluateOnNewDocument OK')
  } catch (e: any) {
    log(`Stealth: CDP inject failed (${e.message}), will rely on execute_script`)
  }

  // 2. Apply to current page immediately
  try {
    await wdFetch(`/session/${sid}/execute/sync`, {
      method: 'POST',
      body: JSON.stringify({ script: `return ${STEALTH_SCRIPT}`, args: [] }),
    }, 5000)
  } catch {}

  // 3. Set timezone to Asia/Shanghai via CDP
  try {
    await wdFetch(`/session/${sid}/goog/cdp/execute`, {
      method: 'POST',
      body: JSON.stringify({
        cmd: 'Emulation.setTimezoneOverride',
        params: { timezoneId: 'Asia/Shanghai' },
      }),
    }, 5000)
    log('Stealth: timezone → Asia/Shanghai')
  } catch {}
}

// ---------------------------------------------------------------------------
// Humanized input helpers
// ---------------------------------------------------------------------------

function humanKeyActions(text: string): any[] {
  const actions: any[] = []
  for (const ch of text) {
    if (actions.length > 0) {
      actions.push({ type: 'pause', duration: 30 + Math.floor(Math.random() * 90) })
    }
    actions.push({ type: 'keyDown', value: ch })
    actions.push({ type: 'pause', duration: 8 + Math.floor(Math.random() * 25) })
    actions.push({ type: 'keyUp', value: ch })
  }
  return actions
}

function humanClickActions(x: number, y: number): any[] {
  const steps = 3 + Math.floor(Math.random() * 4)
  const startX = x + Math.floor(Math.random() * 120 - 60)
  const startY = y + Math.floor(Math.random() * 120 - 60)
  const cpX = (startX + x) / 2 + Math.floor(Math.random() * 40 - 20)
  const cpY = (startY + y) / 2 + Math.floor(Math.random() * 40 - 20)

  const actions: any[] = []
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    const px = (1 - t) * (1 - t) * startX + 2 * (1 - t) * t * cpX + t * t * x
    const py = (1 - t) * (1 - t) * startY + 2 * (1 - t) * t * cpY + t * t * y
    actions.push({
      type: 'pointerMove',
      duration: 15 + Math.floor(Math.random() * 35),
      x: Math.max(0, Math.round(px)),
      y: Math.max(0, Math.round(py)),
      origin: 'viewport',
    })
  }
  // final precise position (±1px natural jitter)
  actions.push({
    type: 'pointerMove', duration: 10,
    x: x + Math.floor(Math.random() * 3 - 1),
    y: y + Math.floor(Math.random() * 3 - 1),
    origin: 'viewport',
  })
  actions.push({ type: 'pause', duration: 15 + Math.floor(Math.random() * 50) })
  actions.push({ type: 'pointerDown', button: 0 })
  actions.push({ type: 'pause', duration: 30 + Math.floor(Math.random() * 60) })
  actions.push({ type: 'pointerUp', button: 0 })
  return actions
}

async function quickObserve(sid: string): Promise<{ url: string; title: string; elementCount: number }> {
  try {
    const result = await wdFetch(`/session/${sid}/execute/sync`, {
      method: 'POST',
      body: JSON.stringify({ script: OBSERVE_SCRIPT, args: [] }),
    }, 10_000)
    return { url: result?.url || '', title: result?.title || '', elementCount: result?.elements?.length || 0 }
  } catch {
    return { url: '(observe failed)', title: '', elementCount: 0 }
  }
}

const browserTools = {
  browser_navigate: tool({
    description: '在远程浏览器当前标签页中导航到指定 URL',
    inputSchema: z.object({
      url: z.string().describe('要访问的完整 URL'),
    }),
    execute: async ({ url }) => {
      try {
        const sid = await ensureSession()
        await wdFetch(`/session/${sid}/url`, {
          method: 'POST',
          body: JSON.stringify({ url }),
        }, 60_000)
        await new Promise(r => setTimeout(r, 1500))
        const page = await quickObserve(sid)
        return { ok: true, navigatedTo: url, currentPage: page }
      } catch (e: any) {
        return { ok: false, error: e.message, hint: 'Navigation failed. Selenium may be down. Try docker_start selenium first.' }
      }
    },
  }),

  browser_list_tabs: tool({
    description: '列出远程浏览器当前打开的所有标签页，返回每个标签页的 handle 和基本信息',
    inputSchema: z.object({}),
    execute: async () => {
      try {
        const sid = await ensureSession()
        const handles: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000)
        const currentHandle = await wdFetch(`/session/${sid}/window`, undefined, 5000)
        const tabs: { handle: string; url: string; title: string; active: boolean }[] = []
        for (const h of handles) {
          await wdFetch(`/session/${sid}/window`, { method: 'POST', body: JSON.stringify({ handle: h }) }, 5000)
          const url = await wdFetch(`/session/${sid}/url`, undefined, 5000)
          const title = await wdFetch(`/session/${sid}/title`, undefined, 5000)
          tabs.push({ handle: h, url, title, active: h === currentHandle })
        }
        await wdFetch(`/session/${sid}/window`, { method: 'POST', body: JSON.stringify({ handle: currentHandle }) }, 5000)
        return { ok: true, tabs, count: tabs.length }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_switch_tab: tool({
    description: '切换到指定标签页（通过 handle 或索引）。可选关闭当前标签页后再切换。',
    inputSchema: z.object({
      handle: z.string().optional().describe('目标标签页的 handle（从 browser_list_tabs 获取）'),
      index: z.number().optional().describe('目标标签页索引（0 为第一个，-1 为最后一个）'),
      closeCurrent: z.boolean().optional().default(false).describe('切换前是否关闭当前标签页'),
    }),
    execute: async ({ handle, index, closeCurrent }) => {
      try {
        const sid = await ensureSession()
        const handles: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000)

        let targetHandle: string
        if (handle) {
          if (!handles.includes(handle)) return { ok: false, error: `Handle "${handle}" not found` }
          targetHandle = handle
        } else if (index !== undefined) {
          const idx = index < 0 ? handles.length + index : index
          if (idx < 0 || idx >= handles.length) return { ok: false, error: `Index ${index} out of range (${handles.length} tabs)` }
          targetHandle = handles[idx]
        } else {
          return { ok: false, error: 'Must provide handle or index' }
        }

        if (closeCurrent) {
          await wdFetch(`/session/${sid}/window`, { method: 'DELETE' }, 5000)
        }

        await wdFetch(`/session/${sid}/window`, { method: 'POST', body: JSON.stringify({ handle: targetHandle }) }, 5000)
        const page = await quickObserve(sid)
        return { ok: true, switchedTo: targetHandle, currentPage: page }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_observe: tool({
    description: '观察当前页面：获取 URL、标题、可见文本、所有可见的交互元素（含 shadow DOM 和同源 iframe）及其坐标。用这个来"看"页面。',
    inputSchema: z.object({}),
    execute: async () => {
      try {
        const sid = await ensureSession()
        const result = await wdFetch(`/session/${sid}/execute/sync`, {
          method: 'POST',
          body: JSON.stringify({ script: OBSERVE_SCRIPT, args: [] }),
        })
        return result
      } catch (e: any) {
        return { error: e.message }
      }
    },
  }),

  browser_click: tool({
    description: '在远程浏览器页面上点击指定坐标（可从 browser_observe 获取元素坐标）。如果点击导致新标签页打开，会在返回值中提示，你需要用 browser_list_tabs + browser_switch_tab 来决定是否切换。',
    inputSchema: z.object({
      x: z.number().describe('点击的 X 坐标'),
      y: z.number().describe('点击的 Y 坐标'),
    }),
    execute: async ({ x, y }) => {
      try {
        const sid = await ensureSession()
        const handlesBefore: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{
              type: 'pointer', id: 'mouse',
              parameters: { pointerType: 'mouse' },
              actions: humanClickActions(x, y),
            }],
          }),
        })
        await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })
        await new Promise(r => setTimeout(r, 800))
        const handlesAfter: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        const newTabOpened = handlesAfter.length > handlesBefore.length
        if (newTabOpened) log(`Click opened new tab (now ${handlesAfter.length} tabs)`)
        const page = await quickObserve(sid)
        return { ok: true, clickedAt: { x, y }, newTabOpened, tabCount: handlesAfter.length, currentPage: page }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_click_element: tool({
    description: '通过 CSS 选择器查找并点击元素，支持 shadow DOM 和同源 iframe。如果点击导致新标签页打开，会在返回值中提示。',
    inputSchema: z.object({
      selector: z.string().describe('CSS 选择器，如 "#search-btn", "input[name=q]", "a.nav-link"'),
    }),
    execute: async ({ selector }) => {
      try {
        const sid = await ensureSession()
        const handlesBefore: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        const clickResult = await wdFetch(`/session/${sid}/execute/sync`, {
          method: 'POST',
          body: JSON.stringify({ script: CLICK_ELEMENT_SCRIPT, args: [selector] }),
        })
        if (!clickResult?.found) {
          throw new Error(`Element "${selector}" not found`)
        }
        await new Promise(r => setTimeout(r, 800))
        const handlesAfter: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        const newTabOpened = handlesAfter.length > handlesBefore.length
        if (newTabOpened) log(`Click opened new tab (now ${handlesAfter.length} tabs)`)
        const page = await quickObserve(sid)
        return { ok: true, selector, clicked: clickResult, newTabOpened, tabCount: handlesAfter.length, currentPage: page }
      } catch (e: any) {
        return { ok: false, error: e.message, hint: `Element "${selector}" not found or not clickable` }
      }
    },
  }),

  browser_type: tool({
    description: '在远程浏览器中输入文本（在当前聚焦的输入框中）',
    inputSchema: z.object({
      text: z.string().describe('要输入的文本'),
    }),
    execute: async ({ text }) => {
      try {
        const sid = await ensureSession()
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{ type: 'key', id: 'keyboard', actions: humanKeyActions(text) }],
          }),
        })
        await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })
        const page = await quickObserve(sid)
        return { ok: true, typed: text, currentPage: page }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_key: tool({
    description: '在远程浏览器中按下键盘按键，如 Enter、Tab、Escape、Backspace 等',
    inputSchema: z.object({
      key: z.string().describe('按键名称，如 Enter、Tab、Escape'),
    }),
    execute: async ({ key }) => {
      try {
        const sid = await ensureSession()
        const keyValue = KEY_MAP[key] || key
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{
              type: 'key', id: 'keyboard',
              actions: [
                { type: 'keyDown', value: keyValue },
                { type: 'keyUp', value: keyValue },
              ],
            }],
          }),
        })
        await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })
        await new Promise(r => setTimeout(r, 500))
        const page = await quickObserve(sid)
        return { ok: true, key, currentPage: page }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_scroll: tool({
    description: '在远程浏览器页面上滚动',
    inputSchema: z.object({
      x: z.number().optional().default(640).describe('滚动起始 X 坐标'),
      y: z.number().optional().default(360).describe('滚动起始 Y 坐标'),
      deltaX: z.number().optional().default(0).describe('水平滚动量'),
      deltaY: z.number().describe('垂直滚动量（正值向下）'),
    }),
    execute: async ({ x, y, deltaX, deltaY }) => {
      try {
        const sid = await ensureSession()
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{
              type: 'wheel', id: 'wheel',
              actions: [{
                type: 'scroll', x, y, deltaX, deltaY,
                duration: 100, origin: 'viewport',
              }],
            }],
          }),
        })
        await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })
        return { ok: true, deltaX, deltaY }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_get_page_info: tool({
    description: '获取远程浏览器当前页面的 URL 和标题',
    inputSchema: z.object({}),
    execute: async () => {
      try {
        const sid = await ensureSession()
        const url = await wdFetch(`/session/${sid}/url`)
        const title = await wdFetch(`/session/${sid}/title`)
        return { url, title }
      } catch (e: any) {
        return { error: e.message }
      }
    },
  }),
}

// ---------------------------------------------------------------------------
// Docker tools
// ---------------------------------------------------------------------------

const dockerTools = {
  docker_status: tool({
    description: '查询所有 Docker 容器的运行状态',
    inputSchema: z.object({}),
    execute: async () => {
      try {
        return { statuses: await getStatuses() }
      } catch (e: any) {
        return { error: e.message }
      }
    },
  }),

  docker_start: tool({
    description: '启动指定方案的 Docker 服务。可用 id: selenium',
    inputSchema: z.object({
      solutionId: z.string().describe('方案 ID'),
    }),
    execute: async ({ solutionId }) => {
      const services = SERVICE_MAP[solutionId]
      if (!services) return { ok: false, error: `未知方案: ${solutionId}` }
      try {
        log(`start [${solutionId}]: ${services.join(', ')}`)
        await dockerCompose(`up -d --build ${services.join(' ')}`, 300_000, activeRequestSignal || undefined)
        return { ok: true, solutionId, services }
      } catch (e: any) {
        return { ok: false, error: e.message?.slice(0, 300) }
      }
    },
  }),

  docker_stop: tool({
    description: '停止指定方案的 Docker 服务',
    inputSchema: z.object({
      solutionId: z.string().describe('方案 ID'),
    }),
    execute: async ({ solutionId }) => {
      const services = SERVICE_MAP[solutionId]
      if (!services) return { ok: false, error: `未知方案: ${solutionId}` }
      try {
        log(`stop [${solutionId}]: ${services.join(', ')}`)
        await dockerCompose(`stop ${services.join(' ')}`, 60_000, activeRequestSignal || undefined)
        return { ok: true, solutionId, services }
      } catch (e: any) {
        return { ok: false, error: e.message?.slice(0, 300) }
      }
    },
  }),
}

const allTools = { ...browserTools, ...dockerTools }

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', (chunk: Buffer) => { body += chunk.toString() })
    req.on('end', () => resolve(body))
    req.on('error', reject)
  })
}

function jsonError(res: ServerResponse, status: number, error: string) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify({ error }))
}

function sseWrite(res: ServerResponse, data: unknown) {
  res.write(`data: ${JSON.stringify(data)}\n\n`)
}

// ---------------------------------------------------------------------------
// Provider factory
// ---------------------------------------------------------------------------

function createModel(baseUrl: string, apiKey: string, modelName: string, apiType: string) {
  const base = baseUrl.replace(/\/+$/, '')
  const isAnthropic = apiType === 'anthropic'

  log(`createModel: base=${base}, model=${modelName}, apiType=${apiType}, isAnthropic=${isAnthropic}`)

  if (isAnthropic) {
    const anthropicBase = base.endsWith('/v1') ? base : `${base}/v1`
    log(`Anthropic baseURL adjusted: ${base} -> ${anthropicBase}`)
    const provider = createAnthropic({ baseURL: anthropicBase, apiKey })
    return provider(modelName || 'claude-sonnet-4-20250514')
  }

  const provider = createOpenAI({ baseURL: base, apiKey })
  return provider.chat(modelName || 'gpt-4o-mini')
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

const ANTHROPIC_PRESET_MODELS = [
  'claude-sonnet-4-20250514',
  'claude-3-5-haiku-20241022',
]

export function aiChatPlugin(): Plugin {
  return {
    name: 'ai-chat',
    configureServer(server) {

      // ---- /api/ai/models ----
      server.middlewares.use(async (req, res, next) => {
        if (req.url !== '/api/ai/models' || req.method !== 'POST') return next()
        try {
          const body = JSON.parse(await readBody(req))
          const { baseUrl, apiKey, apiType } = body
          if (!apiKey || !baseUrl) {
            res.statusCode = 200
            res.setHeader('Content-Type', 'application/json')
            return res.end(JSON.stringify({ models: [] }))
          }
          if (apiType === 'anthropic') {
            res.statusCode = 200
            res.setHeader('Content-Type', 'application/json')
            return res.end(JSON.stringify({ models: ANTHROPIC_PRESET_MODELS }))
          }
          const base = baseUrl.replace(/\/+$/, '')
          const upstream = await fetch(`${base}/models`, {
            headers: { Authorization: `Bearer ${apiKey}` },
            signal: AbortSignal.timeout(8000),
          })
          if (!upstream.ok) {
            res.statusCode = 200
            res.setHeader('Content-Type', 'application/json')
            return res.end(JSON.stringify({ models: [], error: `upstream ${upstream.status}` }))
          }
          const json = await upstream.json() as { data?: { id: string }[] }
          const ids = (json.data || []).map(m => m.id).sort()
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ models: ids }))
        } catch (e: any) {
          log(`/api/ai/models error: ${e.message}`)
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ models: [], error: e.message }))
        }
      })

      // ---- /api/ai/chat ----
      server.middlewares.use(async (req, res, next) => {
        if (req.url !== '/api/ai/chat' || req.method !== 'POST') return next()

        const requestAbort = new AbortController()
        const abortRequest = () => {
          if (!requestAbort.signal.aborted) requestAbort.abort()
        }
        res.on('close', abortRequest)
        activeRequestSignal = requestAbort.signal

        try {
          const body = JSON.parse(await readBody(req))
          const { messages, apiKey, baseUrl, model: modelName, apiType } = body

          if (!apiKey) return jsonError(res, 400, '请先配置 API Key')
          if (!messages?.length) return jsonError(res, 400, '消息不能为空')

          const llm = createModel(
            baseUrl || 'https://api.openai.com/v1',
            apiKey,
            modelName || 'gpt-4o-mini',
            apiType || 'openai',
          )

          log(`ReAct agent start, model=${modelName}, provider=${apiType}`)

          const coreMessages: ModelMessage[] = messages
            .filter((m: any) => m.role === 'user' || m.role === 'assistant')
            .map((m: any) => ({
              role: m.role as 'user' | 'assistant',
              content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
            }))

          const result = streamText({
            model: llm,
            system: SYSTEM_PROMPT,
            messages: coreMessages,
            tools: allTools,
            stopWhen: stepCountIs(15),
            abortSignal: requestAbort.signal,
            timeout: { stepMs: 30_000 },
          })

          res.statusCode = 200
          res.setHeader('Content-Type', 'text/event-stream')
          res.setHeader('Cache-Control', 'no-cache')
          res.setHeader('Connection', 'keep-alive')

          for await (const part of result.fullStream) {
            switch (part.type) {
              case 'start':
                break
              case 'abort':
                log(`aborted: ${part.reason ?? 'timeout or client disconnect'}`)
                try { sseWrite(res, { type: 'error', message: part.reason ?? '请求超时或已取消' }) } catch {}
                break
              case 'text-delta':
                sseWrite(res, { type: 'text', content: part.text })
                break
              case 'tool-call':
                log(`tool-call: ${part.toolName}(${JSON.stringify(part.input).slice(0, 100)})`)
                sseWrite(res, {
                  type: 'tool_call',
                  id: part.toolCallId,
                  name: part.toolName,
                  args: part.input,
                })
                break
              case 'tool-result': {
                const output = part.output as Record<string, any>
                const ok = output?.ok !== false && !output?.error
                log(`tool-result: ${part.toolName} -> ${ok ? 'ok' : 'FAIL: ' + (output?.error || 'unknown')}`)
                let resultForFrontend: Record<string, any>
                if (part.toolName === 'browser_observe') {
                  resultForFrontend = { ok, url: output?.url, title: output?.title, elementCount: output?.elements?.length }
                } else if (output?.currentPage) {
                  resultForFrontend = {
                    ...output,
                    currentPage: {
                      url: output.currentPage.url,
                      title: output.currentPage.title,
                      elementCount: output.currentPage.elementCount,
                    },
                  }
                } else {
                  resultForFrontend = output
                }
                const screenshot = part.toolName?.startsWith('browser_') && sessionId
                  ? await captureScreenshot(sessionId)
                  : null
                sseWrite(res, {
                  type: 'tool_result',
                  id: part.toolCallId,
                  name: part.toolName,
                  result: resultForFrontend,
                  screenshot,
                })
                break
              }
              case 'error':
                log(`error: ${part.error}`)
                sseWrite(res, { type: 'error', message: String(part.error) })
                break
              case 'finish':
                sseWrite(res, { type: 'done' })
                break
            }
          }

          res.end()
        } catch (e: any) {
          if (requestAbort.signal.aborted) {
            log(`stream aborted: ${e.message?.slice(0, 200)}`)
            return
          }
          log(`FATAL: ${e.message}`)
          if (!res.headersSent) {
            jsonError(res, 500, e.message || 'Internal error')
          } else {
            try { sseWrite(res, { type: 'error', message: e.message }); res.end() } catch {}
          }
        } finally {
          res.off('close', abortRequest)
          if (activeRequestSignal === requestAbort.signal) {
            activeRequestSignal = null
          }
        }
      })
    },
  }
}
