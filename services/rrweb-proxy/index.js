import puppeteer from 'puppeteer-core';
import { WebSocketServer } from 'ws';

const PORT = parseInt(process.env.PORT || '3400');
const CHROME_PATH = process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium';
const RRWEB_CDN = 'https://cdn.jsdelivr.net/npm/rrweb@2.0.0-alpha.13/dist/record/rrweb-record.min.js';

function log(msg, ...args) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  console.log(`[rrweb ${ts}] ${msg}`, ...args);
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
    const stats = { totalBytes: 0, events: 0, snapshots: 0, incrementals: 0, startTime: Date.now() };

    function send(data) {
      if (!closed && ws.readyState === ws.OPEN) {
        const payload = typeof data === 'string' ? data : JSON.stringify(data);
        stats.totalBytes += Buffer.byteLength(payload, 'utf-8');
        ws.send(payload);
      }
    }

    try {
      page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 720 });

      await page.exposeFunction('__rrwebEmit', (eventJson) => {
        const event = JSON.parse(eventJson);
        stats.events++;
        if (event.type === 2) stats.snapshots++;
        else if (event.type === 3) stats.incrementals++;
        send(JSON.stringify({ type: 'rrweb-event', data: event }));
      });

      async function injectRrweb() {
        try {
          await page.addScriptTag({ url: RRWEB_CDN });
          await page.evaluate(() => {
            if (typeof rrwebRecord === 'function') {
              window.__rrwebStop?.();
              window.__rrwebStop = rrwebRecord({
                emit(event) {
                  window.__rrwebEmit(JSON.stringify(event));
                },
                blockClass: 'rrweb-block',
                maskTextClass: 'rrweb-mask',
                sampling: { mousemove: false, mouseInteraction: true, scroll: 150, input: 'last' },
              });
            }
          });
          log('rrweb recording injected for', page.url());
        } catch (e) {
          log('rrweb injection failed:', e.message);
        }
      }

      page.on('domcontentloaded', async () => {
        send({ type: 'url', data: page.url() });
        await new Promise((r) => setTimeout(r, 200));
        await injectRrweb();
      });

      ws.on('message', async (raw) => {
        if (closed) return;
        let msg;
        try { msg = JSON.parse(raw.toString()); } catch { return; }

        try {
          switch (msg.type) {
            case 'navigate':
              log('Navigate ->', msg.url);
              send({ type: 'rrweb-reset' });
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
                await page.setViewport({ width: Math.round(msg.width), height: Math.round(msg.height) });
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
        log(`Disconnected — ${elapsed}s, ${kb} KB, ${stats.events} events (snap: ${stats.snapshots}, incr: ${stats.incrementals})`);
        if (page && !page.isClosed()) await page.close().catch(() => {});
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
