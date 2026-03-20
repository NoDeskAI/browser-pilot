export interface Solution {
  id: string
  name: string
  tech: string
  protocol: string
  description: string
  port: number
  services: string[]
  viewerType: 'iframe' | 'novnc' | 'dom-diff' | 'cdp-screenshot' | 'mjpeg' | 'rrweb'
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
    viewerType: 'novnc',
    url: 'ws://localhost:7900/websockify',
    tags: ['Selenium', 'noVNC', 'Testing', 'Java'],
    color: '#84cc16',
    latency: '100-300ms',
    multiUser: false,
    github: 'https://github.com/SeleniumHQ/docker-selenium',
    stars: '8.3k',
  },
  {
    id: 'cdp-screenshot',
    name: 'CDP Screenshot',
    tech: 'Page.captureScreenshot + Canvas',
    protocol: 'WebSocket / Base64 JPEG',
    description: '通过 CDP 截图接口定时抓取页面 JPEG 截图，WebSocket 推送 base64 帧到客户端 Canvas 渲染。',
    port: 3100,
    services: ['cdp-proxy'],
    viewerType: 'cdp-screenshot',
    url: 'ws://localhost:3100',
    tags: ['CDP', 'Screenshot', 'JPEG', 'Canvas', 'Puppeteer'],
    color: '#a855f7',
    latency: '200-400ms',
    multiUser: false,
  },
  {
    id: 'mjpeg',
    name: 'MJPEG Stream',
    tech: 'HTTP Multipart JPEG',
    protocol: 'HTTP / multipart/x-mixed-replace',
    description: 'MJPEG 视频流方案：通过 HTTP multipart 持续推送 JPEG 帧，浏览器原生 img 标签直接渲染。',
    port: 3200,
    services: ['mjpeg-stream'],
    viewerType: 'mjpeg',
    url: 'http://localhost:3200',
    tags: ['MJPEG', 'HTTP', 'Multipart', 'Puppeteer'],
    color: '#f97316',
    latency: '200-500ms',
    multiUser: true,
  },
  {
    id: 'dom-diff',
    name: 'DOM Diff Streaming',
    tech: 'MutationObserver + DOM Serialization',
    protocol: 'WebSocket / DOM Diff',
    description: '基于 DOM 差异传输的方案：只传输 DOM 变更而非像素流，极低带宽消耗。',
    port: 3300,
    services: ['dom-diff-proxy'],
    viewerType: 'dom-diff',
    url: 'ws://localhost:3300',
    tags: ['DOM Diff', 'MutationObserver', 'Low BW', 'Puppeteer'],
    color: '#06b6d4',
    latency: '50-200ms',
    multiUser: false,
  },
  {
    id: 'rrweb',
    name: 'rrweb Live',
    tech: 'rrweb Record + Replayer',
    protocol: 'WebSocket / rrweb Events',
    description: '使用 rrweb 录制库捕获 DOM 快照与增量事件，客户端通过 rrweb Replayer Live Mode 实时回放。',
    port: 3400,
    services: ['rrweb-proxy'],
    viewerType: 'rrweb',
    url: 'ws://localhost:3400',
    tags: ['rrweb', 'Session Replay', 'DOM', 'Low BW', 'Puppeteer'],
    color: '#22c55e',
    latency: '100-300ms',
    multiUser: false,
    github: 'https://github.com/rrweb-io/rrweb',
    stars: '17k',
  },
]
