import puppeteer from 'puppeteer-core';
import { createServer } from 'http';
import { WebSocketServer } from 'ws';

const PORT = parseInt(process.env.PORT || '3200');
const CHROME_PATH = process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium';
const TARGET_FPS = parseInt(process.env.FPS || '5');

function log(msg, ...args) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  console.log(`[mjpeg ${ts}] ${msg}`, ...args);
}

function cors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
}

function readBody(req) {
  return new Promise((resolve) => {
    let body = '';
    req.on('data', (c) => { body += c; });
    req.on('end', () => resolve(body));
  });
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
  log('Browser launched');

  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 720 });
  await page.goto('about:blank');

  const stats = { totalBytes: 0, frames: 0, startTime: Date.now() };
  const streamClients = new Set();

  async function captureLoop() {
    while (true) {
      if (streamClients.size > 0) {
        try {
          const buf = await page.screenshot({ type: 'jpeg', quality: 50 });
          stats.totalBytes += buf.length * streamClients.size;
          stats.frames++;
          const boundary = '--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + buf.length + '\r\n\r\n';
          for (const res of streamClients) {
            try {
              res.write(boundary);
              res.write(buf);
              res.write('\r\n');
            } catch { streamClients.delete(res); }
          }
        } catch {}
      }
      await new Promise((r) => setTimeout(r, 1000 / TARGET_FPS));
    }
  }
  captureLoop();

  const server = createServer(async (req, res) => {
    cors(res);
    const url = new URL(req.url, 'http://localhost');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    if (url.pathname === '/stream' && req.method === 'GET') {
      res.writeHead(200, {
        'Content-Type': 'multipart/x-mixed-replace; boundary=--frame',
        'Cache-Control': 'no-cache, no-store',
        'Connection': 'keep-alive',
      });
      streamClients.add(res);
      req.on('close', () => streamClients.delete(res));
      return;
    }

    if (url.pathname === '/navigate' && req.method === 'POST') {
      try {
        const { url: navUrl } = JSON.parse(await readBody(req));
        log('Navigate ->', navUrl);
        stats.totalBytes = 0;
        stats.frames = 0;
        stats.startTime = Date.now();
        await page.goto(navUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, url: page.url() }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
      return;
    }

    if (url.pathname === '/click' && req.method === 'POST') {
      try {
        const { x, y, button } = JSON.parse(await readBody(req));
        await page.mouse.click(x, y, { button: button === 2 ? 'right' : 'left' });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
      return;
    }

    if (url.pathname === '/scroll' && req.method === 'POST') {
      try {
        const { dx, dy } = JSON.parse(await readBody(req));
        await page.mouse.wheel({ deltaX: dx || 0, deltaY: dy || 0 });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
      return;
    }

    if (url.pathname === '/key' && req.method === 'POST') {
      try {
        const { key } = JSON.parse(await readBody(req));
        await page.keyboard.press(key);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: e.message }));
      }
      return;
    }

    if (url.pathname === '/stats' && req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({
        totalBytes: stats.totalBytes,
        frames: stats.frames,
        elapsed: (Date.now() - stats.startTime) / 1000,
        avgFrameSize: stats.frames > 0 ? Math.round(stats.totalBytes / stats.frames) : 0,
      }));
      return;
    }

    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not found' }));
  });

  const wss = new WebSocketServer({ noServer: true });
  server.on('upgrade', (req, socket, head) => {
    if (new URL(req.url, 'http://localhost').pathname === '/ws') {
      wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws));
    } else {
      socket.destroy();
    }
  });

  wss.on('connection', (ws) => {
    log('WS interaction client connected');
    ws.on('message', async (raw) => {
      let msg;
      try { msg = JSON.parse(raw.toString()); } catch { return; }
      try {
        switch (msg.type) {
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
              log('Viewport resized to', Math.round(msg.width) + 'x' + Math.round(msg.height));
            }
            break;
        }
      } catch (e) {
        log('WS input error:', msg.type, e.message);
      }
    });
  });

  server.listen(PORT, () => {
    log('HTTP + WS server listening on port', PORT);
  });

  process.on('SIGINT', async () => {
    log('Shutting down...');
    server.close();
    await browser.close();
    process.exit(0);
  });
}

main().catch((e) => {
  log('Fatal:', e.message);
  process.exit(1);
});
