export interface Solution {
  id: string
  name: string
  tech: string
  protocol: string
  description: string
  port: number
  services: string[]
  viewerType: 'iframe' | 'novnc' | 'dom-diff' | 'rrweb'
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
