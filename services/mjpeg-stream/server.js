import puppeteer from 'puppeteer-core';
import http from 'http';
import { URL } from 'url';

const PORT = parseInt(process.env.PORT || '3200');
const CHROME_PATH = process.env.CHROME_PATH || '/usr/bin/chromium';

let browser = null;
let page = null;
const BOUNDARY = '--mjpeg-boundary';
const streamClients = new Set();

async function initBrowser() {
  browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
      '--window-size=1280,720',
    ],
    defaultViewport: { width: 1280, height: 720 },
  });

  page = await browser.newPage();
  await page.goto('https://www.baidu.com', { waitUntil: 'networkidle0' });
  console.log('Browser ready, page loaded');
  startScreenshotLoop();
}

async function startScreenshotLoop() {
  const FPS = 5;
  const interval = 1000 / FPS;

  setInterval(async () => {
    if (!page || streamClients.size === 0) return;
    try {
      const screenshot = await page.screenshot({
        type: 'jpeg',
        quality: 70,
      });

      const header = `\r\n${BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: ${screenshot.length}\r\n\r\n`;
      const headerBuf = Buffer.from(header);

      for (const res of streamClients) {
        try {
          res.write(headerBuf);
          res.write(screenshot);
        } catch {
          streamClients.delete(res);
        }
      }
    } catch (e) {
      // Page might be navigating
    }
  }, interval);
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === '/stream') {
    res.writeHead(200, {
      'Content-Type': `multipart/x-mixed-replace; boundary=${BOUNDARY}`,
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });
    streamClients.add(res);
    req.on('close', () => streamClients.delete(res));
    return;
  }

  if (url.pathname === '/screenshot') {
    if (!page) {
      res.writeHead(503);
      res.end('Browser not ready');
      return;
    }
    const screenshot = await page.screenshot({ type: 'jpeg', quality: 80 });
    res.writeHead(200, {
      'Content-Type': 'image/jpeg',
      'Content-Length': screenshot.length,
    });
    res.end(screenshot);
    return;
  }

  if (url.pathname === '/navigate' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { url: targetUrl } = JSON.parse(body);
        await page.goto(targetUrl, { waitUntil: 'networkidle0', timeout: 15000 });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, url: page.url() }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (url.pathname === '/click' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { x, y } = JSON.parse(body);
        await page.mouse.click(x, y);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (url.pathname === '/type' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { text } = JSON.parse(body);
        await page.keyboard.type(text);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (url.pathname === '/key' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { key } = JSON.parse(body);
        await page.keyboard.press(key);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (url.pathname === '/mousemove' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { x, y } = JSON.parse(body);
        await page.mouse.move(x, y);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (url.pathname === '/scroll' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { x, y, deltaX, deltaY } = JSON.parse(body);
        await page.mouse.wheel({ deltaX, deltaY });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  if (url.pathname === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'ok',
      browser: !!browser,
      page: !!page,
      streamClients: streamClients.size,
    }));
    return;
  }

  if (url.pathname === '/') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(`<!DOCTYPE html>
<html><head><title>MJPEG Browser Stream</title></head>
<body style="margin:0;background:#000;display:flex;justify-content:center;align-items:center;height:100vh">
<img src="/stream" style="max-width:100%;max-height:100vh"/>
</body></html>`);
    return;
  }

  res.writeHead(404);
  res.end('Not Found');
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`MJPEG Stream server on port ${PORT}`);
  initBrowser().catch(e => {
    console.error('Browser init failed:', e);
    process.exit(1);
  });
});
