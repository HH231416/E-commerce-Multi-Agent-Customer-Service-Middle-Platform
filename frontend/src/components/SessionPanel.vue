<template>
  <aside class="panel">
    <div class="panel-head">
      <div class="brand">
        <div class="logo" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <path
              d="M4 12a8 8 0 1116 0v5a3 3 0 01-3 3h-2"
              stroke="currentColor"
              stroke-width="1.9"
              stroke-linecap="round"
            />
            <circle cx="9" cy="12" r="1.4" fill="currentColor" />
            <circle cx="15" cy="12" r="1.4" fill="currentColor" />
          </svg>
        </div>
        <div class="brand-text">
          <strong>智能客服工作台</strong>
          <span>Multi-Agent Service Console</span>
        </div>
      </div>
      <button class="icon-btn close" type="button" title="收起" @click="$emit('close')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
          <path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>
    </div>

    <div class="panel-body">
      <section class="card session">
        <div class="session-top">
          <div class="avatar-lg">{{ initial }}</div>
          <div class="session-id">
            <span class="label">当前会话用户</span>
            <strong>{{ identity.userId }}</strong>
          </div>
        </div>
        <div class="session-meta">
          <span class="pill">租户 {{ identity.tenantId }}</span>
          <span class="pill" :data-tone="authMode === 'debug' ? 'warning' : 'success'">
            {{ authLabel }}
          </span>
        </div>
      </section>

      <section class="block">
        <h3>常用问题</h3>
        <div class="prompts">
          <button v-for="item in prompts" :key="item" type="button" class="prompt" @click="$emit('pick', item)">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none">
              <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span>{{ item }}</span>
          </button>
        </div>
      </section>

      <section class="block">
        <h3>可调度能力</h3>
        <ul class="caps">
          <li v-for="cap in capabilities" :key="cap.name">
            <span class="cap-dot" :style="{ background: cap.color }" aria-hidden="true"></span>
            <div>
              <span class="cap-name">{{ cap.name }}</span>
              <span class="cap-desc">{{ cap.desc }}</span>
            </div>
          </li>
        </ul>
      </section>

      <section class="block">
        <h3>本次会话</h3>
        <div class="stats">
          <div class="stat">
            <span class="stat-value">{{ stats.turns }}</span>
            <span class="stat-label">轮次</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ stats.tokens }}</span>
            <span class="stat-label">Tokens</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ averageLatency ? `${(averageLatency / 1000).toFixed(1)}s` : '—' }}</span>
            <span class="stat-label">平均耗时</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ stats.cacheHits }}</span>
            <span class="stat-label">缓存命中</span>
          </div>
        </div>
      </section>
    </div>

    <div class="panel-foot">
      <span class="foot-note">多智能体编排 · RAG 知识库 · 人工接管</span>
      <button class="link" type="button" @click="$emit('logout')">退出登录</button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Identity, SessionStats } from '../types'

const props = defineProps<{
  identity: Identity
  stats: SessionStats
  averageLatency: number
  authMode: string
}>()

defineEmits<{
  pick: [text: string]
  close: []
  logout: []
}>()

const prompts = [
  '我的订单怎么还没到？',
  '这个商品支持七天无理由退货吗？',
  '优惠券为什么用不了？',
  '我想申请退款，需要什么条件？',
]

const capabilities = [
  { name: '订单 Agent', desc: '查单、取消、退款核验', color: '#4c6ef5' },
  { name: '物流 Agent', desc: '轨迹查询与时效解释', color: '#12b886' },
  { name: '商品 Agent', desc: '规格、库存与推荐', color: '#f59f00' },
  { name: '知识库 RAG', desc: '售后政策与规则检索', color: '#7c5cff' },
]

const initial = computed(() => (props.identity.userId || 'U').slice(0, 1).toUpperCase())

const AUTH_LABELS: Record<string, string> = {
  debug: '调试身份',
  cookie: '会话 Cookie',
  bearer: 'JWT 令牌',
}

const authLabel = computed(() => AUTH_LABELS[props.authMode] ?? props.authMode)
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--surface);
  border-right: 1px solid var(--border);
  min-height: 0;
}

.panel-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px;
  border-bottom: 1px solid var(--border);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.logo {
  width: 36px;
  height: 36px;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, var(--brand) 0%, #7c5cff 100%);
  color: #fff;
  flex-shrink: 0;
  box-shadow: 0 6px 16px rgba(76, 110, 245, 0.3);
}

.brand-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.brand-text strong {
  font-size: 14px;
  letter-spacing: 0.01em;
}

.brand-text span {
  font-size: 10.5px;
  color: var(--text-tertiary);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.close {
  margin-left: auto;
  display: none;
}

.panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.card.session {
  padding: 14px;
  border-radius: var(--radius-md);
  background: var(--surface-muted);
  border: 1px solid var(--border);
}

.session-top {
  display: flex;
  align-items: center;
  gap: 10px;
}

.avatar-lg {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 650;
  color: #fff;
  background: linear-gradient(135deg, #4c6ef5, #7c5cff);
  flex-shrink: 0;
}

.session-id {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.session-id .label {
  font-size: 11px;
  color: var(--text-tertiary);
}

.session-id strong {
  font-size: 14px;
  font-family: var(--font-mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-meta {
  display: flex;
  gap: 6px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.block h3 {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

.prompts {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.prompt {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 10px;
  border-radius: 10px;
  border: 1px solid transparent;
  color: var(--text-secondary);
  font-size: 12.5px;
  text-align: left;
  transition: background 0.16s ease, border-color 0.16s ease, color 0.16s ease;
}

.prompt svg {
  color: var(--text-tertiary);
  flex-shrink: 0;
  transition: color 0.16s ease, transform 0.16s ease;
}

.prompt:hover {
  background: var(--brand-soft);
  border-color: var(--brand-border);
  color: var(--text);
}

.prompt:hover svg {
  color: var(--brand);
  transform: translateX(2px);
}

.prompt span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.caps {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.caps li {
  display: flex;
  gap: 9px;
  align-items: flex-start;
}

.cap-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-top: 6px;
  flex-shrink: 0;
  box-shadow: 0 0 0 3px var(--halo);
}

.cap-name {
  display: block;
  font-size: 12.5px;
  font-weight: 550;
  color: var(--text);
}

.cap-desc {
  display: block;
  font-size: 11.5px;
  color: var(--text-tertiary);
  line-height: 1.45;
}

.stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.stat {
  padding: 9px 10px;
  border-radius: 10px;
  background: var(--surface-muted);
  border: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-value {
  font-size: 15px;
  font-weight: 650;
  font-family: var(--font-mono);
  color: var(--text);
}

.stat-label {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.panel-foot {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.foot-note {
  font-size: 10.5px;
  color: var(--text-tertiary);
  line-height: 1.4;
}

.link {
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.link:hover {
  color: var(--danger);
}

@media (max-width: 1000px) {
  .panel {
    border-right: none;
  }
  .close {
    display: inline-flex;
  }
}
</style>