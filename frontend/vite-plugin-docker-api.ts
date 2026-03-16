import { exec } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'
import type { IncomingMessage } from 'node:http'

const execAsync = promisify(exec)
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = path.resolve(__dirname, '..')

const SERVICE_MAP: Record<string, string[]> = {
  neko: ['neko'],
  novnc: ['novnc-chrome'],
  kasmvnc: ['kasmvnc'],
  browserless: ['browserless', 'browserless-proxy'],
  'cdp-diy': ['chrome-headless', 'cdp-proxy'],
  selenium: ['selenium'],
  mjpeg: ['mjpeg-stream'],
}

function log(msg: string, ...args: unknown[]) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  console.log(`[docker-api ${ts}] ${msg}`, ...args)
}

async function dockerCompose(args: string, timeout = 120_000) {
  log(`exec: docker compose ${args}`)
  const start = Date.now()
  try {
    const result = await execAsync(`docker compose ${args}`, {
      cwd: PROJECT_ROOT,
      timeout,
    })
    log(`done (${((Date.now() - start) / 1000).toFixed(1)}s): docker compose ${args.slice(0, 60)}`)
    return result
  } catch (e: any) {
    log(`FAIL (${((Date.now() - start) / 1000).toFixed(1)}s): docker compose ${args.slice(0, 60)}`)
    log(`  stderr: ${e.stderr?.slice(0, 300) || '(none)'}`)
    log(`  message: ${e.message?.slice(0, 300) || '(none)'}`)
    throw e
  }
}

async function getStatuses(): Promise<Record<string, string>> {
  const result: Record<string, string> = {}
  try {
    const { stdout } = await dockerCompose('ps -a --format json')
    for (const line of stdout.trim().split('\n')) {
      if (!line.trim()) continue
      try {
        const obj = JSON.parse(line)
        result[obj.Service] = obj.State
      } catch {
        // might be an array format on some Docker versions
        try {
          const arr = JSON.parse(stdout)
          if (Array.isArray(arr)) {
            for (const obj of arr) result[obj.Service] = obj.State
          }
        } catch {}
      }
    }
  } catch {
    // docker compose not ready or no containers
  }
  return result
}

function json(res: any, status: number, data: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(data))
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let body = ''
    req.on('data', (chunk: Buffer) => { body += chunk.toString() })
    req.on('end', () => resolve(body))
    req.on('error', reject)
  })
}

const HTTP_NAV_ENDPOINTS: Record<string, string> = {
  novnc: 'http://localhost:6081/navigate',
  browserless: 'http://localhost:3001/navigate',
  'cdp-diy': 'http://localhost:3100/navigate',
  mjpeg: 'http://localhost:3200/navigate',
}

const XDOTOOL_TARGETS: Record<string, { service: string; display: string; wmClass: string }> = {
  neko: { service: 'neko', display: ':99.0', wmClass: 'chromium' },
  kasmvnc: { service: 'kasmvnc', display: ':1', wmClass: 'chromium' },
  selenium: { service: 'selenium', display: ':99.0', wmClass: 'chromium' },
}

