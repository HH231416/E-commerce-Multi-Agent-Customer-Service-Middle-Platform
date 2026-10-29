import { onBeforeUnmount, onMounted, ref } from 'vue'

/**
 * 版本探测：页面常驻时资源可能已经更新，但用户仍在使用旧 bundle。
 * 构建产物会下发 version.json，与其内嵌的 __BUILD_ID__ 不一致时提示刷新。
 */
export function useVersionCheck() {
  const outdated = ref(false)
  let timer: number | undefined

  async function check(): Promise<void> {
    if (!import.meta.env.PROD) return
    try {
      const response = await fetch(`${import.meta.env.BASE_URL}version.json`, { cache: 'no-store' })
      if (!response.ok) return
      const data = (await response.json()) as { buildId?: string }
      if (data.buildId && data.buildId !== __BUILD_ID__) outdated.value = true
    } catch {
      // 探测失败不打扰用户
    }
  }

  function onVisibilityChange(): void {
    if (document.visibilityState === 'visible') void check()
  }

  onMounted(() => {
    const interval = Number(import.meta.env.VITE_VERSION_POLL_MS ?? 5 * 60 * 1000)
    timer = window.setInterval(() => void check(), interval)
    document.addEventListener('visibilitychange', onVisibilityChange)
  })

  onBeforeUnmount(() => {
    if (timer) window.clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { outdated, reload: () => window.location.reload() }
}