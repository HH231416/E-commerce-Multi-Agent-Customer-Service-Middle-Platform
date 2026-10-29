import { describe, expect, it } from 'vitest'
import { createSseParser, type SseEvent } from './sse'

function collect(): { events: SseEvent[]; parser: ReturnType<typeof createSseParser> } {
  const events: SseEvent[] = []
  return { events, parser: createSseParser((event) => events.push(event)) }
}

describe('SSE 解析器', () => {
  it('解析标准 event/data 帧', () => {
    const { events, parser } = collect()
    parser.push('event: token\ndata: {"content":"你"}\n\n')
    parser.push('event: done\ndata: {"total_tokens":12}\n\n')
    expect(events).toEqual([
      { type: 'token', data: { content: '你' } },
      { type: 'done', data: { total_tokens: 12 } },
    ])
  })

  it('兼容被外层 data: 再包一层的格式', () => {
    const { events, parser } = collect()
    const inner = 'event: thinking\ndata: {"step":"正在查询订单..."}\n\n'
    const wrapped =
      inner
        .split('\n')
        .map((line) => `data: ${line}`)
        .join('\n') + '\n\n'

    parser.push(wrapped)
    expect(events).toEqual([{ type: 'thinking', data: { step: '正在查询订单...' } }])
  })

  it('跨 chunk 拆分时保持帧完整', () => {
    const { events, parser } = collect()
    parser.push('event: tok')
    parser.push('en\ndata: {"content":"a"}\n')
    parser.push('\nevent: token\ndata: {"content":"b"}\n\n')
    expect(events.map((event) => event.data)).toEqual([{ content: 'a' }, { content: 'b' }])
  })

  it('忽略注释心跳行', () => {
    const { events, parser } = collect()
    parser.push(': ping - 2026-10-27\n\n')
    parser.push('event: token\ndata: {"content":"ok"}\n\n')
    expect(events).toHaveLength(1)
    expect(events[0]).toEqual({ type: 'token', data: { content: 'ok' } })
  })

  it('非 JSON 载荷退化为纯文本', () => {
    const { events, parser } = collect()
    parser.push('event: message\ndata: hello\n\n')
    expect(events[0]).toEqual({ type: 'message', data: { content: 'hello' } })
  })

  it('flush 处理未以空行结尾的残留帧', () => {
    const { events, parser } = collect()
    parser.push('event: token\ndata: {"content":"tail"}')
    expect(events).toHaveLength(0)
    parser.flush()
    expect(events).toEqual([{ type: 'token', data: { content: 'tail' } }])
  })

  it('无事件名时回退为 message', () => {
    const { events, parser } = collect()
    parser.push('data: {"content":"x"}\n\n')
    expect(events[0]).toEqual({ type: 'message', data: { content: 'x' } })
  })
})