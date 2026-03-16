import { WebSocketServer, WebSocket } from 'ws';
import http from 'http';

const CHROME_HOST = process.env.CHROME_HOST || 'localhost';
const CHROME_PORT = process.env.CHROME_PORT || '9222';
const PORT = parseInt(process.env.PORT || '3100');
const SCREENSHOT_INTERVAL = 200;
const BROWSER_WS = process.env.BROWSER_WS === '1';
const VIEWPORT_W = 1280;
const VIEWPORT_H = 720;
const HEARTBEAT_INTERVAL = 5000;
const HEARTBEAT_TIMEOUT = 3000;

const UA_FIX_EVAL = `(function(){
  if(document.getElementById('__ua_fix')) return;
  var s = document.createElement('style');
  s.id = '__ua_fix';
  s.textContent =
    'head,base,basefont,link,meta,noembed,noframes,param,rp,script,style,template,title{display:none!important}' +
    'html,body,address,article,aside,blockquote,center,dd,details,dir,div,dl,dt,' +
    'fieldset,figcaption,figure,footer,form,h1,h2,h3,h4,h5,h6,header,hgroup,hr,' +
    'legend,listing,main,marquee,menu,nav,ol,p,plaintext,pre,section,summary,ul,xmp{display:block}' +
    'table{display:table}thead{display:table-header-group}tbody{display:table-row-group}' +
    'tfoot{display:table-footer-group}tr{display:table-row}td,th{display:table-cell}' +
    'col{display:table-column}colgroup{display:table-column-group}caption{display:table-caption}' +
    'li{display:list-item}' +
    'input,textarea,select,button{display:inline-block}' +
    'img,video,canvas,object,embed,iframe{display:inline}' +
    'body{margin:8px}' +
    'head *{display:none!important}';
  (document.head || document.documentElement).prepend(s);
})();`;

const WEBDRIVER_HIDE_SCRIPT = `Object.defineProperty(navigator,'webdriver',{get:()=>false});`;

async function injectUAFix() {
  try { await sendCDP('Runtime.evaluate', { expression: UA_FIX_EVAL }); }
  catch {}
}

let cdpWs = null;
let clients = new Set();
let screenshotTimer = null;
let screenshotInFlight = false;
let cdpSessionId = null;
let currentTargetId = null;
let wsPingInterval = null;
let heartbeatInterval = null;
let reinitLock = false;

