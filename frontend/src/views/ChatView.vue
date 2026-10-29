<template>
  <div class="shell" :class="{ 'panel-open': panelOpen }">
    <SessionPanel
      ref="sidebarRef"
      class="sidebar"
      :identity="state"
      :stats="stats"
      :average-latency="averageLatency"
      :auth-mode="authMode"
      @pick="pickPrompt"
      @close="closePanel"
      @logout="logout"
    />

    <div class="backdrop" @click="panelOpen = false"></div>

    <section class="main">
      <header class="topbar">
        <button ref="menuButton" class="icon-btn menu" type="button" title="会话信息" @click="panelOpen = true">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
            <path d="M4 6h16M4 12h16M4 18h10" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>

        <div class="title-block">
          <h1>会话工作台</h1>
          <div class="sub">
            <span class="dot" :class="{ live: streaming }" aria-hidden="true"></span>
            {{ statusText }}
            <span class="sep">·</span>
            <span class="mono">{{ state.userId }}</span>
          </div>
        </div>

        <div class="top-actions">
          <button class="icon-btn" type="button" title="刷新会话历史" :disabled="streaming" @click="refresh">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path
                d="M20 11a8 8 0 10-2.6 5.9M20 5v6h-6"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
          <button class="icon-btn" type="button" :title="theme === 'dark' ? '切换到浅色' : '切换到深色'" @click="toggle">
            <svg v-if="theme === 'dark'" width="16" height="16" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="2" />
              <path
                d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              />
            </svg>
            <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path
                d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </button>
        </div>
      </header>

      <div
        ref="scroller"
        class="scroll"
        role="log"
        aria-live="polite"
        aria-relevant="additions text"
        aria-label="会话消息"
        @scroll="onScroll"
      >
        <div class="thread">
          <div v-if="loadingHistory" class="skeleton">
            <div v-for="index in 3" :key="index" class="sk-row" :class="index % 2 === 0 ? 'right' : 'left'"></div>
          </div>

          <template v-else>
            <div v-if="loadError" class="alert">
              <span>{{ loadError }}</span>
              <button type="button" @click="refresh">重试</button>
            </div>

            <div v-if="!messages.length" class="empty">
              <div class="empty-art" aria-hidden="true">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
                  <path d="M4 12a8 8 0 1116 0v5a3 3 0 01-3 3h-2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
                  <circle cx="9" cy="12" r="1.4" fill="currentColor" />
                  <circle cx="15" cy="12" r="1.4" fill="currentColor" />
                </svg>
              </div>
              <h2>您好，我是智能客服</h2>
              <p>订单、物流、退换货、优惠券问题都可以直接问我。</p>
              <div class="suggestions">
                <button v-for="item in prompts" :key="item" type="button" @click="pickPrompt(item)">
                  {{ item }}
                </button>
              </div>
            </div>

            <MessageItem
              v-for="message in messages"
              :key="message.id"
              :message="message"
              @rate="rateMessage(message, $event)"
            />
          </template>
        </div>
      </div>

      <button v-if="!stick && messages.length" class="jump" type="button" @click="scrollToBottom(true)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
          <path d="M12 5v14m0 0l-6-6m6 6l6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        最新消息
      </button>

      <footer class="composer-area">
        <div class="composer-inner">
          <ComposerBox
            ref="composerRef"
            :streaming="streaming"
            :disabled="loadingHistory"
            @send="handleSend"
            @stop="stop"
          />
        </div>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SessionPanel from '../components/SessionPanel.vue'
import MessageItem from '../components/MessageItem.vue'
import ComposerBox from '../components/ComposerBox.vue'
import type { ChatMessage } from '../types'
import { endSession } from '../api/client'
import { useAuth } from '../composables/useAuth'
import { useTheme } from '../composables/useTheme'
import { useChat } from '../composables/useChat'

const router = useRouter()
const { state, authMode, signOut } = useAuth()
const { theme, toggle } = useTheme()

const {
  messages,
  streaming,
  loadingHistory,
  loadError,
  stats,
  averageLatency,
  loadHistory,
  send,
  stop,
  rate,
} = useChat()

const composerRef = ref<InstanceType<typeof ComposerBox> | null>(null)
const sidebarRef = ref<InstanceType<typeof SessionPanel> | null>(null)
const menuButton = ref<HTMLButtonElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
const panelOpen = ref(false)
const stick = ref(true)

const prompts = [
  '我的订单怎么还没到？',
  '这个商品支持七天无理由退货吗？',
  '优惠券为什么用不了？',
  '我想申请退款，需要什么条件？',
]

const statusText = computed(() => (streaming.value ? 'AI 正在生成回复' : '已连接多智能体网关'))

// 只观察“内容量”的变化，避免深度监听整棵消息树带来的性能开销
const signature = computed(() =>
  messages.value.reduce(
    (total, message) => total + message.content.length + message.steps.length * 2 + message.tools.length,
    0,
  ),
)

watch(signature, () => scrollToBottom())

