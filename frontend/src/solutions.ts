export interface Solution {
  id: string
  name: string
  tech: string
  protocol: string
  description: string
  port: number
  /** docker-compose service names required for this solution */
  services: string[]
  viewerType: 'iframe' | 'cdp-canvas' | 'mjpeg'
  url: string
  tags: string[]
  color: string
  latency: string
  multiUser: boolean
  github?: string
  stars?: string
}

export const solutions: Solution[] = [
  {
    id: 'selenium',
    name: 'Selenium Grid',
    tech: '内置 noVNC',
    protocol: 'VNC / WebSocket',
    description: '自动化测试基础设施，每个 Node 内置 noVNC，实时查看浏览器画面。',
    port: 7900,
    services: ['selenium'],
    viewerType: 'iframe',
    url: 'http://localhost:7900/?autoconnect=1&resize=scale',
    tags: ['Selenium', 'noVNC', 'Testing', 'Java'],
    color: '#84cc16',
    latency: '100-300ms',
    multiUser: false,
    github: 'https://github.com/SeleniumHQ/docker-selenium',
    stars: '8.3k',
  },
  {
    id: 'mjpeg',
    name: 'MJPEG 截屏流',
    tech: 'Puppeteer + MJPEG',
    protocol: 'multipart/x-mixed-replace',
    description: 'Puppeteer 定时截屏，MJPEG HTTP 推流到 <img>。零 JS 依赖极简方案。',
    port: 3200,
    services: ['mjpeg-stream'],
    viewerType: 'mjpeg',
    url: 'http://localhost:3200/stream',
    tags: ['MJPEG', 'Puppeteer', 'HTTP', 'Screenshot'],
    color: '#f97316',
    latency: '300-500ms',
    multiUser: false,
  },
  {
    id: 'neko',
    name: 'n.eko',
    tech: 'WebRTC',
    protocol: 'WebRTC (UDP)',
    description: '基于 WebRTC 的多人协作虚拟浏览器，内置用户管理、聊天、剪贴板同步。',
    port: 8080,
    services: ['neko'],
    viewerType: 'iframe',
    url: 'http://localhost:8080/?usr=user&pwd=admin',
    tags: ['WebRTC', 'GStreamer', 'Go', 'Vue.js'],
    color: '#6366f1',
    latency: '< 100ms',
    multiUser: true,
    github: 'https://github.com/m1k1o/neko',
    stars: '17.3k',
  },
  {
    id: 'novnc',
    name: 'noVNC + x11vnc',
    tech: 'VNC over WebSocket',
    protocol: 'RFB / WebSocket',
    description: '经典 VNC 方案。Xvfb + x11vnc + websockify + noVNC HTML5 客户端。',
    port: 6080,
    services: ['novnc-chrome'],
    viewerType: 'iframe',
    url: 'http://localhost:6080/vnc.html?autoconnect=true&resize=scale',
    tags: ['VNC', 'x11vnc', 'WebSocket', 'noVNC'],
    color: '#22c55e',
    latency: '100-300ms',
    multiUser: false,
    github: 'https://github.com/novnc/noVNC',
  },
  {
    id: 'kasmvnc',
    name: 'KasmVNC',
    tech: '现代 VNC + WebRTC',
    protocol: 'WebRTC + WASM + WebGL',
    description: 'TigerVNC 现代 fork，一体化集成虚拟显示、编码器和 Web 服务器。',
    port: 6901,
    services: ['kasmvnc'],
    viewerType: 'iframe',
    url: 'http://localhost:6901/?password=password',
    tags: ['KasmVNC', 'WASM', 'WebGL', 'C++'],
    color: '#f59e0b',
    latency: '50-150ms',
    multiUser: false,
    github: 'https://github.com/kasmtech/KasmVNC',
    stars: '4.7k',
  },
  {
    id: 'browserless',
    name: 'Browserless',
    tech: 'CDP + Live Debugger',
    protocol: 'Chrome DevTools Protocol',
    description: '成熟的 BaaS 平台，内置可视化 Live Debugger，支持 Puppeteer/Playwright。',
    port: 3000,
    services: ['browserless', 'browserless-proxy'],
    viewerType: 'cdp-canvas',
    url: 'ws://localhost:3001',
    tags: ['CDP', 'Puppeteer', 'TypeScript', 'BaaS'],
    color: '#ec4899',
    latency: '200-500ms',
    multiUser: false,
    github: 'https://github.com/browserless/browserless',
    stars: '10.1k',
  },
  {
    id: 'cdp-diy',
    name: 'CDP DIY 自研',
    tech: 'CDP Screencast',
    protocol: 'Page.startScreencast / WebSocket',
    description: '直接使用 CDP startScreencast API 逐帧推送 JPEG 到 Canvas，键鼠通过 Input Domain 回传。',
    port: 3100,
    services: ['chrome-headless', 'cdp-proxy'],
    viewerType: 'cdp-canvas',
    url: 'ws://localhost:3100',
    tags: ['CDP', 'WebSocket', 'Canvas', 'JPEG'],
    color: '#06b6d4',
    latency: '200-500ms',
    multiUser: false,
  },
]
