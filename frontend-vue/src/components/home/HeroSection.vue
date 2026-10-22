<template>
  <div class="hero">
    <h1 class="hero-title"><span class="hero-title-lead">一句话，</span>生成一个<mark>能跑、能改、能发布</mark>的应用</h1>
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

.hero-title {
  font-family: var(--font-display);
  font-size: clamp(26px, 4.8vw, 44px);
  font-weight: 700;
  color: var(--fg);
  letter-spacing: -0.02em;
  margin-bottom: 14px;
  line-height: 1.25;
}

.hero-title-lead {
  display: block;
}

.hero-title mark {
  display: inline-block;
  background: linear-gradient(96deg, #e23b2e, #f08a1d);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

@media (max-width: 520px) {
  .hero-title {
    font-size: clamp(22px, 6.4vw, 28px);
  }

  .hero-title-lead {
    display: inline;
  }
}

.hero-desc {
  font-size: 14.5px;
  color: var(--muted);
  max-width: 560px;
  margin: 0 auto;
  line-height: 1.8;
}
</style>