function cdpHTTP(path, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: CHROME_HOST,
      port: CHROME_PORT,
      path,
      method,
      headers: { Host: `localhost:${CHROME_PORT}` },
    }, (res) => {
      let body = '';
      res.on('data', c => body += c);
      res.on('end', () => {
        try { resolve(JSON.parse(body)); }
        catch { reject(new Error(`Invalid JSON from CDP: ${body.slice(0, 100)}`)); }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

function rewriteWsUrl(wsUrl) {
  return wsUrl
    .replace('ws://127.0.0.1:', `ws://${CHROME_HOST}:`)
    .replace('ws://localhost:', `ws://${CHROME_HOST}:`);
}

async function getFirstPage() {
  const pages = await cdpHTTP('/json/list');
  const page = pages.find(p => p.type === 'page') || pages[0];
  if (!page) return null;
  return rewriteWsUrl(page.webSocketDebuggerUrl);
}

async function ensurePage() {
  const page = await cdpHTTP('/json/new?about:blank', 'PUT');
  return rewriteWsUrl(page.webSocketDebuggerUrl);
}

let cmdId = 1;
const pendingCmds = new Map();

function sendCDP(method, params = {}, useSession = true, timeout = 10000) {
  return new Promise((resolve, reject) => {
    if (!cdpWs || cdpWs.readyState !== WebSocket.OPEN) {
      return reject(new Error('CDP not connected'));
    }
    const id = cmdId++;
    pendingCmds.set(id, { resolve, reject });
    const msg = { id, method, params };
    if (useSession && cdpSessionId) msg.sessionId = cdpSessionId;
    cdpWs.send(JSON.stringify(msg));
    setTimeout(() => {
      if (pendingCmds.has(id)) {
        pendingCmds.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }
    }, timeout);
  });
}

function startScreenshotLoop() {
  if (screenshotTimer) return;
  screenshotTimer = setInterval(async () => {
    if (screenshotInFlight || clients.size === 0) return;
    screenshotInFlight = true;
    try {
      const result = await sendCDP('Page.captureScreenshot', {
        format: 'jpeg',
        quality: 70,
      });
      if (result?.data) {
        const frame = JSON.stringify({ type: 'frame', data: result.data });
        for (const client of clients) {
          if (client.readyState === WebSocket.OPEN) {
            client.send(frame);
          }
        }
      }
    } catch {}
    screenshotInFlight = false;
  }, SCREENSHOT_INTERVAL);
}

function stopScreenshotLoop() {
  if (screenshotTimer) {
    clearInterval(screenshotTimer);
    screenshotTimer = null;
    screenshotInFlight = false;
  }
}

function stopTimers() {
  if (wsPingInterval) { clearInterval(wsPingInterval); wsPingInterval = null; }
  if (heartbeatInterval) { clearInterval(heartbeatInterval); heartbeatInterval = null; }
}

function startHeartbeat() {
  if (heartbeatInterval) return;
  heartbeatInterval = setInterval(async () => {
    if (!cdpWs || cdpWs.readyState !== WebSocket.OPEN) return;
    try {
      await sendCDP('Runtime.evaluate', { expression: '1' }, true, HEARTBEAT_TIMEOUT);
    } catch {
      console.log('Heartbeat failed, full reconnect...');
      stopScreenshotLoop();
      stopTimers();
      connectCDP();
    }
  }, HEARTBEAT_INTERVAL);
}

async function initTarget() {
  if (!BROWSER_WS || !cdpWs || cdpWs.readyState !== WebSocket.OPEN) return;
  if (reinitLock) return;
  reinitLock = true;

  cdpSessionId = null;
  currentTargetId = null;
  stopScreenshotLoop();

  try {
    await sendCDP('Target.setDiscoverTargets', { discover: true }, false);

    const targets = await sendCDP('Target.getTargets', {}, false);
    const reusable = targets.targetInfos?.find(
      t => t.type === 'page' && !t.attached
    );

    let targetId;
    if (reusable) {
      targetId = reusable.targetId;
      console.log('Reusing target:', targetId);
    } else {
      const created = await sendCDP('Target.createTarget', { url: 'about:blank' }, false);
      targetId = created.targetId;
      console.log('Created target:', targetId);
    }

    const { sessionId } = await sendCDP('Target.attachToTarget', { targetId, flatten: true }, false);
    cdpSessionId = sessionId;
    currentTargetId = targetId;
    console.log('Attached, sessionId:', sessionId);

    await sendCDP('Emulation.setDeviceMetricsOverride', {
      width: VIEWPORT_W,
      height: VIEWPORT_H,
      deviceScaleFactor: 1,
      mobile: false,
    });

    await sendCDP('Page.enable');
    await sendCDP('Page.addScriptToEvaluateOnNewDocument', { source: WEBDRIVER_HIDE_SCRIPT });
    await sendCDP('Page.navigate', { url: 'https://www.baidu.com' });
    console.log('Target ready, starting loops');
    startScreenshotLoop();
    startHeartbeat();
    setTimeout(injectUAFix, 2000);
  } catch (e) {
    console.error('initTarget failed:', e.message);
    reinitLock = false;
    console.log('initTarget failed, full reconnect...');
    setTimeout(connectCDP, 3000);
    return;
  }
  reinitLock = false;
}

function handleCDPEvent(msg) {
  if (msg.method === 'Target.targetDestroyed' && msg.params?.targetId === currentTargetId) {
    console.log('Target destroyed, full reconnect...');
    stopScreenshotLoop();
    stopTimers();
    connectCDP();
  }
  if (msg.method === 'Target.detachedFromTarget' && msg.params?.sessionId === cdpSessionId) {
    console.log('Detached from target, full reconnect...');
    stopScreenshotLoop();
    stopTimers();
    connectCDP();
  }
  if (msg.method === 'Page.domContentEventFired') {
    injectUAFix();
  }
}

async function connectCDP() {
  stopScreenshotLoop();
  stopTimers();
  cdpSessionId = null;
  currentTargetId = null;
  reinitLock = false;
  for (const [, { reject }] of pendingCmds) {
    reject(new Error('CDP reconnecting'));
  }
  pendingCmds.clear();
  if (cdpWs) {
    try { cdpWs.close(); } catch {}
    cdpWs = null;
  }

  let wsUrl;
  if (BROWSER_WS) {
    wsUrl = `ws://${CHROME_HOST}:${CHROME_PORT}`;
    console.log('BROWSER_WS mode');
  } else {
    try {
      wsUrl = await getFirstPage();
      if (!wsUrl) wsUrl = await ensurePage();
    } catch (e) {
      console.error('Failed to get CDP page:', e.message);
      setTimeout(connectCDP, 3000);
      return;
    }
    if (!wsUrl) {
      console.error('No CDP page available');
      setTimeout(connectCDP, 3000);
      return;
    }
  }

  console.log('Connecting:', wsUrl);
  cdpWs = new WebSocket(wsUrl);

  cdpWs.on('open', async () => {
    console.log('CDP connected');
    wsPingInterval = setInterval(() => {
      if (cdpWs?.readyState === WebSocket.OPEN) cdpWs.ping();
    }, 20000);

    if (BROWSER_WS) {
      await initTarget();
    } else {
      try {
        await sendCDP('Emulation.setDeviceMetricsOverride', {
          width: VIEWPORT_W, height: VIEWPORT_H,
          deviceScaleFactor: 1, mobile: false,
        });
        await sendCDP('Page.enable');
        await sendCDP('Page.addScriptToEvaluateOnNewDocument', { source: WEBDRIVER_HIDE_SCRIPT });
        await sendCDP('Page.navigate', { url: 'https://www.baidu.com' });
        console.log('Target ready, starting loops');
        startScreenshotLoop();
        startHeartbeat();
        setTimeout(injectUAFix, 2000);
      } catch (e) {
        console.error('Failed to init page:', e.message);
      }
    }
  });

  cdpWs.on('message', (raw) => {
    const msg = JSON.parse(raw.toString());
    if (msg.id && pendingCmds.has(msg.id)) {
      const { resolve, reject } = pendingCmds.get(msg.id);
      pendingCmds.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
      return;
    }
    if (msg.method) handleCDPEvent(msg);
  });

  cdpWs.on('close', () => {
    console.log('CDP WS closed, reconnecting...');
    cdpSessionId = null;
    currentTargetId = null;
    stopScreenshotLoop();
    stopTimers();
    setTimeout(connectCDP, 2000);
  });

  cdpWs.on('error', (err) => {
    console.error('CDP error:', err.message);
  });
}

const server = http.createServer((req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  if (req.url === '/screenshot') {
    sendCDP('Page.captureScreenshot', { format: 'jpeg', quality: 80 })
      .then(result => {
        if (result?.data) {
          const buf = Buffer.from(result.data, 'base64');
          res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Content-Length': buf.length });
          res.end(buf);
        } else {
          res.writeHead(500, { 'Content-Type': 'text/plain' });
          res.end('No screenshot data');
        }
      })
      .catch(e => {
        res.writeHead(500, { 'Content-Type': 'text/plain' });
        res.end('Screenshot error: ' + e.message);
      });
    return;
  }

  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status: 'ok',
      cdp: cdpWs?.readyState === WebSocket.OPEN,
      session: !!cdpSessionId,
      target: currentTargetId,
      clients: clients.size,
      heartbeat: !!heartbeatInterval,
    }));
    return;
  }

  if (req.url === '/navigate' && req.method === 'POST') {
    let body = '';
    req.on('data', c => body += c);
    req.on('end', async () => {
      try {
        const { url } = JSON.parse(body);
        await sendCDP('Page.navigate', { url });
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: true, url }));
      } catch (e) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  res.writeHead(404);
  res.end('Not Found');
});

