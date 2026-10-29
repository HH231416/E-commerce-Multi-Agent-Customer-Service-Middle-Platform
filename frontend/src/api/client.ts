import type { HistoryItem, Identity } from '../types'
import { createSseParser } from './sse'

// 开发态由 Vite 代理 /api；构建态由 FastAPI 同源托管，因此默认不带域名前缀。
export const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export type StreamEventHandler = (type: string, payload: unknown) => void

let unauthorizedHandler: (() => void) | null = null

/** 由路由层注册：收到 401 时清理身份并跳转登录 */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler
}

export function buildHeaders(identity: Identity, extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra }
  if (identity.token) {
    headers.Authorization = `Bearer ${identity.token}`
  } else if (identity.mode === 'debug' && import.meta.env.DEV) {
    // 调试身份头只在开发环境发送，避免打包进生产后误用弱鉴权
    headers['X-Debug-User-Id'] = identity.userId
    headers['X-Debug-Tenant-Id'] = identity.tenantId || 'demo'
    headers['X-Debug-Roles'] = identity.roles || 'customer'
  }
  return headers
}

export interface SessionInfo {
  user_id: string
  tenant_id: string
  roles: string[]
  expires_in: number
}

/**
 * 用 Bearer 令牌换取 HttpOnly 会话 Cookie。
 * 成功后前端不再持有令牌，后续请求由浏览器自动携带 Cookie。
 */
export async function startSession(token: string): Promise<SessionInfo> {
  const response = await fetch(`${API_BASE}/api/auth/session`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    credentials: 'same-origin',
  })
  await ensureOk(response)
  return (await response.json()) as SessionInfo
}

/** 清除服务端会话 Cookie */
export async function endSession(): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/auth/session`, {
      method: 'DELETE',
      credentials: 'same-origin',
    })
  } catch {
    // 登出失败不阻塞本地身份清理
  }
}

const STATUS_TEXT: Record<number, string> = {
  401: '身份校验未通过，请重新登录',
  403: '当前身份无权访问该资源',
  404: '请求的接口不存在',
  429: '请求过于频繁，请稍后再试',
  500: '后端服务暂不可用，请确认 API 已启动',
  502: '网关异常，请稍后重试',
  503: '服务尚未就绪，请稍后重试',
  504: '请求超时，请稍后重试',
}

async function describeError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (body && body.detail) return String(body.detail)
  } catch {
    // 响应体不是 JSON 时退化为状态码描述
  }
  return STATUS_TEXT[response.status] ?? `请求失败（HTTP ${response.status}）`
}

/** 统一处理非 2xx：401 时触发登出，再抛出可展示的错误文案 */
async function ensureOk(response: Response): Promise<Response> {
  if (response.ok) return response
  if (response.status === 401) unauthorizedHandler?.()
  throw new Error(await describeError(response))
}

export async function fetchHistory(options: {
  identity: Identity
  userId: string
  signal?: AbortSignal
}): Promise<HistoryItem[]> {
  const { identity, userId, signal } = options
  const response = await fetch(`${API_BASE}/api/chat_history/${encodeURIComponent(userId)}`, {
    headers: buildHeaders(identity),
    credentials: 'same-origin',
    signal,
  })
  await ensureOk(response)
  const data = (await response.json()) as { history?: HistoryItem[] }
  return Array.isArray(data?.history) ? data.history : []
}

export async function submitFeedback(options: {
  identity: Identity
  userId: string
  score: number
}): Promise<void> {
  const { identity, userId, score } = options
  const response = await fetch(
    `${API_BASE}/api/feedback/${encodeURIComponent(userId)}?score=${encodeURIComponent(score)}`,
    { headers: buildHeaders(identity), credentials: 'same-origin' },
  )
  await ensureOk(response)
}

export interface StreamChatOptions {
  identity: Identity
  userId: string
  message: string
  signal: AbortSignal
  onEvent: StreamEventHandler
}

/**
 * POST 发起对话并增量消费 SSE。
 * 原生 EventSource 只支持 GET，因此这里用 fetch + ReadableStream 手动解析。
 */
export async function streamChat(options: StreamChatOptions): Promise<void> {
  const { identity, userId, message, signal, onEvent } = options

  const response = await fetch(`${API_BASE}/api/chat/${encodeURIComponent(userId)}/stream`, {
    method: 'POST',
    headers: buildHeaders(identity, {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    }),
    body: JSON.stringify({ message }),
    credentials: 'same-origin',
    signal,
  })

  await ensureOk(response)
  if (!response.body) throw new Error('当前浏览器不支持流式响应')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = createSseParser((event) => onEvent(event.type, event.data))

  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      parser.push(decoder.decode(value, { stream: true }))
    }
    parser.push(decoder.decode())
    parser.flush()
  } finally {
    reader.releaseLock()
  }
}