<template>
  <div class="section">
    <h3 class="section-title">外观偏好</h3>
    <div class="setting-row">
      <div>
        <div class="setting-label">深色模式</div>
        <div class="setting-desc">切换应用的明暗主题</div>
      </div>
      <ToggleSwitch
        :model-value="settingsStore.darkMode"
        @update:model-value="settingsStore.toggleDarkMode()"
      />
    </div>
    <div class="setting-row">
      <div>
        <div class="setting-label">代码字体大小</div>
        <div class="setting-desc">调整代码编辑器的字号</div>
      </div>
      <select
        :value="settingsStore.codeFontSize"
        class="select-input"
        @change="settingsStore.setCodeFontSize(($event.target as HTMLSelectElement).value)"
      >
        <option value="12px">12px</option>
        <option value="14px">14px</option>
        <option value="16px">16px</option>
        <option value="18px">18px</option>
      </select>
    </div>
    <div class="setting-row">
      <div>
        <div class="setting-label">自动保存</div>
        <div class="setting-desc">编辑代码时自动保存</div>
      </div>
      <ToggleSwitch
        :model-value="settingsStore.autoSave"
        @update:model-value="settingsStore.toggleAutoSave()"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useSettingsStore } from '@/stores/settings'
import ToggleSwitch from '@/components/common/ToggleSwitch.vue'

const settingsStore = useSettingsStore()
</script>

<style scoped>
.section-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 20px;
}

.setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
  gap: 16px;
}

.setting-label {
  font-size: 14px;
  font-weight: 500;
  color: var(--fg);
}

.setting-desc {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}

.select-input {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
  font-family: var(--font-body);
  color: var(--fg);
  background: var(--surface);
  cursor: pointer;
  outline: none;
}

.select-input:focus {
  border-color: var(--accent);
}
</style>