const wss = new WebSocketServer({ server });

wss.on('connection', (ws) => {
  console.log('Client connected, total:', clients.size + 1);
  clients.add(ws);

  ws.on('message', (raw) => {
    let parsed;
    try { parsed = JSON.parse(raw.toString()); } catch { return; }

    const fire = (method, params) => sendCDP(method, params).catch(() => {});
    switch (parsed.type) {
      case 'mousemove':
        fire('Input.dispatchMouseEvent', { type: 'mouseMoved', x: parsed.x, y: parsed.y });
        break;
      case 'mousedown':
        fire('Input.dispatchMouseEvent', { type: 'mousePressed', x: parsed.x, y: parsed.y, button: parsed.button === 2 ? 'right' : 'left', clickCount: 1 });
        break;
      case 'mouseup':
        fire('Input.dispatchMouseEvent', { type: 'mouseReleased', x: parsed.x, y: parsed.y, button: parsed.button === 2 ? 'right' : 'left', clickCount: 1 });
        break;
      case 'wheel':
        fire('Input.dispatchMouseEvent', { type: 'mouseWheel', x: parsed.x, y: parsed.y, deltaX: parsed.deltaX, deltaY: parsed.deltaY });
        break;
      case 'keydown':
        fire('Input.dispatchKeyEvent', { type: 'keyDown', key: parsed.key, code: parsed.code, text: parsed.key.length === 1 ? parsed.key : undefined, windowsVirtualKeyCode: parsed.keyCode });
        break;
      case 'keyup':
        fire('Input.dispatchKeyEvent', { type: 'keyUp', key: parsed.key, code: parsed.code, windowsVirtualKeyCode: parsed.keyCode });
        break;
      case 'navigate':
        sendCDP('Page.navigate', { url: parsed.url }).catch(() => {});
        break;
    }
  });

  ws.on('close', () => {
    clients.delete(ws);
    console.log('Client disconnected, total:', clients.size);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`CDP Proxy listening on port ${PORT}`);
  setTimeout(connectCDP, 2000);
});
