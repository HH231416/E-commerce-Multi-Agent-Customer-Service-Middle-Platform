<template>
  <div class="login-page">
    <div class="card">
      <section class="hero">
        <div class="hero-brand">
          <div class="logo">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
              <path d="M4 12a8 8 0 1116 0v5a3 3 0 01-3 3h-2" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" />
              <circle cx="9" cy="12" r="1.4" fill="currentColor" />
              <circle cx="15" cy="12" r="1.4" fill="currentColor" />
            </svg>
          </div>
          <div>
            <strong>电商多智能体客服中台</strong>
            <span>Multi-Agent Service Console</span>
          </div>
        </div>

        <h1>让每一次咨询<br />都落到正确的智能体</h1>
        <p class="hero-desc">网关统一意图路由，订单、物流、商品与售后知识库各自处理，复杂问题自动转人工。</p>

        <ul class="features">
          <li v-for="item in features" :key="item.title">
            <span class="tick">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none">
                <path d="M20 6L9 17l-5-5" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            <div>
              <strong>{{ item.title }}</strong>
              <span>{{ item.desc }}</span>
            </div>
          </li>
        </ul>

        <div class="hero-foot">SSE 流式响应 · RAG 混合检索 · 人工接管工单</div>
      </section>

      <section class="form-side">
        <div class="form-head">
          <h2>进入工作台</h2>
          <p>输入用户 ID 以恢复该用户的会话上下文。</p>
        </div>

        <form @submit.prevent="submit">
          <p v-if="expired" class="notice">登录状态已失效，请重新进入工作台。</p>

          <label class="field">
            <span>用户 ID</span>
            <input v-model.trim="userId" type="text" maxlength="128" placeholder="例如：5588" autocomplete="off" />
          </label>

          <p v-if="error" class="error">{{ error }}</p>

          <details class="advanced">
            <summary>身份与租户设置</summary>
            <div class="advanced-body">
              <label class="field">
                <span>租户 ID</span>
                <input v-model.trim="tenantId" type="text" maxlength="128" placeholder="demo" autocomplete="off" />
              </label>
              <label class="field">
                <span>角色</span>
                <select v-model="roles">
                  <option value="customer">customer（消费者）</option>
                  <option value="support">support（人工客服）</option>
                  <option value="admin">admin（管理员）</option>
                </select>
              </label>
              <label class="field">
                <span>访问令牌（可选）</span>
                <input v-model.trim="token" type="password" placeholder="留空则使用调试身份头" autocomplete="off" />
              </label>
              <p class="hint">令牌只用于换取 HttpOnly 会话 Cookie，不会留在浏览器存储中；本地开发可留空。</p>
            </div>
          </details>

          <button class="btn btn-primary submit" type="submit" :disabled="!canSubmit || submitting">
            {{ submitting ? '正在建立会话…' : '进入工作台' }}
            <svg v-if="!submitting" width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </form>

        <p class="foot">会话记录仅对当前身份可见，租户之间相互隔离。</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { startSession } from '../api/client'
import { useAuth } from '../composables/useAuth'

const route = useRoute()
const router = useRouter()
const { state, signIn, completeSession } = useAuth()

const expired = computed(() => route.query.reason === 'expired')

const userId = ref(state.userId)
const tenantId = ref(state.tenantId)
const roles = ref(state.roles)
const token = ref(state.token)
const error = ref('')
const submitting = ref(false)

// 与后端 _IDENTIFIER 规则保持一致
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/

const canSubmit = computed(() => userId.value.length > 0)

const features = [
  { title: '意图路由', desc: '网关识别问题类型并分派到对应 Agent' },
  { title: '流式响应', desc: '逐字返回回复，处理过程实时可见' },
  { title: '风险兜底', desc: '情绪识别与退款核验，必要时转人工' },
]

async function submit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return
  if (!IDENTIFIER.test(userId.value)) {
    error.value = '用户 ID 只能包含字母、数字与 _ . : - ，且需以字母或数字开头'
    return
  }
  if (tenantId.value && !IDENTIFIER.test(tenantId.value)) {
    error.value = '租户 ID 格式不正确'
    return
  }

  const rawToken = token.value.trim()
  if (!rawToken && !import.meta.env.DEV) {
    error.value = '生产环境必须填写网关签发的访问令牌'
    return
  }

  error.value = ''
  submitting.value = true
  try {
    if (rawToken) {
      // 校验并换取 HttpOnly 会话 Cookie；失败则停留在登录页
      await startSession(rawToken)
    }
    signIn({ userId: userId.value, tenantId: tenantId.value, roles: roles.value, token: rawToken })
    if (rawToken) completeSession()
    await router.push({ name: 'chat' })
  } catch (err) {
    error.value = (err as Error)?.message ?? '登录失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: clamp(16px, 4vw, 48px);
}

