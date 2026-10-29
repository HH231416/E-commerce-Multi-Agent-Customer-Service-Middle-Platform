import { ref } from 'vue'

type Theme = 'light' | 'dark'

const STORAGE_KEY = 'cs.theme'

function currentTheme(): Theme {
  // index.html 的内联脚本已在首帧前写好，这里直接沿用，避免二次闪烁
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

const theme = ref<Theme>(currentTheme())

function apply(value: Theme): void {
  document.documentElement.dataset.theme = value
}

export function useTheme() {
  function toggle(): void {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    try {
      localStorage.setItem(STORAGE_KEY, theme.value)
    } catch {
      // 忽略存储失败
    }
    apply(theme.value)
  }

  return { theme, toggle }
}