async function navigateInBrowser(solutionId: string, url: string): Promise<{ ok: boolean; url?: string; error?: string }> {
  log(`navigate: [${solutionId}] -> ${url}`)

  const httpEndpoint = HTTP_NAV_ENDPOINTS[solutionId]
  if (httpEndpoint) {
    log(`  via HTTP proxy -> ${httpEndpoint}`)
    const resp = await fetch(httpEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    })
    const data = (await resp.json()) as { ok: boolean; url?: string }
    log(`  result:`, data)
    return data
  }

  const target = XDOTOOL_TARGETS[solutionId]
  if (target) {
    log(`  via xdotool -> service=${target.service} DISPLAY=${target.display} class=${target.wmClass}`)
    const safeUrl = url.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\$/g, '\\$').replace(/`/g, '\\`')
    const cmd = [
      `export DISPLAY=${target.display}`,
      `WID=$(xdotool search --name " - " --class ${target.wmClass} 2>/dev/null | head -1)`,
      `[ -z "$WID" ] && WID=$(xdotool search --class ${target.wmClass} 2>/dev/null | tail -1)`,
      `if [ -z "$WID" ]; then echo "NO_WINDOW"; exit 1; fi`,
      `xdotool windowactivate $WID`,
      `sleep 0.2`,
      `xdotool key ctrl+l`,
      `sleep 0.3`,
      `xdotool type --clearmodifiers --delay 12 "${safeUrl}"`,
      `xdotool key Return`,
    ].join(' && ')
    try {
      await dockerCompose(`exec -T ${target.service} bash -c '${cmd}'`, 15_000)
      log(`  xdotool done`)
      return { ok: true, url }
    } catch (e: any) {
      if (e.stderr?.includes('NO_WINDOW') || e.message?.includes('NO_WINDOW')) {
        return { ok: false, error: '未找到浏览器窗口，请确认容器内浏览器已启动' }
      }
      throw e
    }
  }

  log(`  unknown solution: ${solutionId}`)
  return { ok: false, error: `未知方案: ${solutionId}` }
}

export function dockerApiPlugin(): Plugin {
  return {
    name: 'docker-api',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!req.url?.startsWith('/api/docker')) return next()

        if (req.method === 'OPTIONS') {
          res.statusCode = 204
          res.end()
          return
        }

        const url = req.url

        try {
          if (url === '/api/docker/status') {
            const statuses = await getStatuses()
            return json(res, 200, { statuses })
          }

          if (url === '/api/docker/start-all' && req.method === 'POST') {
            log('=> start-all')
            const { stdout, stderr } = await dockerCompose('up -d --build', 300_000)
            log('=> start-all output:', (stdout + stderr).slice(0, 300))
            return json(res, 200, { ok: true, output: stdout + stderr })
          }

          if (url === '/api/docker/stop-all' && req.method === 'POST') {
            log('=> stop-all')
            const { stdout, stderr } = await dockerCompose('stop', 60_000)
            log('=> stop-all output:', (stdout + stderr).slice(0, 300))
            return json(res, 200, { ok: true, output: stdout + stderr })
          }

          const startMatch = url.match(/^\/api\/docker\/start\/(.+)$/)
          if (startMatch && req.method === 'POST') {
            const id = decodeURIComponent(startMatch[1])
            const services = SERVICE_MAP[id]
            if (!services) return json(res, 404, { error: `Unknown solution: ${id}` })
            log(`=> start [${id}] services: ${services.join(', ')}`)
            const { stdout, stderr } = await dockerCompose(
              `up -d --build ${services.join(' ')}`,
              300_000,
            )
            log(`=> start [${id}] output:`, (stdout + stderr).slice(0, 300))
            return json(res, 200, { ok: true, output: stdout + stderr })
          }

          const stopMatch = url.match(/^\/api\/docker\/stop\/(.+)$/)
          if (stopMatch && req.method === 'POST') {
            const id = decodeURIComponent(stopMatch[1])
            const services = SERVICE_MAP[id]
            if (!services) return json(res, 404, { error: `Unknown solution: ${id}` })
            log(`=> stop [${id}] services: ${services.join(', ')}`)
            const { stdout, stderr } = await dockerCompose(
              `stop ${services.join(' ')}`,
              60_000,
            )
            log(`=> stop [${id}] output:`, (stdout + stderr).slice(0, 300))
            return json(res, 200, { ok: true, output: stdout + stderr })
          }

          if (url === '/api/docker/navigate' && req.method === 'POST') {
            const body = JSON.parse(await readBody(req))
            const result = await navigateInBrowser(body.solutionId, body.url)
            return json(res, result.ok ? 200 : 400, result)
          }

          json(res, 404, { error: 'Not found' })
        } catch (e: any) {
          log(`ERROR on ${url}: ${e.message?.slice(0, 300)}`)
          json(res, 500, { error: e.message?.slice(0, 500) || 'Unknown error' })
        }
      })
    },
  }
}
