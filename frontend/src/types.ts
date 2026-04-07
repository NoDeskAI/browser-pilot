export interface ChatBlock {
  type: 'text' | 'tool_call' | 'tool_result' | 'error'
  content?: string
  id?: string
  toolName?: string
  args?: Record<string, any>
  result?: any
  loading?: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  blocks: ChatBlock[]
}

export interface Session {
  id: string
  name: string
  createdAt: string
  updatedAt: string
  messageCount: number
  preview: string
  containerStatus: 'running' | 'paused' | 'exited' | 'not_found'
  ports?: { selenium_port: number; vnc_port: number } | null
}
