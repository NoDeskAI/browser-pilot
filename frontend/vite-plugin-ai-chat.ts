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
                '--test-type',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--window-size=1366,768',
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
        body: JSON.stringify({ width: 1366, height: 768 }),
      }, 5000)
    } catch {}

    // Inject stealth anti-detection scripts
    await injectStealth(sessionId!)
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

async function detectAndSwitchNewTab(
  sid: string,
  handlesBefore: string[],
  timeoutMs = 3000,
): Promise<{ newTabOpened: boolean; autoSwitched: boolean; tabCount: number; switchedTo?: string }> {
  const start = Date.now()
  let lastHandles = handlesBefore

  while (Date.now() - start < timeoutMs) {
    const handlesAfter: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
    lastHandles = handlesAfter

    const opened = handlesAfter.filter(h => !handlesBefore.includes(h))
    if (opened.length > 0) {
      const target = opened[opened.length - 1]
      const current = await wdFetch(`/session/${sid}/window`, undefined, 5000)
      if (current !== target) {
        log(`Auto-switching to newly opened tab ${target} (${handlesAfter.length} tabs)`)
        await wdFetch(`/session/${sid}/window`, {
          method: 'POST',
          body: JSON.stringify({ handle: target }),
        }, 5000)
      }
      return { newTabOpened: true, autoSwitched: true, tabCount: handlesAfter.length, switchedTo: target }
    }

    await new Promise(r => setTimeout(r, 200))
  }

  return {
    newTabOpened: lastHandles.length > handlesBefore.length,
    autoSwitched: false,
    tabCount: lastHandles.length,
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

const SYSTEM_PROMPT = `你是 NoDeskPane 的 AI 助手，通过调用工具操控远程浏览器。用户正在 noVNC 中实时观看你的操作。

## 第一优先级规则 — 每次回复都必须调用工具

**你的每次回复都必须包含至少一个工具调用。** 这是最重要的规则。

- 如果不确定该做什么 → 调用 browser_observe 观察页面
- 如果用户说"继续" → 调用 browser_observe 先看当前状态，然后执行下一步
- 如果用户说"完成验证/已登录/验证完成" → 不需要 observe，直接执行任务的下一步操作（如搜索、导航等）
- 如果上一步失败了 → 调用 browser_observe 重新评估，然后用另一种方式重试
- **绝对不允许只输出文字而不调用工具。** 如果你发现自己在写一大段文字描述操作结果（如"搜索到了XXX"、"已点击点赞"），但没有调用工具——你在产生幻觉。立即停止，调用 browser_observe。

### 禁止事项
- 禁止编造操作结果（如"点赞14.8万"）——所有数据必须来自 browser_observe 的返回
- 禁止用文字描述你"将要做"或"已经做了"的操作，然后不调用工具
- 禁止把工具调用写成 [TOOL_CALL]、JSON 或其他文本格式——必须通过 tool calling 机制
- 禁止在用户登录以后再进行账号的校验，只需要继续完成当前工作即可
- 禁止在回复中复述 observe 返回的页面内容（如列举所有元素、文本、URL 等）。observe 的结果是给你决策用的，不需要展示给用户。只需简短说明你看到了什么，然后立刻执行下一步操作

### 操作流程
1. docker_status 确认 Selenium 运行中
2. browser_observe 获取当前页面
3. 执行操作（navigate/click/type）
4. browser_observe 确认操作结果
5. 基于 observe 返回的**真实数据**回复用户，继续下一步

### 点击优先级
1. **browser_click_text** — 首选！B站等现代网站的按钮是 div/span，CSS 选择器找不到，但文字/aria-label 匹配总能找到
2. **browser_click_element** — CSS 选择器，适合 input 等标准元素
3. **browser_click** — 坐标点击，最后备选，容易失准

### 输入文本
- 先点击输入框，再 browser_type 输入
- 已有内容需清空：先 browser_key Ctrl+A，再输入

### 登录流程
1. 导航到网站 → browser_observe
2. browser_click_text "登录" 打开登录框
3. 点击账号输入框 → browser_type 输入账号
4. 点击密码输入框 → browser_type 输入密码
5. browser_click_text "登录" 提交
6. browser_observe 确认结果
7. 如出现验证码 → 见下方验证码处理

### 验证码处理 — 全部交给用户
遇到任何类型的验证码时（图片点击、滑块、短信验证码、图形验证码等），**立即停止操作**，按以下步骤处理：
1. 调用 browser_observe 确认验证码类型
2. 用一句话告诉用户验证码类型，请用户手动完成
3. **等待用户回复确认已完成**
4. 收到用户确认后，直接继续执行下一步操作（不需要再 observe 检查验证结果）

**注意：**
- 不要尝试用坐标点击验证码图片
- 不要尝试自动识别或输入验证码
- 短信验证码同样等用户回复验证码数字后再操作
- 用户提供验证码后，你必须执行完整流程：browser_click_element 点击输入框 → browser_type 输入验证码 → browser_click_text 点击确认/登录按钮。不要只输入不点击确认。

### 多步任务
用户给出多步指令时（如"登录后搜索XX并三连"），逐步执行。每步：操作 → observe 确认 → 下一步。不要跳步或编造。

### B站三连操作（强制规则）
当用户要求“三连”或“一键三连”时，你**必须且只能**调用 browser_triple_like() 工具。
操作流程：进入视频详情页 → browser_observe 确认在视频页 → 调用 browser_triple_like()
browser_triple_like 会自动完成：定位点赞按钮 → 长按3秒以上 → 检测“三连成功”字样
仅当 browser_triple_like 返回 ok:false 时，才可尝试 browser_click_text(“点赞”, holdMs=3200)
**禁止用 browser_click 猜坐标点赞/投币/收藏，禁止跳过 browser_triple_like**

用中文回答用户。`

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
    '[role="tab"]',
    '[role="search"]',
    '[contenteditable=""]',
    '[contenteditable="true"]',
    '[onclick]',
    '[tabindex]',
    '[class*="btn"]',
    '[class*="submit"]',
    '[class*="login"]',
    '[class*="search"]',
    '[class*="close"]',
    '[class*="confirm"]',
    '[class*="cancel"]'
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

  // Strip target="_blank" from all links so navigation stays in current tab
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
`

const CLICK_TEXT_SCRIPT = `
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
      if (exactMatch) {
        matched = text === searchText;
      } else {
        matched = text.indexOf(searchText) >= 0 || searchText.indexOf(text) >= 0;
      }
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

  // Strip target="_blank" so clicks navigate in current tab
  try { var blanks = document.querySelectorAll('a[target="_blank"]'); for (var b = 0; b < blanks.length; b++) blanks[b].removeAttribute('target'); } catch(e) {}

  walk(document, 0, 0, new Set());
  if (candidates.length === 0) return { found: false, searched: searchText };

  candidates.sort(function(a, b) { return a.score - b.score; });
  var best = candidates[0];
  var el = best.el;
  // Also strip target on the matched element and its parent links
  try { if (el.removeAttribute) el.removeAttribute('target'); var p = el.closest ? el.closest('a') : null; if (p) p.removeAttribute('target'); } catch(e) {}
  if (el.scrollIntoView) el.scrollIntoView({ block: 'center', inline: 'center' });
  if (el.focus) el.focus();
  if (arguments[2] !== false && el.click) el.click();
  var rect = el.getBoundingClientRect();
  return {
    found: true,
    tag: el.tagName.toLowerCase(),
    text: best.text.substring(0, 80),
    matchedIn: best.text === (el.getAttribute('aria-label')||'').trim() ? 'aria-label' : best.text === (el.getAttribute('data-title')||'').trim() ? 'data-title' : 'text',
    x: Math.round(best.ox + rect.left + rect.width / 2),
    y: Math.round(best.oy + rect.top + rect.height / 2),
    allMatches: candidates.length
  };
})(arguments[0], arguments[1], arguments[2]);
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

  // Strip target="_blank" so clicks navigate in current tab
  try { var blanks = document.querySelectorAll('a[target="_blank"]'); for (var b = 0; b < blanks.length; b++) blanks[b].removeAttribute('target'); } catch(e) {}

  var el = search(document);
  if (!el) return { found: false };
  try { if (el.removeAttribute) el.removeAttribute('target'); var p = el.closest ? el.closest('a') : null; if (p) p.removeAttribute('target'); } catch(e) {}
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

