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

async function wdFetch(urlPath: string, init?: RequestInit, timeoutMs = 30_000): Promise<any> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const resp = await fetch(`${SELENIUM_BASE}${urlPath}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
      signal: controller.signal,
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
            'goog:chromeOptions': { args: ['--no-sandbox', '--disable-dev-shm-usage'] },
          },
        },
      }),
      signal: controller.signal,
    })
    const data: any = await resp.json()
    sessionId = data.value.sessionId
    log(`WebDriver session created: ${sessionId}`)
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
  var sel = 'a, button, input, textarea, select, [role="button"], [onclick], [tabindex]';
  var els = document.querySelectorAll(sel);
  for (var i = 0; i < Math.min(els.length, 80); i++) {
    var el = els[i];
    var rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) continue;
    var tag = el.tagName.toLowerCase();
    var text = (el.textContent || '').trim().substring(0, 60);
    var attrs = {};
    if (el.id) attrs.id = el.id;
    if (el.name) attrs.name = el.name;
    if (el.type) attrs.type = el.type;
    if (el.placeholder) attrs.placeholder = el.placeholder;
    if (el.href) attrs.href = el.href.substring(0, 100);
    if (el.ariaLabel) attrs.ariaLabel = el.ariaLabel;
    result.elements.push({
      index: i,
      tag: tag,
      text: text,
      attrs: attrs,
      x: Math.round(rect.x + rect.width / 2),
      y: Math.round(rect.y + rect.height / 2)
    });
  }
  var bodyText = document.body ? document.body.innerText : '';
  result.visibleText = bodyText.substring(0, 2000);
  return result;
})();
`

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
    description: '在远程浏览器中导航到指定 URL',
    inputSchema: z.object({
      url: z.string().describe('要访问的完整 URL'),
    }),
    execute: async ({ url }) => {
      try {
        const sid = await ensureSession()
        await switchToLatestTab(sid)
        await closeOtherTabs(sid)
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

  browser_observe: tool({
    description: '观察当前页面：获取 URL、标题、可见文本、所有可交互元素（链接/按钮/输入框等）及其坐标。用这个来"看"页面。',
    inputSchema: z.object({}),
    execute: async () => {
      try {
        const sid = await ensureSession()
        await switchToLatestTab(sid)
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
    description: '在远程浏览器页面上点击指定坐标（可从 browser_observe 获取元素坐标）',
    inputSchema: z.object({
      x: z.number().describe('点击的 X 坐标'),
      y: z.number().describe('点击的 Y 坐标'),
    }),
    execute: async ({ x, y }) => {
      try {
        const sid = await ensureSession()
        await switchToLatestTab(sid)
        const handlesBefore: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{
              type: 'pointer', id: 'mouse',
              parameters: { pointerType: 'mouse' },
              actions: [
                { type: 'pointerMove', duration: 0, x, y, origin: 'viewport' },
                { type: 'pointerDown', button: 0 },
                { type: 'pointerUp', button: 0 },
              ],
            }],
          }),
        })
        await wdFetch(`/session/${sid}/actions`, { method: 'DELETE' })
        await new Promise(r => setTimeout(r, 800))
        const handlesAfter: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        if (handlesAfter.length > handlesBefore.length) {
          log(`Click opened new tab, switching to it`)
          await switchToLatestTab(sid)
        }
        const page = await quickObserve(sid)
        return { ok: true, clickedAt: { x, y }, currentPage: page }
      } catch (e: any) {
        return { ok: false, error: e.message }
      }
    },
  }),

  browser_click_element: tool({
    description: '通过 CSS 选择器查找并点击元素（比坐标更精确）',
    inputSchema: z.object({
      selector: z.string().describe('CSS 选择器，如 "#search-btn", "input[name=q]", "a.nav-link"'),
    }),
    execute: async ({ selector }) => {
      try {
        const sid = await ensureSession()
        await switchToLatestTab(sid)
        const handlesBefore: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        const element = await wdFetch(`/session/${sid}/element`, {
          method: 'POST',
          body: JSON.stringify({ using: 'css selector', value: selector }),
        })
        const elementId = element.ELEMENT || element[Object.keys(element)[0]]
        await wdFetch(`/session/${sid}/element/${elementId}/click`, { method: 'POST', body: '{}' })
        await new Promise(r => setTimeout(r, 800))
        const handlesAfter: string[] = await wdFetch(`/session/${sid}/window/handles`, undefined, 5000) || []
        if (handlesAfter.length > handlesBefore.length) {
          log(`Click opened new tab, switching to it`)
          await switchToLatestTab(sid)
        }
        const page = await quickObserve(sid)
        return { ok: true, selector, currentPage: page }
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
        const keyActions: any[] = []
        for (const ch of text) {
          keyActions.push({ type: 'keyDown', value: ch })
          keyActions.push({ type: 'keyUp', value: ch })
        }
        await wdFetch(`/session/${sid}/actions`, {
          method: 'POST',
          body: JSON.stringify({
            actions: [{ type: 'key', id: 'keyboard', actions: keyActions }],
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
        await dockerCompose(`up -d --build ${services.join(' ')}`, 300_000)
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
        await dockerCompose(`stop ${services.join(' ')}`, 60_000)
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

export function aiChatPlugin(): Plugin {
  return {
    name: 'ai-chat',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (req.url !== '/api/ai/chat' || req.method !== 'POST') return next()

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
          })

          res.statusCode = 200
          res.setHeader('Content-Type', 'text/event-stream')
          res.setHeader('Cache-Control', 'no-cache')
          res.setHeader('Connection', 'keep-alive')

          for await (const part of result.fullStream) {
            switch (part.type) {
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
                  resultForFrontend = { ...output, currentPage: { url: output.currentPage.url, title: output.currentPage.title } }
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
              case 'finish':
                sseWrite(res, { type: 'done' })
                break
            }
          }

          res.end()
        } catch (e: any) {
          log(`FATAL: ${e.message}`)
          if (!res.headersSent) {
            jsonError(res, 500, e.message || 'Internal error')
          } else {
            try { sseWrite(res, { type: 'error', message: e.message }); res.end() } catch {}
          }
        }
      })
    },
  }
}