function onScroll(): void {
  const el = scroller.value
  if (!el) return
  stick.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function scrollToBottom(force = false): void {
  void nextTick(() => {
    const el = scroller.value
    if (!el) return
    if (!force && !stick.value) return
    el.scrollTo({ top: el.scrollHeight, behavior: force ? 'smooth' : 'auto' })
  })
}

function handleSend(text: string): void {
  stick.value = true
  void send(text)
  scrollToBottom(true)
}

function pickPrompt(text: string): void {
  closePanel()
  composerRef.value?.setText(text)
}

function closePanel(): void {
  panelOpen.value = false
  // 关闭后把焦点还给触发按钮，避免键盘用户丢失位置
  void nextTick(() => menuButton.value?.focus())
}

function refresh(): void {
  if (streaming.value) return
  stick.value = true
  void loadHistory()
}

async function logout(): Promise<void> {
  // 先清服务端会话 Cookie，再清本地身份
  await endSession()
  signOut()
  await router.push({ name: 'login' })
}

function rateMessage(message: ChatMessage, score: number): void {
  void rate(message, score)
}

const MOBILE_QUERY = '(max-width: 1000px)'
const isMobile = (): boolean => window.matchMedia(MOBILE_QUERY).matches

function focusableIn(root: HTMLElement): HTMLElement[] {
  const selector =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  return Array.from(root.querySelectorAll<HTMLElement>(selector)).filter((el) => el.offsetParent !== null)
}

function sidebarEl(): HTMLElement | null {
  const instance = sidebarRef.value as unknown as { $el?: HTMLElement } | null
  return instance?.$el ?? null
}

// 抽屉打开时：Esc 关闭、Tab 焦点循环、锁定背景滚动
function onGlobalKeydown(event: KeyboardEvent): void {
  if (!panelOpen.value || !isMobile()) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closePanel()
    return
  }
  if (event.key !== 'Tab') return
  const root = sidebarEl()
  if (!root) return
  const items = focusableIn(root)
  if (!items.length) return
  const first = items[0]
  const last = items[items.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

watch(panelOpen, (open) => {
  if (!isMobile()) return
  document.body.style.overflow = open ? 'hidden' : ''
  if (open) {
    void nextTick(() => {
      const root = sidebarEl()
      if (root) focusableIn(root)[0]?.focus()
    })
  }
})

function onResize(): void {
  if (!isMobile()) {
    panelOpen.value = false
    document.body.style.overflow = ''
  }
}

onMounted(() => {
  void loadHistory()
  window.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('resize', onResize)
  document.body.style.overflow = ''
})
</script>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 288px minmax(0, 1fr);
  height: 100vh;
  height: 100dvh;
}

.sidebar {
  min-height: 0;
}

.backdrop {
  display: none;
}

.main {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 12px;
  height: 64px;
  padding: 0 clamp(12px, 2.4vw, 24px);
  border-bottom: 1px solid var(--border);
  background: var(--glass);
  backdrop-filter: blur(14px);
  flex-shrink: 0;
}

.menu {
  display: none;
}

.title-block {
  min-width: 0;
}

.title-block h1 {
  font-size: 15px;
  font-weight: 650;
  line-height: 1.3;
}

.sub {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
  flex-shrink: 0;
}

.dot.live {
  background: var(--brand);
  animation: blink 1s ease-in-out infinite;
}

.sep {
  opacity: 0.5;
}

.mono {
  font-family: var(--font-mono);
}

.top-actions {
  margin-left: auto;
  display: flex;
  gap: 2px;
}

.icon-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.scroll {
  flex: 1;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 22px clamp(14px, 4vw, 40px) 6px;
  scroll-behavior: smooth;
}

.thread {
  max-width: 860px;
  margin: 0 auto;
}

.skeleton {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding-top: 12px;
}

.sk-row {
  height: 62px;
  width: 62%;
  border-radius: var(--radius-lg);
  background: linear-gradient(90deg, var(--surface-muted) 25%, var(--surface-hover) 37%, var(--surface-muted) 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}

.sk-row.right {
  align-self: flex-end;
  width: 44%;
}

@keyframes shimmer {
  0% {
    background-position: 100% 50%;
  }
  100% {
    background-position: 0 50%;
  }
}

.alert {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12.5px;
  margin-bottom: 16px;
}

.alert button {
  margin-left: auto;
  font-size: 12px;
  color: var(--danger);
  text-decoration: underline;
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: clamp(28px, 8vh, 72px) 16px 24px;
  animation: rise-in 0.35s ease;
}

.empty-art {
  width: 58px;
  height: 58px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--brand-soft);
  color: var(--brand);
  border: 1px solid var(--brand-border);
  margin-bottom: 16px;
}

.empty h2 {
  font-size: 17px;
  margin-bottom: 6px;
}

.empty p {
  font-size: 13px;
  color: var(--text-secondary);
  max-width: 42ch;
}

.suggestions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 20px;
}

.suggestions button {
  padding: 8px 13px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 12.5px;
  color: var(--text-secondary);
  transition: border-color 0.16s ease, color 0.16s ease, background 0.16s ease, transform 0.16s ease;
  box-shadow: var(--shadow-sm);
}

.suggestions button:hover {
  border-color: var(--brand-border);
  color: var(--brand);
  background: var(--brand-soft);
  transform: translateY(-1px);
}

.jump {
  position: absolute;
  bottom: 128px;
  left: 50%;
  transform: translateX(-50%);
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 12px;
  border-radius: var(--radius-pill);
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-md);
  font-size: 11.5px;
  color: var(--text-secondary);
  z-index: 5;
  animation: rise-in 0.2s ease;
}

.jump:hover {
  color: var(--brand);
}

.composer-area {
  padding: 10px clamp(14px, 4vw, 40px) max(16px, env(safe-area-inset-bottom));
  flex-shrink: 0;
}

.composer-inner {
  max-width: 860px;
  margin: 0 auto;
}

@media (max-width: 1000px) {
  .shell {
    grid-template-columns: minmax(0, 1fr);
  }

  .sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    width: 288px;
    max-width: 86vw;
    z-index: 40;
    transform: translateX(-102%);
    transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
    box-shadow: var(--shadow-lg);
  }

  .shell.panel-open .sidebar {
    transform: translateX(0);
  }

  .backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 30;
    background: rgba(9, 11, 17, 0.45);
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s ease;
  }

  .shell.panel-open .backdrop {
    opacity: 1;
    pointer-events: auto;
  }

  .menu {
    display: inline-flex;
  }
}
</style>