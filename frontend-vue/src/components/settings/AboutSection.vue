<template>
  <div class="section">
    <h3 class="section-title">关于</h3>
    <div class="about-table">
      <div class="about-row">
        <span class="about-key">版本</span>
        <span class="about-val">v2.0.0</span>
      </div>
      <div class="about-row">
        <span class="about-key">构建日期</span>
        <span class="about-val">2026.05.20</span>
      </div>
      <div class="about-row">
        <span class="about-key">技术栈</span>
        <span class="about-val">Vue 3 + Flask + LangGraph + SSE</span>
      </div>
      <div class="about-row">
        <span class="about-key">设计系统</span>
        <span class="about-val">Warm Soft</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.section-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 20px;
}

.about-table {
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}

.about-row {
  display: flex;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.about-row:last-child {
  border-bottom: none;
}

.about-key {
  width: 120px;
  font-size: 13px;
  color: var(--muted);
  flex-shrink: 0;
}

.about-val {
  font-size: 13px;
  color: var(--fg);
  font-weight: 500;
}
</style>
