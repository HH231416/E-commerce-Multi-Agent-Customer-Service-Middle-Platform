import { computed, reactive, watch } from 'vue'
import type { AuthMode, Identity } from '../types'

const PROFILE_KEY = 'cs.profile'

interface Profile {
  userId: string
  tenantId: string
  roles: string
  mode: AuthMode
}

function safeGet(storage: Storage, key: string): string | null {
  try {
    return storage.getItem(key)
  } catch {
    return null
  }
}

function safeSet(storage: Storage, key: string, value: string): void {
  try {
    storage.setItem(key, value)
  } catch {
    // 隐私模式下写入失败不阻塞使用
  }
}

function loadProfile(): Profile | null {
  const raw = safeGet(localStorage, PROFILE_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<Profile>
    if (parsed && typeof parsed.userId === 'string' && parsed.userId) {
      return {
        userId: parsed.userId,
        tenantId: parsed.tenantId || 'demo',
        roles: parsed.roles || 'customer',
        mode: parsed.mode === 'cookie' ? 'cookie' : 'debug',
      }
    }
  } catch {
    // 本地存储损坏时按未登录处理
  }
  return null
}

const saved = loadProfile()

// 身份单例：登录页写入，API 层与工作台共用。
// 注意：这里不保存令牌，令牌只在换取会话的瞬间存在于内存中。
const state = reactive<Identity>({
  userId: saved?.userId ?? '',
  tenantId: saved?.tenantId ?? 'demo',
  roles: saved?.roles ?? 'customer',
  token: '',
  mode: saved?.mode ?? 'debug',
})

watch(
  () => ({ userId: state.userId, tenantId: state.tenantId, roles: state.roles, mode: state.mode }),
  (profile) => {
    if (profile.userId) safeSet(localStorage, PROFILE_KEY, JSON.stringify(profile))
    else localStorage.removeItem(PROFILE_KEY)
  },
  { deep: true },
)

export function useAuth() {
  const authMode = computed(() => state.mode)

  function signIn(input: { userId: string; tenantId: string; roles: string; token: string }): void {
    state.userId = input.userId.trim()
    state.tenantId = (input.tenantId || 'demo').trim()
    state.roles = (input.roles || 'customer').trim()
    state.token = (input.token || '').trim()
    // 有令牌时先按 Bearer 访问，调用方换取 Cookie 后应立刻 completeSession()
    state.mode = state.token ? 'bearer' : 'debug'
  }

  /** 会话 Cookie 已建立：丢弃内存中的令牌 */
  function completeSession(): void {
    state.token = ''
    state.mode = 'cookie'
  }

  function signOut(): void {
    state.userId = ''
    state.token = ''
    state.mode = 'debug'
  }

  return { state, authMode, signIn, completeSession, signOut }
}