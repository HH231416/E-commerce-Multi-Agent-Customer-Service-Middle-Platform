import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildHeaders, endSession, fetchHistory, setUnauthorizedHandler, startSession } from './client'
import type { Identity } from '../types'

const identity: Identity = {
  userId: 'u_1',
  tenantId: 'demo',
  roles: 'customer',
  token: '',
  mode: 'debug',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('鉴权头', () => {
  it('有令牌时只发送 Bearer，不发送调试身份头', () => {
    const headers = buildHeaders({ ...identity, token: 'jwt-token', mode: 'bearer' })
    expect(headers.Authorization).toBe('Bearer jwt-token')
    expect(headers['X-Debug-User-Id']).toBeUndefined()
  })

  it('开发环境调试模式下回退到调试身份头', () => {
    const headers = buildHeaders(identity)
    expect(headers['X-Debug-User-Id']).toBe('u_1')
    expect(headers['X-Debug-Tenant-Id']).toBe('demo')
  })

  it('Cookie 会话模式不发送任何身份头，由浏览器携带 Cookie', () => {
    const headers = buildHeaders({ ...identity, mode: 'cookie' })
    expect(headers.Authorization).toBeUndefined()
    expect(headers['X-Debug-User-Id']).toBeUndefined()
  })
})

describe('会话换取', () => {
  it('携带 Bearer 调用会话接口并返回身份信息', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ user_id: 'u_1', tenant_id: 'demo', roles: ['customer'], expires_in: 3600 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const info = await startSession('jwt-token')
    expect(info.user_id).toBe('u_1')
    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>
    const [url, init] = calls[0]
    expect(url).toContain('/api/auth/session')
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer jwt-token')
  })

  it('令牌无效时抛出错误且不进入工作台', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: '访问令牌无效或已过期' }), { status: 401 })),
    )
    await expect(startSession('bad-token')).rejects.toThrow('访问令牌无效或已过期')
  })

  it('登出调用 DELETE 并携带同源凭据', async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await endSession()

    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>
    expect(calls[0][0]).toContain('/api/auth/session')
    expect(calls[0][1].method).toBe('DELETE')
    expect(calls[0][1].credentials).toBe('same-origin')
  })
})

describe('401 处理', () => {
  it('触发登出回调并抛出可展示的错误', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: '访问令牌无效或已过期' }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    )

    await expect(fetchHistory({ identity, userId: 'u_1' })).rejects.toThrow('访问令牌无效或已过期')
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it('其他错误不触发登出', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })))

    await expect(fetchHistory({ identity, userId: 'u_1' })).rejects.toThrow('后端服务暂不可用')
    expect(onUnauthorized).not.toHaveBeenCalled()
  })
})