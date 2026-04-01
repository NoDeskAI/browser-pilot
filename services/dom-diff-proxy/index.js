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

function withTimeout(promise, ms) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`CDP timeout ${ms}ms`)), ms); }),
  ]).finally(() => clearTimeout(timer));
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
    let navigating = false;
    let queue = Promise.resolve();
    let pendingMove = null;
    let moveTimer = null;
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

    function cleanup() {
      closed = true;
      if (moveTimer) clearInterval(moveTimer);
    }

    try {
      page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 720 });

      page.on('dialog', async (dialog) => {
        log('Dialog: %s "%s" → accept', dialog.type(), dialog.message().slice(0, 80));
        await dialog.accept().catch(() => {});
      });

      page.on('popup', async (popup) => {
        const url = popup.url();
        log('Popup → redirect main page to %s', url.slice(0, 100));
        await popup.close().catch(() => {});
        if (url && url !== 'about:blank') {
          queue = queue.then(async () => {
            if (closed) return;
            navigating = true;
            try {
              await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
            } catch (e) {
              log('Popup redirect error:', e.message);
            }
          });
        }
      });

      page.on('framenavigated', (frame) => {
        if (frame === page.mainFrame()) {
          navigating = true;
        }
      });

      await page.exposeFunction('__domDiffEmit', (eventJson) => {
        send(eventJson);
      });

      async function injectRecorder() {
        try {
          await page.evaluate(RECORDER_SCRIPT);
          navigating = false;
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

      async function flushPendingMove() {
        if (!pendingMove || closed || navigating) return;
        const move = pendingMove;
        pendingMove = null;
        await withTimeout(page.mouse.move(move.x, move.y), 2000).catch(() => {});
      }

      moveTimer = setInterval(() => {
        if (pendingMove && !closed && !navigating) {
          queue = queue.then(() => flushPendingMove());
        }
      }, 100);

      ws.on('message', (raw) => {
        if (closed) return;
        let msg;
        try { msg = JSON.parse(raw.toString()); } catch { return; }

        if (msg.type === 'mousemove') {
          pendingMove = msg;
          return;
        }

        queue = queue.then(async () => {
          if (closed) return;

          if (navigating && !['navigate', 'resize'].includes(msg.type)) {
            return;
          }

          await flushPendingMove();

          try {
            switch (msg.type) {
              case 'navigate':
                log('Navigate ->', msg.url);
                navigating = true;
                await page.goto(msg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
                break;

              case 'click':
                log('click (%d, %d) btn=%d', msg.x, msg.y, msg.button);
                await withTimeout(page.mouse.click(msg.x, msg.y, {
                  button: msg.button === 2 ? 'right' : 'left',
                }), 5000);
                break;

              case 'mousedown':
                await withTimeout(page.mouse.move(msg.x, msg.y), 2000);
                await withTimeout(page.mouse.down({ button: msg.button === 2 ? 'right' : 'left' }), 2000);
                break;

              case 'mouseup':
                await withTimeout(page.mouse.move(msg.x, msg.y), 2000);
                await withTimeout(page.mouse.up({ button: msg.button === 2 ? 'right' : 'left' }), 2000);
                break;

              case 'wheel':
                await withTimeout(page.mouse.wheel({ deltaX: msg.dx || 0, deltaY: msg.dy || 0 }), 2000);
                break;

              case 'keydown': {
                log('keydown key=%s', msg.key);
                const k = msg.key;
                if (['Shift', 'Control', 'Alt', 'Meta'].includes(k)) {
                  await withTimeout(page.keyboard.down(k), 2000);
                } else {
                  await withTimeout(page.keyboard.press(k), 2000);
                }
                break;
              }

              case 'keyup': {
                const k = msg.key;
                if (['Shift', 'Control', 'Alt', 'Meta'].includes(k)) {
                  await withTimeout(page.keyboard.up(k), 2000);
                }
                break;
              }

              case 'resize':
                log('resize %dx%d', msg.width, msg.height);
                if (msg.width > 0 && msg.height > 0) {
                  await withTimeout(page.setViewport({
                    width: Math.round(msg.width),
                    height: Math.round(msg.height),
                  }), 5000);
                }
                break;
            }
          } catch (e) {
            log('Error [%s]: %s', msg.type, e.message);
          }
        });
      });

      ws.on('close', async () => {
        cleanup();
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
      cleanup();
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
