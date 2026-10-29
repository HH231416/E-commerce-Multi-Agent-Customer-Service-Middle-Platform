/**
 * SSE 解析器：把字节流按事件边界切分并归一化。
 *
 * 为什么单独抽成纯函数模块：
 * 1. 解析逻辑是整条流式链路里最容易出错的部分，独立后可单测
 * 2. 兼容两种线上格式：
 *    - 标准 SSE：`event: token` / `data: {...}`
 *    - 预格式化字符串被 EventSourceResponse 再包一层 `data:` 的情况
 */

export interface SseEvent {
  type: string
  data: unknown
}

const SEPARATOR = /\r?\n\r?\n/
const NESTED_HINT = /(^|\n)(event|data): /
const MAX_NESTING = 3

function parseBlock(block: string): { event: string; data: string } {
  let event = ''
  const dataLines: string[] = []
  for (const line of block.split(/\r?\n/)) {
    // 空行与以 ':' 开头的注释行（sse-starlette 的 ping 心跳）直接跳过
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
  }
  return { event, data: dataLines.join('\n') }
}

function decodePayload(data: string): unknown {
  if (!data) return {}
  try {
    return JSON.parse(data)
  } catch {
    // 非 JSON 载荷退化为纯文本，交由调用方决定是否展示
    return { content: data }
  }
}

export function createSseParser(emit: (event: SseEvent) => void) {
  let buffer = ''

  function dispatch(block: string, depth = 0): void {
    const { event, data } = parseBlock(block)
    if (!event && !data) return

    if (depth < MAX_NESTING && NESTED_HINT.test(data)) {
      for (const inner of data.split(/\r?\n\r?\n/)) {
        if (inner.trim()) dispatch(inner, depth + 1)
      }
      return
    }

    emit({ type: event || 'message', data: decodePayload(data) })
  }

  function drain(): void {
    let match = SEPARATOR.exec(buffer)
    while (match) {
      const block = buffer.slice(0, match.index)
      buffer = buffer.slice(match.index + match[0].length)
      dispatch(block)
      match = SEPARATOR.exec(buffer)
    }
  }

  return {
    push(chunk: string): void {
      buffer += chunk
      drain()
    },
    /** 流结束时调用，处理没有以空行收尾的残留帧 */
    flush(): void {
      if (buffer.trim()) dispatch(buffer)
      buffer = ''
    },
  }
}