export type AuthMode = 'debug' | 'cookie' | 'bearer'

export interface Identity {
  userId: string
  tenantId: string
  roles: string
  /** 仅在换取会话的瞬间持有，成功后立即清空 */
  token: string
  mode: AuthMode
}

export type MessageRole = 'user' | 'assistant'
export type MessageStatus = 'done' | 'streaming' | 'error' | 'stopped'

export interface ToolCall {
  name: string
  args: Record<string, unknown>
  done: boolean
}

export interface MessageMetrics {
  total_tokens?: number
  cost?: number
  latency_ms?: number
  cached?: boolean
}

export interface TransferInfo {
  reason?: string
  ticket_id?: string
  message?: string
}

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  createdAt: number
  steps: string[]
  tools: ToolCall[]
  metrics: MessageMetrics | null
  status: MessageStatus
  transfer: TransferInfo | null
  rating: number
  /** 服务端返回的失败原因，单独展示，避免被已生成的正文掩盖 */
  error?: string
}

export interface HistoryItem {
  role?: string
  content?: string
}

export interface SessionStats {
  turns: number
  tokens: number
  latencyTotal: number
  latencySamples: number
  cacheHits: number
}

/** 后端 SSE 事件与载荷的对应关系 */
export interface StreamPayloads {
  thinking: { step?: string }
  tool_start: { tool?: string; args?: Record<string, unknown> }
  tool_end: { tool?: string; summary?: string }
  token: { content?: string }
  transfer: TransferInfo
  done: MessageMetrics
  error: { message?: string }
}

export interface AppMetrics {
  streams: number
  errors: number
  firstTokenMs: number | null
  lastLatencyMs: number | null
  cacheHits: number
}