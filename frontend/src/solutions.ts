export interface Solution {
  id: string
  name: string
  tech: string
  protocol: string
  description: string
  port: number
  services: string[]
  viewerType: 'iframe' | 'novnc'
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
]
