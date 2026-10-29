import type { App } from 'vue'
import type { AppMetrics } from './types'

/** 运行时指标：可接入外部采集，也可在控制台通过 window.__CS_METRICS__ 查看 */
export const metrics: AppMetrics = {
  streams: 0,
  errors: 0,
  firstTokenMs: null,
  lastLatencyMs: null,
  cacheHits: 0,
}

const ENDPOINT = import.meta.env.VITE_ERROR_ENDPOINT ?? ''

function serialize(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}\n${error.stack ?? ''}`
  if (typeof error === 'string') return error
  try {
    return JSON.stringify(error)
  } catch {
    return String(error)
  }
}

/**
 * 上报错误。
 * 未配置 VITE_ERROR_ENDPOINT 时只落到控制台与本地计数，
 * 这样本地开发不需要额外服务，生产可通过环境变量接自建收集端或 Sentry 网关。
 */
export function reportError(error: unknown, context: string): void {
  metrics.errors += 1
  const detail = serialize(error)
  console.error(`[monitor:${context}]`, detail)

  if (!ENDPOINT) return
  try {
    const payload = JSON.stringify({
      context,
      message: detail,
      buildId: __BUILD_ID__,
      url: window.location.href,
      timestamp: Date.now(),
    })
    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([payload], { type: 'application/json' }))
    } else {
      void fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      })
    }
  } catch {
    // 上报失败不能影响主流程
  }
}

/** 注册全局错误捕获：脚本异常、未处理 Promise、Vue 组件异常 */
export function installMonitoring(app: App): void {
  window.addEventListener('error', (event) => {
    reportError(event.error ?? event.message, 'window.error')
  })

  window.addEventListener('unhandledrejection', (event) => {
    reportError(event.reason, 'unhandledrejection')
  })

  app.config.errorHandler = (error, _instance, info) => {
    reportError(error, `vue:${info}`)
  }

  ;(window as unknown as Record<string, unknown>).__CS_METRICS__ = metrics
}