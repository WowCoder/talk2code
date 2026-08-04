<template>
  <div class="hero">
    <h1 class="hero-title">
      用自然语言<mark>创造</mark>应用
    </h1>
    <p class="hero-desc">
      描述你想要的应用，AI 将为你生成完整的前端代码。支持实时预览和对话式修改。
    </p>
  </div>
</template>

<style scoped>
.hero {
  text-align: center;
  padding: 48px 0 32px;
}

.hero-title {
  font-family: var(--font-display);
  font-size: clamp(28px, 5vw, 48px);
  font-weight: 700;
  color: var(--fg);
  letter-spacing: -0.02em;
  margin-bottom: 12px;
  line-height: 1.3;
}

.hero-title mark {
  background: none;
  color: var(--accent);
}

.hero-desc {
  font-size: 15px;
  color: var(--muted);
  max-width: 520px;
  margin: 0 auto;
  line-height: 1.7;
}
</style>