const TRIPLE_SUCCESS_CHECK_SCRIPT = `
return (function(keywords) {
  function collectText() {
    var parts = [];
    try {
      if (document.body && document.body.innerText) parts.push(document.body.innerText);
    } catch (e) {}
    try {
      var sels = [
        '.bili-toast',
        '[class*="toast"]',
        '[class*="message"]',
        '[class*="notice"]',
        '[class*="tip"]',
        '[role="alert"]'
      ];
      for (var s = 0; s < sels.length; s++) {
        var nodes = document.querySelectorAll(sels[s]);
        for (var i = 0; i < nodes.length; i++) {
          var t = (nodes[i].innerText || nodes[i].textContent || '').trim();
          if (t) parts.push(t);
        }
      }
    } catch (e) {}
    return parts.join('\\n').replace(/\\s+/g, ' ');
  }

  var text = collectText();
  for (var i = 0; i < keywords.length; i++) {
    if (text.indexOf(keywords[i]) >= 0) {
      return { matched: true, keyword: keywords[i], sample: text.slice(0, 220) };
    }
  }
  return { matched: false, keyword: '', sample: text.slice(0, 220) };
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

  // 10. Screen dimensions → 1366×768
  try{
    var sv={width:1366,height:768,availWidth:1366,availHeight:728,colorDepth:24,pixelDepth:24};
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
    description: '在远程浏览器页面上点击指定坐标（可从 browser_observe 获取元素坐标）。如果点击导致新标签页打开，会自动切换到新标签页。',
    inputSchema: z.object({
      x: z.number().describe('点击的 X 坐标'),
      y: z.number().describe('点击的 Y 坐标'),
      holdMs: z.number().optional().default(0).describe('按住时长（毫秒）。>0 时执行长按，适用于一键三连等场景'),
    }),
    execute: async ({ x, y, holdMs }) => {
      try {
        const sid = await ensureSession()
        const handlesBefore: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        const holdDuration = Math.max(0, Math.min(10_000, Math.floor(holdMs ?? 0)))
        const pointerActions = holdDuration > 0
          ? [
              { type: 'pointerMove', duration: 80, x, y, origin: 'viewport' },
              { type: 'pause', duration: 80 + Math.floor(Math.random() * 120) },
              { type: 'pointerDown', button: 0 },
              { type: 'pause', duration: holdDuration },
              { type: 'pointerUp', button: 0 },
            ]
          : humanClickActions(x, y)
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{
              type: 'pointer', id: 'mouse',
              parameters: { pointerType: 'mouse' },
              actions: pointerActions,
            }],
          }),
        })
        await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })
        await new Promise(r => setTimeout(r, 500))
        const switchInfo = await detectAndSwitchNewTab(sid, handlesBefore, 3200)
        if (switchInfo.autoSwitched) {
          await new Promise(r => setTimeout(r, 600))
        }
        const page = await quickObserve(sid)
        return {
          ok: true,
          clickedAt: { x, y },
          holdMs: holdDuration,
          newTabOpened: switchInfo.newTabOpened,
          autoSwitched: switchInfo.autoSwitched,
          switchedTo: switchInfo.switchedTo,
          tabCount: switchInfo.tabCount,
          currentPage: page,
        }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_click_text: tool({
    description: '通过可见文字内容查找并点击元素（也搜索 aria-label、data-title、title 属性）。适合点击"登录"、"搜索"、"点赞"等按钮。优先使用此工具来点击按钮和链接。',
    inputSchema: z.object({
      text: z.string().describe('要查找的元素可见文字或属性值，如"登录"、"搜索"、"点赞"、"投币"、"收藏"'),
      exact: z.boolean().optional().default(false).describe('是否精确匹配（默认模糊匹配）'),
      holdMs: z.number().optional().default(0).describe('按住时长（毫秒）。>0 时会定位文字元素后在其中心点长按'),
    }),
    execute: async ({ text, exact, holdMs }) => {
      try {
        const sid = await ensureSession()
        const handlesBefore: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        const holdDuration = Math.max(0, Math.min(10_000, Math.floor(holdMs ?? 0)))
        const clickResult = await wdFetch(`/session/${sid}/execute/sync`, {
          method: 'POST',
          body: JSON.stringify({ script: CLICK_TEXT_SCRIPT, args: [text, exact ?? false, holdDuration <= 0] }),
        })
        if (!clickResult?.found) {
          throw new Error(`No element with text "${text}" found on page`)
        }
        if (holdDuration > 0) {
          await wdFetch(`/session/${sid}/actions`, {
            method: 'POST',
            body: JSON.stringify({
              actions: [{
                type: 'pointer', id: 'mouse',
                parameters: { pointerType: 'mouse' },
                actions: [
                  { type: 'pointerMove', duration: 80, x: clickResult.x, y: clickResult.y, origin: 'viewport' },
                  { type: 'pause', duration: 80 + Math.floor(Math.random() * 120) },
                  { type: 'pointerDown', button: 0 },
                  { type: 'pause', duration: holdDuration },
                  { type: 'pointerUp', button: 0 },
                ],
              }],
            }),
          })
          await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })
        }
        await new Promise(r => setTimeout(r, 500))
        const switchInfo = await detectAndSwitchNewTab(sid, handlesBefore, 3200)
        if (switchInfo.autoSwitched) {
          await new Promise(r => setTimeout(r, 600))
        }
        const page = await quickObserve(sid)
        return {
          ok: true,
          text,
          holdMs: holdDuration,
          clicked: clickResult,
          newTabOpened: switchInfo.newTabOpened,
          autoSwitched: switchInfo.autoSwitched,
          switchedTo: switchInfo.switchedTo,
          tabCount: switchInfo.tabCount,
          currentPage: page,
        }
      } catch (e: any) {
        return { ok: false, error: e.message, hint: `No clickable element with text "${text}" found. Try browser_observe to see available elements.` }
      }
    },
  }),

  browser_triple_like: tool({
    description: '【一键三连专用工具】在B站视频页执行一键三连。自动完成：定位点赞按钮 → 长按3秒以上 → 检测三连成功。无需传参，直接调用即可。当用户要求三连时必须使用此工具。',
    inputSchema: z.object({
      holdMs: z.number().optional().default(3200).describe('长按时长毫秒，默认3200，一般不需要修改'),
      buttonText: z.string().optional().default('点赞').describe('点赞按钮文本，默认“点赞”，一般不需要修改'),
    }),
    execute: async ({ holdMs, buttonText }) => {
      try {
        const sid = await ensureSession()
        const pressMs = Math.max(3000, Math.min(10_000, Math.floor(holdMs ?? 3200)))
        const likeText = (buttonText || '点赞').trim() || '点赞'

        // 1) 找到点赞按钮（只定位，不直接点击）
        const findResult = await wdFetch(`/session/${sid}/execute/sync`, {
          method: 'POST',
          body: JSON.stringify({ script: CLICK_TEXT_SCRIPT, args: [likeText, false, false] }),
        })
        if (!findResult?.found) {
          return { ok: false, error: `未找到点赞按钮（text="${likeText}"）`, step: 1 }
        }

        // 2) 长按点赞按钮（超过 3s）
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{
              type: 'pointer', id: 'mouse',
              parameters: { pointerType: 'mouse' },
              actions: [
                { type: 'pointerMove', duration: 80, x: findResult.x, y: findResult.y, origin: 'viewport' },
                { type: 'pause', duration: 120 },
                { type: 'pointerDown', button: 0 },
                { type: 'pause', duration: pressMs },
                { type: 'pointerUp', button: 0 },
              ],
            }],
          }),
        })
        await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })

        // 3) 立刻检测“三连成功”字样（先即时检测，再短轮询）
        const successKeywords = ['三连成功', '一键三连成功', '已完成一键三连', '三连完成']
        let detect = await wdFetch(`/session/${sid}/execute/sync`, {
          method: 'POST',
          body: JSON.stringify({ script: TRIPLE_SUCCESS_CHECK_SCRIPT, args: [successKeywords] }),
        })

        const deadline = Date.now() + 2000
        while (!detect?.matched && Date.now() < deadline) {
          await new Promise(r => setTimeout(r, 200))
          detect = await wdFetch(`/session/${sid}/execute/sync`, {
            method: 'POST',
            body: JSON.stringify({ script: TRIPLE_SUCCESS_CHECK_SCRIPT, args: [successKeywords] }),
          })
        }

        const page = await quickObserve(sid)
        if (!detect?.matched) {
          return {
            ok: false,
            step: 3,
            error: '未检测到“三连成功”字样',
            checkedKeywords: successKeywords,
            detectedSample: detect?.sample || '',
            currentPage: page,
          }
        }

        // 4) 三连成功
        return {
          ok: true,
          step: 4,
          message: '点赞三连成功完成',
          matchedKeyword: detect.keyword,
          holdMs: pressMs,
          button: { text: likeText, x: findResult.x, y: findResult.y },
          currentPage: page,
        }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_click_element: tool({
    description: '通过 CSS 选择器查找并点击元素，支持 shadow DOM 和同源 iframe。如果点击导致新标签页打开，会自动切换到新标签页。',
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
        await new Promise(r => setTimeout(r, 500))
        const switchInfo = await detectAndSwitchNewTab(sid, handlesBefore, 3200)
        if (switchInfo.autoSwitched) {
          await new Promise(r => setTimeout(r, 600))
        }
        const page = await quickObserve(sid)
        return {
          ok: true,
          selector,
          clicked: clickResult,
          newTabOpened: switchInfo.newTabOpened,
          autoSwitched: switchInfo.autoSwitched,
          switchedTo: switchInfo.switchedTo,
          tabCount: switchInfo.tabCount,
          currentPage: page,
        }
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
    description: '在远程浏览器中按下键盘按键，如 Enter、Tab、Escape、Backspace 等。如果按键导致新标签页打开，会自动切换过去。',
    inputSchema: z.object({
      key: z.string().describe('按键名称，如 Enter、Tab、Escape'),
    }),
    execute: async ({ key }) => {
      try {
        const sid = await ensureSession()
        const handlesBefore: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
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
        await new Promise(r => setTimeout(r, 800))
        const switchInfo = await detectAndSwitchNewTab(sid, handlesBefore, 2500)
        if (switchInfo.autoSwitched) {
          await new Promise(r => setTimeout(r, 600))
        }
        const page = await quickObserve(sid)
        return {
          ok: true,
          key,
          newTabOpened: switchInfo.newTabOpened,
          autoSwitched: switchInfo.autoSwitched,
          tabCount: switchInfo.tabCount,
          currentPage: page,
        }
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
// Stray text-based tool call parser (fallback for models that don't use proper tool calling)
// ---------------------------------------------------------------------------

function parseStrayToolCall(text: string): { toolName: string; args: Record<string, any> } | null {
  const blockMatch = text.match(/\[TOOL_CALL\]([\s\S]*?)\[\/TOOL_CALL\]/)
  if (!blockMatch) return null

  const block = blockMatch[1]

  // 1) Try parsing JSON-like object
  const objMatch = block.match(/\{[\s\S]*\}/)
  if (objMatch) {
    try {
      let normalized = objMatch[0]
      normalized = normalized.replace(/'/g, '"')
      normalized = normalized.replace(/=>/g, ':')
      // Fix broken quotes like "parameters: {" → "parameters": {
      normalized = normalized.replace(/"(\w+)\s*:\s*\{/g, '"$1": {')
      normalized = normalized.replace(/([,{]\s*)([A-Za-z_]\w*)\s*:/g, '$1"$2":')
      const parsedObj = JSON.parse(normalized) as any
      // Accept "tool", "name", or "function" as the tool name field
      const toolName = parsedObj?.tool || parsedObj?.name || parsedObj?.function
      if (typeof toolName === 'string' && toolName in allTools) {
        let args: Record<string, any>
        // Accept "args", "parameters", "params", or "input" as the arguments field
        const argsField = parsedObj.args || parsedObj.parameters || parsedObj.params || parsedObj.input
        if (argsField && typeof argsField === 'object') {
          args = argsField
        } else {
          args = { ...parsedObj }
          delete args.tool; delete args.name; delete args.function
        }
        return { toolName, args }
      }
    } catch {}
  }

  // 2) Fallback regex: extract tool name from common patterns
  const toolMatch = block.match(/["']?(?:tool|name|function)["']?\s*(?:=>|:)\s*["']([^"']+)["']/)
  if (!toolMatch) return null
  const toolName = toolMatch[1]
  if (!(toolName in allTools)) return null

  const args: Record<string, any> = {}
  const argsSection = block.match(/["']?(?:args|parameters|params)["']?\s*(?:=>|:)\s*\{([\s\S]*?)\}/)
  if (argsSection) {
    const argsText = argsSection[1].trim()
    if (argsText) {
      const kvMatches = argsText.matchAll(/--(\w+)\s+(.+?)(?=\s+--|$)/g)
      for (const m of kvMatches) {
        const key = m[1]
        let val: any = m[2].trim()
        if (val === 'true') val = true
        else if (val === 'false') val = false
        else if (/^\d+$/.test(val)) val = parseInt(val)
        else if (/^\d+\.\d+$/.test(val)) val = parseFloat(val)
        args[key] = val
      }
      const jsonKvMatches = argsText.matchAll(/["']?(\w+)["']?\s*(?::|=>)\s*(?:"([^"]*)"|'([^']*)'|(true|false|\d+(?:\.\d+)?))/g)
      for (const m of jsonKvMatches) {
        const key = m[1]
        if (m[2] !== undefined) args[key] = m[2]
        else if (m[3] !== undefined) args[key] = m[3]
        else if (m[4] === 'true') args[key] = true
        else if (m[4] === 'false') args[key] = false
        else args[key] = Number(m[4])
      }
    }
  }

  return { toolName, args }
}


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

export function aiChatPlugin(): Plugin {
  return {
    name: 'ai-chat',
    configureServer(server) {
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

          const resolvedBaseUrl = baseUrl || 'https://api.openai.com/v1'
          const resolvedModel = modelName || 'gpt-4o-mini'
          const resolvedApiType = apiType || 'openai'

          const llm = createModel(resolvedBaseUrl, apiKey, resolvedModel, resolvedApiType)

          log(`ReAct agent start, model=${modelName}, provider=${apiType}`)

          const coreMessages: ModelMessage[] = messages
            .filter((m: any) => m.role === 'user' || m.role === 'assistant')
            .map((m: any) => ({
              role: m.role as 'user' | 'assistant',
              content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
            }))

          const firstUserMessage = messages.find((m: any) => m.role === 'user')
          const lastUserMessage = [...messages].reverse().find((m: any) => m.role === 'user')
          const originalTask = String(firstUserMessage?.content || '').slice(0, 2000)
          const latestUserRequest = String(lastUserMessage?.content || '').slice(0, 1500)

          res.statusCode = 200
          res.setHeader('Content-Type', 'text/event-stream')
          res.setHeader('Cache-Control', 'no-cache')
          res.setHeader('Connection', 'keep-alive')
          res.flushHeaders()

          const MAX_ATTEMPTS = 3
          let attempt = 0
          let runMessages = coreMessages

          while (attempt < MAX_ATTEMPTS) {
            attempt++
            if (requestAbort.signal.aborted) break

            const result = streamText({
              model: llm,
              system: SYSTEM_PROMPT,
              messages: runMessages,
              tools: allTools,
              stopWhen: stepCountIs(25),
              maxRetries: 2,
              abortSignal: requestAbort.signal,
            })

            let textAccumulator = ''
            let hadToolCalls = false
            let hadStrayToolCall = false
            let strayRetryMessages: { role: 'user' | 'assistant'; content: string }[] | null = null
            const recentActions: string[] = []

            for await (const part of result.fullStream) {
              switch (part.type) {
                case 'text-delta':
                  textAccumulator += part.text
                  sseWrite(res, { type: 'text', content: part.text })
                  break
                case 'tool-call':
                  hadToolCalls = true
                  recentActions.push(`tool-call: ${part.toolName}(${JSON.stringify(part.input).slice(0, 120)})`)
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
                  recentActions.push(`tool-result: ${part.toolName} -> ${ok ? 'ok' : 'FAIL'}${output?.error ? ` (${String(output.error).slice(0, 120)})` : ''}`)
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
                  sseWrite(res, {
                    type: 'tool_result',
                    id: part.toolCallId,
                    name: part.toolName,
                    result: resultForFrontend,
                  })

                  break
                }
                case 'error':
                  log(`error: ${part.error}`)
                  sseWrite(res, { type: 'error', message: String(part.error) })
                  break
                case 'finish': {
                  if (textAccumulator.includes('[TOOL_CALL]')) {
                    const parsed = parseStrayToolCall(textAccumulator)
                    if (parsed && parsed.toolName in allTools) {
                      hadStrayToolCall = true
                      log(`Auto-executing stray text tool call: ${parsed.toolName}(${JSON.stringify(parsed.args).slice(0, 100)})`)
                      sseWrite(res, { type: 'text', content: '\n\n🔧 检测到文本格式的工具调用，自动执行中...' })
                      const callId = `stray_${Date.now()}`
                      sseWrite(res, { type: 'tool_call', id: callId, name: parsed.toolName, args: parsed.args })
                      let strayResultSummary = ''
                      try {
                        const toolDef = allTools[parsed.toolName as keyof typeof allTools]
                        const output = await (toolDef as any).execute(parsed.args) as Record<string, any>
                        const ok = output?.ok !== false && !output?.error
                        log(`stray tool-result: ${parsed.toolName} -> ${ok ? 'ok' : 'FAIL'}`)
                        let resultForFrontend: Record<string, any>
                        if (parsed.toolName === 'browser_observe') {
                          resultForFrontend = { ok, url: output?.url, title: output?.title, elementCount: output?.elements?.length }
                          strayResultSummary = `[${parsed.toolName} 结果] url=${output?.url || ''}, title=${output?.title || ''}, elements=${output?.elements?.length || 0}`
                        } else if (output?.currentPage) {
                          resultForFrontend = { ...output, currentPage: { url: output.currentPage.url, title: output.currentPage.title, elementCount: output.currentPage.elementCount } }
                          strayResultSummary = `[${parsed.toolName} 结果] ${ok ? '成功' : '失败'}, page=${output.currentPage.url || ''}`
                        } else {
                          resultForFrontend = output
                          strayResultSummary = `[${parsed.toolName} 结果] ${JSON.stringify(resultForFrontend).slice(0, 300)}`
                        }
                        sseWrite(res, { type: 'tool_result', id: callId, name: parsed.toolName, result: resultForFrontend })
                      } catch (e: any) {
                        sseWrite(res, { type: 'tool_result', id: callId, name: parsed.toolName, result: { ok: false, error: e.message } })
                        strayResultSummary = `[${parsed.toolName} 结果] 失败: ${e.message}`
                      }
                      // Feed the stray tool result back to the model and retry so it can continue
                      if (attempt < MAX_ATTEMPTS) {
                        strayRetryMessages = [
                          { role: 'assistant' as const, content: textAccumulator },
                          { role: 'user' as const, content: `${strayResultSummary}\n\n工具已执行完成。请根据结果继续执行下一步操作。你必须通过 tool calling 调用工具。` },
                        ]
                      }
                    } else {
                      log(`WARNING: Model output [TOOL_CALL] but could not parse: ${textAccumulator.slice(0, 200)}`)
                      sseWrite(res, { type: 'text', content: '\n\n⚠️ 模型未正确调用工具。请重试，或换一个支持 tool calling 的模型（推荐 Claude / GPT-4o）。' })
                    }
                  }
                  textAccumulator = ''
                  break
                }
              }

            }

            // Stray tool call was executed — feed result back to model and continue
            if (strayRetryMessages) {
              log(`Stray tool call executed, feeding result back to model for continuation...`)
              sseWrite(res, { type: 'text', content: '\n\n🔄 继续执行中...\n' })
              runMessages = [...runMessages, ...strayRetryMessages]
              continue
            }

            // }

            if (hadToolCalls || hadStrayToolCall) {
              sseWrite(res, { type: 'done' })
              break
            }

            if (attempt < MAX_ATTEMPTS) {
              log(`WARNING: Agent finished without tool calls (attempt ${attempt}). Auto-retrying with forced prompt...`)
              sseWrite(res, { type: 'text', content: '\n\n🔄 Agent 未执行操作，自动重试中...\n' })
              const retryHint = textAccumulator.includes('验证码')
                ? '你刚才描述了验证码操作但没有调用工具。请立刻调用 browser_observe 观察当前页面，然后执行具体操作（如点击确认按钮）。'
                : '你刚才没有调用任何工具。请立刻调用 browser_observe 观察当前页面状态，然后根据结果执行下一步操作。'
              runMessages = [
                ...runMessages,
                { role: 'assistant' as const, content: textAccumulator || '(无内容)' },
                { role: 'user' as const, content: retryHint },
              ]
            } else {
              log(`WARNING: Agent still no tool calls after ${attempt} attempts`)
              sseWrite(res, { type: 'text', content: '\n\n💡 Agent 连续未执行操作。请尝试更明确的指令，或清除对话重新开始。' })
              sseWrite(res, { type: 'done' })
            }
          }

          res.end()
        } catch (e: any) {
          if (requestAbort.signal.aborted) return
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
