<template>
  <div v-if="failed" class="fallback">
    <h1>页面渲染出现异常</h1>
    <p>异常已记录，刷新后通常可以恢复。如果反复出现，请联系技术支持并提供下方时间。</p>
    <p class="time">发生时间：{{ failedAt }}</p>
    <button type="button" class="btn btn-primary" @click="reload">刷新页面</button>
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'
import { reportError } from '../monitoring'

const failed = ref(false)
const failedAt = ref('')

function reload(): void {
  window.location.reload()
}

// 返回 false 阻止异常继续向上冒泡，组件树整体切换为兜底页
onErrorCaptured((error, _instance, info) => {
  reportError(error, `boundary:${info}`)
  failedAt.value = new Date().toLocaleString('zh-CN')
  failed.value = true
  return false
})
</script>

<style scoped>
.fallback {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 32px;
  text-align: center;
}

.fallback h1 {
  font-size: 18px;
}

.fallback p {
  max-width: 46ch;
  font-size: 13px;
  color: var(--text-secondary);
}

.time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-tertiary);
}

.btn {
  margin-top: 8px;
}
</style>