<template>
  <div class="composer">
    <div class="box" :class="{ 'is-streaming': streaming }">
      <textarea
        ref="textareaRef"
        v-model="draft"
        rows="1"
        :maxlength="maxLength"
        :disabled="disabled"
        placeholder="描述您的问题，例如：我的订单为什么还没发货？"
        @input="autoGrow"
        @keydown="onKeydown"
      ></textarea>

      <div class="actions">
        <button v-if="streaming" class="stop" type="button" title="停止生成" @click="$emit('stop')">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
          <span>停止</span>
        </button>
        <button v-else class="send" type="button" :disabled="!canSend" title="发送" @click="submit">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 12l16-8-6 8 6 8-16-8z"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
          </svg>
        </button>
      </div>
    </div>

    <div class="hint">
      <span>Enter 发送 · Shift + Enter 换行</span>
      <span v-if="draft.length > maxLength * 0.8" class="counter" :class="{ full: draft.length >= maxLength }">
        {{ draft.length }} / {{ maxLength }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    streaming?: boolean
    disabled?: boolean
    maxLength?: number
  }>(),
  { streaming: false, disabled: false, maxLength: 4000 },
)

const emit = defineEmits<{
  send: [text: string]
  stop: []
}>()

const draft = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)

const canSend = computed(() => !props.disabled && !props.streaming && draft.value.trim().length > 0)

function autoGrow(): void {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 168)}px`
}

function submit(): void {
  if (!canSend.value) return
  emit('send', draft.value)
  draft.value = ''
  nextTick(autoGrow)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  submit()
}

function setText(text: string): void {
  draft.value = text
  nextTick(() => {
    autoGrow()
    textareaRef.value?.focus()
  })
}

defineExpose({ setText, focus: () => textareaRef.value?.focus() })
</script>

<style scoped>
.composer {
  width: 100%;
}

.box {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 10px 10px 10px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.box:focus-within {
  border-color: var(--brand-border);
  box-shadow: 0 0 0 4px var(--brand-ring);
}

.box.is-streaming {
  border-color: var(--brand-border);
}

textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  min-height: 24px;
  max-height: 168px;
  padding: 5px 0;
  line-height: 1.6;
  font-size: 14px;
}

textarea::placeholder {
  color: var(--text-tertiary);
}

.actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.send {
  width: 36px;
  height: 36px;
  border-radius: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--brand) 0%, #7c5cff 100%);
  color: #fff;
  transition: transform 0.18s ease, opacity 0.18s ease, box-shadow 0.18s ease;
  box-shadow: 0 6px 16px rgba(76, 110, 245, 0.3);
}

.send:hover:not(:disabled) {
  transform: translateY(-1px);
}

.send:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  box-shadow: none;
}

.stop {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 36px;
  padding: 0 12px;
  border-radius: 12px;
  border: 1px solid var(--border-strong);
  color: var(--text-secondary);
  font-size: 12.5px;
  background: var(--surface);
}

.stop:hover {
  background: var(--surface-hover);
  color: var(--text);
}

.hint {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
  padding: 0 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.counter.full {
  color: var(--danger);
}

@media (max-width: 640px) {
  .hint span:first-child {
    display: none;
  }
  .hint {
    justify-content: flex-end;
  }
}

/* iOS Safari 对字号小于 16px 的输入框会强制缩放页面 */
@media (max-width: 768px) {
  textarea {
    font-size: 16px;
  }
}
</style>