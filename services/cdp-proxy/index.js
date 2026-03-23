import puppeteer from 'puppeteer-core';
import { WebSocketServer } from 'ws';

const PORT = parseInt(process.env.PORT || '3100');
const CHROME_PATH = process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium';
const TARGET_FPS = parseInt(process.env.FPS || '5');

function log(msg, ...args) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  console.log(`[cdp-screenshot ${ts}] ${msg}`, ...args);
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
    let captureTimer = null;
    const stats = { totalBytes: 0, frames: 0, startTime: Date.now() };

    function send(payload) {
      if (!closed && ws.readyState === ws.OPEN) {
        const str = typeof payload === 'string' ? payload : JSON.stringify(payload);
        stats.totalBytes += Buffer.byteLength(str, 'utf-8');
        ws.send(str);
      }
    }

    function startCapture() {
      stopCapture();
      const interval = 1000 / TARGET_FPS;
      let capturing = false;
      captureTimer = setInterval(async () => {
        if (capturing || closed || !page || page.isClosed()) return;
        capturing = true;
        try {
          const buf = await page.screenshot({ type: 'jpeg', quality: 50, encoding: 'base64' });
          stats.frames++;
          send(JSON.stringify({ type: 'frame', data: buf }));
        } catch {}
        capturing = false;
      }, interval);
    }

    function stopCapture() {
      if (captureTimer) { clearInterval(captureTimer); captureTimer = null; }
    }

    try {
      page = await browser.newPage();
      await page.setViewport({ width: 1280, height: 720 });

      ws.on('message', async (raw) => {
        if (closed) return;
        let msg;
        try { msg = JSON.parse(raw.toString()); } catch { return; }

        try {
          switch (msg.type) {
            case 'navigate':
              log('Navigate ->', msg.url);
              stopCapture();
              await page.goto(msg.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
              send({ type: 'url', data: page.url() });
              startCapture();
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
        stopCapture();
        const elapsed = ((Date.now() - stats.startTime) / 1000).toFixed(1);
        const kb = (stats.totalBytes / 1024).toFixed(1);
        log(`Disconnected — ${elapsed}s, ${kb} KB, ${stats.frames} frames`);
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
