import { defineConfig } from 'vitest/config'

// 仅对纯逻辑（SSE 解析、工具函数）做单元测试，组件交互留给 E2E
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})