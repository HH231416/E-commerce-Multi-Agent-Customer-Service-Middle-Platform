/// <reference types="vite/client" />

declare const __BUILD_ID__: string

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_ERROR_ENDPOINT?: string
  readonly VITE_VERSION_POLL_MS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}