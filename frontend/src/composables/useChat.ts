import { computed, reactive, ref } from 'vue'
import { fetchHistory, streamChat, submitFeedback } from '../api/client'
import { metrics, reportError } from '../monitoring'
import type {
  ChatMessage,
  HistoryItem,
  MessageStatus,
  SessionStats,
  StreamPayloads,
} from '../types'
import { useAuth } from './useAuth'

export function useChat() {
  const { state: identity } = useAuth()

  const messages = ref<ChatMessage[]>([])
  const streaming = ref(false)
  const loadingHistory = ref(false)
  const loadError = ref('')
  const transfer = ref<StreamPayloads['transfer'] | null>(null)

  const stats = reactive<SessionStats>({
    turns: 0,
    tokens: 0,
    latencyTotal: 0,
    latencySamples: 0,
    cacheHits: 0,
  })

  let controller: AbortController | null = null
  let sequence = 0

  const averageLatency = computed(() =>
    stats.latencySamples ? Math.round(stats.latencyTotal / stats.latencySamples) : 0,
  )

  function makeMessage(role: ChatMessage['role'], content: string): ChatMessage {
    return {
      id: `m${++sequence}`,
      role,
      content,
      createdAt: Date.now(),
      steps: [],
      tools: [],
      metrics: null,
      status: 'done',
      transfer: null,
      rating: 0,
    }
  }

  function addStep(message: ChatMessage, step: unknown): void {
    const text = typeof step === 'string' ? step.trim() : ''
    if (!text) return
    if (message.steps[message.steps.length - 1] === text) return
    message.steps.push(text)
  }

  function recordStats(payload: StreamPayloads['done']): void {
    if (Number.isFinite(payload.total_tokens)) stats.tokens += payload.total_tokens as number
    if (Number.isFinite(payload.latency_ms)) {
      stats.latencyTotal += payload.latency_ms as number
      stats.latencySamples += 1
      metrics.lastLatencyMs = payload.latency_ms as number
    }
    if (payload.cached === true) {
      stats.cacheHits += 1
      metrics.cacheHits += 1
    }
  }

  async function loadHistory(): Promise<void> {
    if (!identity.userId) return
    loadingHistory.value = true
    loadError.value = ''
    try {
      const history = await fetchHistory({ identity, userId: identity.userId })
      messages.value = history
        .filter((item: HistoryItem) => typeof item?.content === 'string')
        .filter((item: HistoryItem) => item.role === 'user' || item.role === 'assistant')
        .map((item: HistoryItem) => makeMessage(item.role as ChatMessage['role'], item.content as string))
    } catch (error) {
      loadError.value = (error as Error)?.message ?? '会话历史加载失败'
    } finally {
      loadingHistory.value = false
    }
  }

  async function send(rawText: string): Promise<void> {
    const text = (rawText || '').trim()
    if (!text || streaming.value) return

    loadError.value = ''
    transfer.value = null

    messages.value.push(makeMessage('user', text))
    messages.value.push(makeMessage('assistant', ''))
    // 取回数组中的响应式代理，后续增量修改才能触发视图更新
    const assistant = messages.value[messages.value.length - 1]
    assistant.status = 'streaming' satisfies MessageStatus

    const startedAt = performance.now()
    let firstTokenAt: number | null = null

    streaming.value = true
    metrics.streams += 1
    controller = new AbortController()

    try {
      await streamChat({
        identity,
        userId: identity.userId,
        message: text,
        signal: controller.signal,
        onEvent: (type, payload) => {
          switch (type) {
            case 'thinking': {
              addStep(assistant, (payload as StreamPayloads['thinking']).step)
              break
            }
            case 'tool_start': {
              const data = payload as StreamPayloads['tool_start']
              assistant.tools.push({ name: data.tool ?? 'tool', args: data.args ?? {}, done: false })
              break
            }
            case 'tool_end': {
              const data = payload as StreamPayloads['tool_end']
              const running = [...assistant.tools].reverse().find((tool) => tool.name === data.tool && !tool.done)
              if (running) running.done = true
              if (data.summary) addStep(assistant, data.summary)
              break
            }
            case 'token': {
              const data = payload as StreamPayloads['token']
              if (firstTokenAt === null) {
                firstTokenAt = performance.now()
                metrics.firstTokenMs = Math.round(firstTokenAt - startedAt)
              }
              assistant.content += data.content ?? ''
              break
            }
            case 'transfer': {
              const data = payload as StreamPayloads['transfer']
              assistant.transfer = data
              transfer.value = data
              if (!assistant.content) assistant.content = data.message ?? '已为您转接人工客服。'
              break
            }
            case 'done': {
              const data = payload as StreamPayloads['done']
              assistant.metrics = data
              assistant.status = 'done'
              recordStats(data)
              break
            }
            case 'error': {
              const data = payload as StreamPayloads['error']
              const reason = data.message ?? '处理出错，请稍后重试。'
              assistant.status = 'error'
              // 失败原因单独存放：即使前面已经流出了部分正文，也要让用户看到真实原因
              assistant.error = reason
              if (!assistant.content) assistant.content = reason
              reportError(new Error(reason), 'chat.stream')
              break
            }
            default:
              break
          }
        },
      })

      if (assistant.status === 'streaming') assistant.status = 'done'
    } catch (error) {
      if ((error as Error)?.name === 'AbortError') {
        assistant.status = 'stopped'
        if (!assistant.content) assistant.content = '（已停止生成）'
      } else {
        assistant.status = 'error'
        if (!assistant.content) assistant.content = `请求失败：${(error as Error)?.message ?? '未知错误'}`
        reportError(error, 'chat.request')
      }
    } finally {
      streaming.value = false
      controller = null
      stats.turns += 1
    }
  }

  function stop(): void {
    controller?.abort()
  }

  async function rate(message: ChatMessage, score: number): Promise<void> {
    if (!message || message.rating === score) return
    try {
      await submitFeedback({ identity, userId: identity.userId, score })
      message.rating = score
    } catch (error) {
      // 反馈失败不打断对话，只记录
      reportError(error, 'chat.feedback')
    }
  }

  return {
    messages,
    streaming,
    loadingHistory,
    loadError,
    transfer,
    stats,
    averageLatency,
    loadHistory,
    send,
    stop,
    rate,
  }
}