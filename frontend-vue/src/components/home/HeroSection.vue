<template>
  <div class="hero">
    <span class="hero-badge">
      <span class="badge-dot"></span>
      v0.4 · 内测开发中
    </span>
    <h1 class="hero-title">用一句话，生成一个<mark>能跑</mark>的应用</h1>
    <p class="hero-desc">
      描述你想要的应用，Talk2Code 的多智能体会自动拆解需求，批量编码、
      用 Playwright 真实验收。你可以随时打断、补充，让它继续做下去。
    </p>
  </div>
</template>

<style scoped>
.hero {
  text-align: center;
  padding: 48px 0 32px;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 14px;
  border-radius: 100px;
  background: var(--accent-soft);
  border: 1px solid rgba(207, 106, 95, 0.25);
  color: var(--accent-strong);
  font-size: 12px;
  font-weight: 500;
  margin-bottom: 20px;
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: hero-pulse 2s infinite;
}

@keyframes hero-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.hero-title {
  font-family: var(--font-display);
  font-size: clamp(30px, 5vw, 48px);
  font-weight: 700;
  color: var(--fg);
  letter-spacing: -0.02em;
  margin-bottom: 14px;
  line-height: 1.25;
}

.hero-title mark {
  background: none;
  color: var(--accent);
}

.hero-desc {
  font-size: 14.5px;
  color: var(--muted);
  max-width: 560px;
  margin: 0 auto;
  line-height: 1.8;
}
</style>
