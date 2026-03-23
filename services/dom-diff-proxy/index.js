import puppeteer from 'puppeteer-core';
import { WebSocketServer } from 'ws';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RECORDER_SCRIPT = readFileSync(join(__dirname, 'recorder.js'), 'utf-8');
const PORT = parseInt(process.env.PORT || '3300');
const CHROME_PATH = process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium';

function log(msg, ...args) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  console.log(`[dom-diff ${ts}] ${msg}`, ...args);
}

async function main() {
  log('Launching browser at', CHROME_PATH);
  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--disable-software-rasterizer',
      '--no-first-run',
      '--no-zygote',
    ],
  });
  log('Browser launched, pid:', browser.process()?.pid);

  const wss = new WebSocketServer({ port: PORT });
  log('WebSocket server listening on port', PORT);

  wss.on('connection', async (ws) => {
    log('Client connected');
    let page = null;
    let closed = false;
    const stats = { totalBytes: 0, snapshots: 0, snapshotBytes: 0, mutations: 0, mutationBytes: 0, startTime: Date.now() };

    function send(data) {
      if (!closed && ws.readyState === ws.OPEN) {
        const payload = typeof data === 'string' ? data : JSON.stringify(data);
        const bytes = Buffer.byteLength(payload, 'utf-8');
        stats.totalBytes += bytes;
        if (typeof data === 'string') {
          try {
            const parsed = JSON.parse(data);
            if (parsed.type === 'snapshot') { stats.snapshots++; stats.snapshotBytes += bytes; }
            else if (parsed.type === 'mutations') { stats.mutations++; stats.mutationBytes += bytes; }
          } catch {}
        }
        ws.send(payload);
      }
    }

    try {
      page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 720 });

      await page.exposeFunction('__domDiffEmit', (eventJson) => {
        send(eventJson);
      });

      async function injectRecorder() {
        try {
          await page.evaluate(RECORDER_SCRIPT);
          log('Recorder injected for', page.url());
        } catch (e) {
          log('Recorder injection failed:', e.message);
        }
      }

      page.on('domcontentloaded', async () => {
        send({ type: 'url', data: page.url() });
        await new Promise((r) => setTimeout(r, 100));
        await injectRecorder();
      });

      ws.on('message', async (raw) => {
        if (closed) return;
        let msg;
        try { msg = JSON.parse(raw.toString()); } catch { return; }

        try {
          switch (msg.type) {
            case 'navigate':
              log('Navigate ->', msg.url);
              await page.goto(msg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
              break;

            case 'mousedown':
              await page.mouse.move(msg.x, msg.y);
              await page.mouse.down({ button: msg.button === 2 ? 'right' : 'left' });
              break;

            case 'mouseup':
              await page.mouse.move(msg.x, msg.y);
              await page.mouse.up({ button: msg.button === 2 ? 'right' : 'left' });
              break;

            case 'mousemove':
              await page.mouse.move(msg.x, msg.y);
              break;

            case 'wheel':
              await page.mouse.wheel({ deltaX: msg.dx || 0, deltaY: msg.dy || 0 });
              break;

            case 'keydown': {
              const k = msg.key;
              if (['Shift', 'Control', 'Alt', 'Meta'].includes(k)) {
                await page.keyboard.down(k);
              } else {
                await page.keyboard.press(k);
              }
              break;
            }

            case 'keyup': {
              const k = msg.key;
              if (['Shift', 'Control', 'Alt', 'Meta'].includes(k)) {
                await page.keyboard.up(k);
              }
              break;
            }

            case 'resize':
              if (msg.width > 0 && msg.height > 0) {
                await page.setViewport({
                  width: Math.round(msg.width),
                  height: Math.round(msg.height),
                });
              }
              break;
          }
        } catch (e) {
          log('Error handling', msg.type + ':', e.message);
          send({ type: 'error', data: e.message });
        }
      });

      ws.on('close', async () => {
        closed = true;
        const elapsed = ((Date.now() - stats.startTime) / 1000).toFixed(1);
        const kb = (stats.totalBytes / 1024).toFixed(1);
        const snapKb = (stats.snapshotBytes / 1024).toFixed(1);
        const diffKb = (stats.mutationBytes / 1024).toFixed(1);
        log(`Client disconnected — ${elapsed}s, total: ${kb} KB (snap: ${snapKb} KB × ${stats.snapshots}, diff: ${diffKb} KB × ${stats.mutations} batches)`);
        if (page && !page.isClosed()) {
          await page.close().catch(() => {});
        }
      });

    } catch (e) {
      log('Setup error:', e.message);
      ws.close();
    }
  });

  process.on('SIGINT', async () => {
    log('Shutting down...');
    wss.close();
    await browser.close();
    process.exit(0);
  });
}

main().catch((e) => {
  log('Fatal:', e.message);
  process.exit(1);
});
