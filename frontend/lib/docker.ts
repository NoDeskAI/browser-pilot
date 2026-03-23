import { exec } from 'node:child_process'
import { promisify } from 'node:util'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const execAsync = promisify(exec)
const __dirname = path.dirname(fileURLToPath(import.meta.url))
export const PROJECT_ROOT = path.resolve(__dirname, '../..')

export const SERVICE_MAP: Record<string, string[]> = {
  selenium: ['selenium'],
  'cdp-screenshot': ['cdp-proxy'],
  mjpeg: ['mjpeg-stream'],
  'dom-diff': ['dom-diff-proxy'],
  rrweb: ['rrweb-proxy'],
}

export function log(tag: string, msg: string, ...args: unknown[]) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  console.log(`[${tag} ${ts}] ${msg}`, ...args)
}

export async function dockerCompose(args: string, timeout = 120_000, signal?: AbortSignal) {
  log('docker', `exec: docker compose ${args}`)
  const start = Date.now()
  try {
    const result = await execAsync(`docker compose ${args}`, {
      cwd: PROJECT_ROOT,
      timeout,
      signal,
    })
    log('docker', `done (${((Date.now() - start) / 1000).toFixed(1)}s): docker compose ${args.slice(0, 60)}`)
    return result
  } catch (e: any) {
    log('docker', `FAIL (${((Date.now() - start) / 1000).toFixed(1)}s): docker compose ${args.slice(0, 60)}`)
    log('docker', `  stderr: ${e.stderr?.slice(0, 300) || '(none)'}`)
    log('docker', `  message: ${e.message?.slice(0, 300) || '(none)'}`)
    throw e
  }
}

export async function getStatuses(): Promise<Record<string, string>> {
  const result: Record<string, string> = {}
  try {
    const { stdout } = await dockerCompose('ps -a --format json')
    for (const line of stdout.trim().split('\n')) {
      if (!line.trim()) continue
      try {
        const obj = JSON.parse(line)
        result[obj.Service] = obj.State
      } catch {
        try {
          const arr = JSON.parse(stdout)
          if (Array.isArray(arr)) {
            for (const obj of arr) result[obj.Service] = obj.State
          }
        } catch {}
      }
    }
  } catch {}
  return result
}
