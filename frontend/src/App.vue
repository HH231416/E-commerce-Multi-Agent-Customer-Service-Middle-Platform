<template>
  <div class="app-root">
    <div v-if="outdated" class="update-bar" role="status">
      <span>工作台已发布新版本</span>
      <button type="button" @click="reload">立即刷新</button>
    </div>

    <ErrorBoundary>
      <RouterView v-slot="{ Component }">
        <Transition name="fade" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </ErrorBoundary>
  </div>
</template>

<script setup lang="ts">
import ErrorBoundary from './components/ErrorBoundary.vue'
import { useVersionCheck } from './composables/useVersionCheck'

const { outdated, reload } = useVersionCheck()
</script>

<style>
.app-root {
  min-height: 100%;
}

.update-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  height: 38px;
  background: linear-gradient(90deg, var(--brand) 0%, #7c5cff 100%);
  color: #fff;
  font-size: 12.5px;
}

.update-bar button {
  text-decoration: underline;
  font-weight: 600;
  color: #fff;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>