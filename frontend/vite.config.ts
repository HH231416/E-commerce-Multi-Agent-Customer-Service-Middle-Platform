import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建 ID 用于前端版本探测：产物里的 __BUILD_ID__ 与 version.json 比对，
// 不一致说明页面加载的是旧资源，提示用户刷新。
const buildId = Date.now().toString(36)

// 开发态由 Vite 代理 /api 到 FastAPI，避免改后端 CORS；
// 构建产物由 FastAPI 以 /app 静态目录托管，故 build 时把 base 设为 /app/。
export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/app/' : '/',
  define: {
    __BUILD_ID__: JSON.stringify(buildId),
  },
  plugins: [
    vue(),
    {
      name: 'emit-version-file',
      apply: 'build',
      generateBundle() {
        this.emitFile({
          type: 'asset',
          fileName: 'version.json',
          source: JSON.stringify({ buildId }),
        })
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
        // 保留原始 Host：后端 Cookie 鉴权会用 Origin 与 Host 做同源校验，
        // 改写 Host 会让本地开发被判定为跨站请求。
        changeOrigin: false,
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // 目标基线：现代常青浏览器；低于该基线不做降级承诺
    target: 'es2020',
    cssTarget: 'safari16',
  },
}))