.card {
  width: min(940px, 100%);
  display: grid;
  grid-template-columns: 1.05fr 0.95fr;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 24px;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  animation: rise-in 0.4s ease;
}

.hero {
  position: relative;
  padding: clamp(24px, 3.4vw, 40px);
  background: linear-gradient(150deg, #3b5bdb 0%, #4c6ef5 42%, #7c5cff 100%);
  color: #fff;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.hero::after {
  content: '';
  position: absolute;
  width: 320px;
  height: 320px;
  right: -120px;
  bottom: -140px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
  filter: blur(2px);
}

.hero-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 34px;
}

.logo {
  width: 38px;
  height: 38px;
  border-radius: 11px;
  background: rgba(255, 255, 255, 0.18);
  border: 1px solid rgba(255, 255, 255, 0.28);
  display: flex;
  align-items: center;
  justify-content: center;
}

.hero-brand strong {
  display: block;
  font-size: 14px;
}

.hero-brand span {
  display: block;
  font-size: 10.5px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  opacity: 0.72;
}

.hero h1 {
  font-size: clamp(22px, 2.4vw, 28px);
  line-height: 1.4;
  letter-spacing: -0.01em;
  margin-bottom: 12px;
}

.hero-desc {
  font-size: 13px;
  line-height: 1.7;
  opacity: 0.82;
  max-width: 34ch;
}

.features {
  list-style: none;
  margin: 28px 0 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.features li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.tick {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.features strong {
  display: block;
  font-size: 13px;
  font-weight: 600;
}

.features span {
  display: block;
  font-size: 12px;
  opacity: 0.78;
  line-height: 1.5;
}

.hero-foot {
  margin-top: auto;
  padding-top: 30px;
  font-size: 11px;
  letter-spacing: 0.03em;
  opacity: 0.7;
}

.form-side {
  padding: clamp(24px, 3.4vw, 40px);
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.form-head h2 {
  font-size: 20px;
  margin-bottom: 6px;
}

.form-head p {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 24px;
}

.field {
  display: block;
  margin-bottom: 14px;
}

.field > span {
  display: block;
  font-size: 12px;
  font-weight: 550;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.field input,
.field select {
  width: 100%;
  height: 42px;
  padding: 0 12px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-strong);
  background: var(--surface);
  font-size: 14px;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.field input::placeholder {
  color: var(--text-tertiary);
}

.field input:focus,
.field select:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 4px var(--brand-ring);
}

.error {
  font-size: 12px;
  color: var(--danger);
  background: var(--danger-soft);
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  margin-bottom: 12px;
}

.notice {
  font-size: 12px;
  color: var(--warning);
  background: var(--warning-soft);
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  margin-bottom: 12px;
}

.advanced {
  margin: 4px 0 18px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 0 12px;
  background: var(--surface-muted);
}

.advanced summary {
  cursor: pointer;
  font-size: 12.5px;
  color: var(--text-secondary);
  padding: 10px 0;
  list-style: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.advanced summary::-webkit-details-marker {
  display: none;
}

.advanced summary::after {
  content: '+';
  font-size: 15px;
  color: var(--text-tertiary);
}

.advanced[open] summary::after {
  content: '−';
}

.advanced-body {
  padding-bottom: 8px;
}

.advanced .field {
  margin-bottom: 10px;
}

.advanced .field input,
.advanced .field select {
  height: 36px;
  font-size: 13px;
  background: var(--surface);
}

.hint {
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.submit {
  width: 100%;
  height: 44px;
  font-size: 14px;
}

.foot {
  margin-top: 16px;
  font-size: 11px;
  color: var(--text-tertiary);
  text-align: center;
}

@media (max-width: 820px) {
  .card {
    grid-template-columns: 1fr;
  }
  .hero {
    padding: 24px;
  }
  .hero-brand {
    margin-bottom: 20px;
  }
  .features,
  .hero-desc {
    display: none;
  }
  .hero-foot {
    display: none;
  }
}

/* iOS Safari 对字号小于 16px 的输入框会强制缩放页面 */
@media (max-width: 768px) {
  .field input,
  .field select {
    font-size: 16px;
  }
}
</style>