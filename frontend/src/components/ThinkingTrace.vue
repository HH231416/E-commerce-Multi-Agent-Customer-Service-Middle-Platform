<template>
  <section class="trace" :class="{ 'is-live': streaming }">
    <button class="trace-head" type="button" @click="open = !open">
      <span class="pulse" :class="{ live: streaming }" aria-hidden="true"></span>
      <span class="trace-title">处理过程</span>
      <span class="trace-count">{{ steps.length }} 步</span>
      <svg class="chevron" :class="{ rotated: open }" width="14" height="14" viewBox="0 0 24 24" fill="none">
        <path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>

    <div v-show="open" class="trace-body">
      <ol class="steps">
        <li v-for="(step, index) in steps" :key="`${index}-${step}`">
          <span class="tick" aria-hidden="true">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none">
              <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </span>
          <span class="step-text">{{ step }}</span>
        </li>
      </ol>

      <div v-if="tools.length" class="tools">
        <span v-for="(tool, index) in tools" :key="`${index}-${tool.name}`" class="tool" :class="{ done: tool.done }">
          <span class="tool-dot" aria-hidden="true"></span>
          {{ toolLabel(tool.name) }}
        </span>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ToolCall } from '../types'

const props = defineProps<{
  steps: string[]
  tools: ToolCall[]
  streaming: boolean
  hasContent: boolean
}>()

const open = ref(true)

// 正文一旦开始输出就自动收起，把注意力交给回复本身；用户仍可手动展开
watch(
  () => props.hasContent,
  (value) => {
    if (value) open.value = false
  },
)

const TOOL_LABELS: Record<string, string> = {
  get_order: '查询订单',
  check_cancelable: '校验可取消',
  create_refund_draft: '核验退款条件',
  get_product_info: '查询商品',
  search_products: '搜索商品',
  get_policy: '检索政策',
  query_knowledge_base: '检索知识库',
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? name
}
</script>

<style scoped>
.trace {
  border: 1px solid var(--border);
  background: var(--surface-muted);
  border-radius: var(--radius-md);
  overflow: hidden;
  margin-bottom: 8px;
}

.trace.is-live {
  border-color: var(--brand-border);
}

.trace-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  color: var(--text-secondary);
  font-size: 12px;
  text-align: left;
}

.trace-head:hover {
  background: var(--surface-hover);
}

.pulse {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-tertiary);
  flex-shrink: 0;
}

.pulse.live {
  background: var(--brand);
  box-shadow: 0 0 0 0 var(--brand-glow);
  animation: ping 1.4s ease-out infinite;
}

@keyframes ping {
  0% {
    box-shadow: 0 0 0 0 var(--brand-glow);
  }
  70% {
    box-shadow: 0 0 0 6px transparent;
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
}

.trace-title {
  font-weight: 600;
  color: var(--text);
}

.trace-count {
  color: var(--text-tertiary);
}

.chevron {
  margin-left: auto;
  transition: transform 0.2s ease;
}

.chevron.rotated {
  transform: rotate(180deg);
}

.trace-body {
  padding: 0 12px 10px;
  animation: rise-in 0.2s ease;
}

.steps {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.steps li {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  font-size: 12.5px;
  color: var(--text-secondary);
  animation: rise-in 0.22s ease;
}

.tick {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--brand-soft);
  color: var(--brand);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.step-text {
  line-height: 1.5;
}

.tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.tool {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 9px;
  border-radius: var(--radius-pill);
  background: var(--surface);
  border: 1px solid var(--border);
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.tool.done {
  color: var(--success);
  border-color: var(--success-border);
}

.tool-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand);
  animation: blink 1s ease-in-out infinite;
}

.tool.done .tool-dot {
  background: var(--success);
  animation: none;
}
</style>