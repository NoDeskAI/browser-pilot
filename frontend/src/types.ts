export interface Session {
  id: string
  name: string
  createdAt: string
  updatedAt: string
  currentUrl: string
  currentTitle: string
  containerStatus: 'running' | 'paused' | 'exited' | 'not_found'
  ports?: { selenium_port: number; vnc_port: number } | null
}
