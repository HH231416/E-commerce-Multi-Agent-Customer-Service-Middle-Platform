<template>
  <article class="msg" :class="message.role">
    <div v-if="isAssistant" class="avatar" aria-hidden="true">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
        <path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9L12 3z" fill="currentColor" />
        <circle cx="18" cy="17.5" r="2.2" fill="currentColor" opacity="0.65" />
      </svg>
    </div>

    <div class="column">
      <div class="meta">
        <span class="who">{{ isAssistant ? '智能客服' : '我' }}</span>
        <span class="time">{{ timeLabel }}</span>
        <span v-if="message.status === 'streaming'" class="live-tag">生成中</span>
        <span v-else-if="message.status === 'stopped'" class="chip neutral">已停止</span>
        <span v-else-if="message.status === 'error'" class="chip danger">异常</span>
      </div>

      <ThinkingTrace
        v-if="isAssistant && (message.steps.length || message.tools.length)"
        :steps="message.steps"
        :tools="message.tools"
        :streaming="message.status === 'streaming'"
        :has-content="Boolean(message.content)"
      />

      <div v-if="isAssistant" class="bubble assistant">
        <div v-if="!message.content && message.status === 'streaming'" class="typing" aria-label="正在输入">
          <span></span><span></span><span></span>
        </div>
        <template v-else>
          <div class="md-body" v-html="renderedContent"></div>
          <span v-if="message.status === 'streaming'" class="caret" aria-hidden="true"></span>
        </template>

        <div v-if="message.error" class="error-row">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2" />
            <path d="M12 8v4m0 4h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
          <span>{{ message.error }}</span>
        </div>

        <div v-if="message.transfer" class="transfer">
          <div class="transfer-head">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
            <span>已转接人工客服</span>
          </div>
          <p class="transfer-text">{{ message.transfer.message || '客服人员将尽快接入。' }}</p>
          <p v-if="message.transfer.ticket_id" class="transfer-ticket">
            工单号 <code>{{ message.transfer.ticket_id }}</code>
          </p>
        </div>

        <div v-if="showMetrics" class="metrics">
          <span v-if="message.metrics?.latency_ms">耗时 {{ latencyText }}</span>
          <span v-if="message.metrics?.total_tokens"> {{ message.metrics.total_tokens }} tokens</span>
          <span v-if="message.metrics?.cost !== undefined && message.metrics?.cost !== null">
            ¥{{ message.metrics.cost }}
          </span>
          <span v-if="message.metrics?.cached" class="cached">缓存命中</span>
          <div class="rating">
            <button
              v-for="score in 5"
              :key="score"
              type="button"
              class="star"
              :class="{ active: score <= message.rating }"
              :aria-label="`评分 ${score} 分`"
              @click="$emit('rate', score)"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" :fill="score <= message.rating ? 'currentColor' : 'none'">
                <path
                  d="M12 2.5l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3.1-5.8 3.1 1.1-6.5L2.6 9.3l6.5-.9L12 2.5z"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linejoin="round"
                />
              </svg>
            </button>
          </div>
        </div>
      </div>

      <div v-else class="bubble user">{{ message.content }}</div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ThinkingTrace from './ThinkingTrace.vue'
import type { ChatMessage } from '../types'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps<{
  message: ChatMessage
}>()

defineEmits<{
  rate: [score: number]
}>()

const isAssistant = computed(() => props.message.role === 'assistant')

/**
 * 流式期间对 Markdown 重渲染做节流（80ms）。
 * 每个 token 都全量跑一遍 markdown-it 会让长回复在低端设备上掉帧，
 * 但完全等到结束再渲染又会失去“逐字输出”的观感。
 */
const renderedContent = ref(renderMarkdown(props.message.content))
let renderTimer: number | undefined

function renderNow(): void {
  renderedContent.value = renderMarkdown(props.message.content)
}

watch(
  () => [props.message.content, props.message.status] as const,
  ([, status]) => {
    if (status !== 'streaming') {
      if (renderTimer) {
        window.clearTimeout(renderTimer)
        renderTimer = undefined
      }
      renderNow()
      return
    }
    if (renderTimer) return
    renderTimer = window.setTimeout(() => {
      renderTimer = undefined
      renderNow()
    }, 80)
  },
)

onBeforeUnmount(() => {
  if (renderTimer) window.clearTimeout(renderTimer)
})

const timeLabel = computed(() =>
  new Date(props.message.createdAt).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  }),
)

const latencyText = computed(() => {
  const ms = props.message.metrics?.latency_ms
  if (!Number.isFinite(ms)) return ''
  return (ms as number) >= 1000 ? `${((ms as number) / 1000).toFixed(1)}s` : `${ms}ms`
})

const showMetrics = computed(
  () => isAssistant.value && props.message.status !== 'streaming' && Boolean(props.message.metrics),
)
</script>

<style scoped>
.msg {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  animation: rise-in 0.3s ease;
}

.msg.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--brand-soft);
  color: var(--brand);
  border: 1px solid var(--brand-border);
  margin-top: 22px;
}

.column {
  min-width: 0;
  display: flex;
  flex-direction: column;
  max-width: min(680px, 86%);
}

.msg.user .column {
  align-items: flex-end;
}

.meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  color: var(--text-tertiary);
  margin-bottom: 5px;
  padding: 0 2px;
}

.who {
  color: var(--text-secondary);
  font-weight: 600;
}

.live-tag {
  color: var(--brand);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.live-tag::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--brand);
  animation: blink 1s ease-in-out infinite;
}

.chip {
  padding: 0 6px;
  border-radius: var(--radius-pill);
  font-size: 10.5px;
  line-height: 16px;
}

.chip.neutral {
  background: var(--surface-muted);
  border: 1px solid var(--border);
}

.chip.danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.bubble {
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  font-size: 14px;
  line-height: 1.7;
  position: relative;
}

.bubble.assistant {
  background: var(--surface);
  border: 1px solid var(--border);
  border-top-left-radius: 6px;
  box-shadow: var(--shadow-sm);
}

.bubble.user {
  background: var(--user-bubble);
  color: #fff;
  border-top-right-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  box-shadow: 0 8px 20px rgba(76, 110, 245, 0.22);
}

.typing {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  height: 20px;
}

.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-tertiary);
  animation: bounce 1.2s infinite ease-in-out;
}

.typing span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes bounce {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.5;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

.caret {
  display: inline-block;
  width: 2px;
  height: 14px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--brand);
  animation: blink 1s steps(2, start) infinite;
}

.error-row {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.error-row svg {
  flex-shrink: 0;
  margin-top: 2px;
}

.transfer {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--warning-soft);
  border: 1px solid var(--warning-border);
}

.transfer-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--warning);
}

.transfer-text {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--text-secondary);
}

.transfer-ticket {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.transfer-ticket code {
  font-family: var(--font-mono);
  font-size: 11px;
}

.metrics {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  color: var(--text-tertiary);
}

.cached {
  color: var(--success);
  background: var(--success-soft);
  padding: 0 6px;
  border-radius: var(--radius-pill);
}

.rating {
  display: flex;
  gap: 2px;
  margin-left: auto;
}

.star {
  color: var(--border-strong);
  display: inline-flex;
  padding: 2px;
  transition: color 0.15s ease, transform 0.15s ease;
}

.star:hover {
  color: var(--warning);
  transform: scale(1.15);
}

.star.active {
  color: var(--warning);
}

@media (max-width: 640px) {
  .column {
    max-width: 100%;
  }
}
</style>