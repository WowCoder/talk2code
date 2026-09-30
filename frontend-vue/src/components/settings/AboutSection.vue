<template>
  <div class="section">
    <h3 class="section-title">关于 Talk2Code</h3>

    <!-- 版本卡 -->
    <div class="brand-card">
      <div class="brand-row">
        <img src="@/assets/logo.png" alt="Talk2Code" class="brand-logo" />
        <div class="brand-info">
          <div class="brand-name-row">
            <span class="brand-name">Talk2Code</span>
            <span class="version-pill">v0.4</span>
            <span class="brand-stage">内测开放中</span>
          </div>
          <p class="brand-desc">
            用多智能体协作，把「想做的事」变成可运行的代码。
            TeamLeader 拆任务、Coder 写代码、QA 在浏览器里实际跑，三角色循环迭代，
            每一轮都有真人确认。
          </p>
        </div>
      </div>
    </div>

    <!-- 更新日志：只记录真实存在的版本，更早的指向 GitHub commits，
         不在界面上虚构没有发生过的版本号 -->
    <div class="changelog-card">
      <div class="changelog-title">更新日志</div>
      <div class="changelog-current">
        <div class="cl-row">
          <span class="cl-version">v0.4</span>
          <span class="cl-tag">当前版本</span>
        </div>
        <div class="cl-desc">内测开放 · 历史记录 / 创意市集上线 · 主应用改版</div>
      </div>
      <div class="changelog-older">
        <span>觉得 Talk2Code 有用？去 GitHub 点个 Star 支持一下 →</span>
        <a
          class="commits-btn"
          href="https://github.com/WowCoder/talk2code"
          target="_blank"
          rel="noopener"
        >去 Star</a>
      </div>
    </div>

    <!-- 条款 / 隐私 / 联系：三列等高卡片 -->
    <div class="info-grid">
      <div class="info-card">
        <svg class="info-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7l8-4z"/>
          <path d="M9 12l2 2 4-4"/>
        </svg>
        <h4 class="info-title">服务条款</h4>
        <p class="info-desc">你发布的内容归你 · 我们不扫描 · 保留 30 天后自动归档</p>
      </div>
      <div class="info-card">
        <svg class="info-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="9"/>
          <circle cx="12" cy="12" r="3.5"/>
        </svg>
        <h4 class="info-title">隐私政策</h4>
        <p class="info-desc">只收集用于改进生成效果的反馈 · 不做广告画像 · 可一键导出</p>
      </div>
      <div class="info-card">
        <svg class="info-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="5" width="18" height="14" rx="2"/>
          <path d="M3 7l9 6 9-6"/>
        </svg>
        <h4 class="info-title">联系我们</h4>
        <p class="info-desc">Bug / 合作 / 内测邀请都欢迎 · 一般一周内回复</p>
        <a class="info-link" href="mailto:723181005@qq.com">723181005@qq.com →</a>
      </div>
    </div>

    <footer class="about-footer">
      © 2026 Talk2Code · 由 <a class="footer-repo" href="https://github.com/WowCoder/talk2code"
        target="_blank" rel="noopener">WowCoder/talk2code</a> 维护
    </footer>
  </div>
</template>

<style scoped>
.section-title {
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 18px;
}

/* ===== 版本卡 ===== */
.brand-card {
  background: var(--accent-soft);
  border: 1px solid rgba(207, 106, 95, 0.2);
  border-radius: 14px;
  padding: 20px;
  margin-bottom: 14px;
}

.brand-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.brand-logo {
  width: 52px;
  height: 52px;
  border-radius: 12px;
  flex-shrink: 0;
}

.brand-info {
  min-width: 0;
}

.brand-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.brand-name {
  font-family: var(--font-display);
  font-size: 19px;
  font-weight: 700;
  color: var(--fg);
}

.version-pill {
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 600;
}

.brand-stage {
  font-size: 12px;
  color: var(--muted);
}

.brand-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--fg);
}

/* ===== 更新日志 ===== */
.changelog-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px 18px;
  margin-bottom: 14px;
  background: var(--surface);
}

.changelog-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
  margin-bottom: 12px;
}

.changelog-current {
  background: var(--bg);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 10px;
}

.cl-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
}

.cl-version {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-strong);
}

.cl-tag {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent-strong);
}

.cl-desc {
  font-size: 12.5px;
  color: var(--fg);
}

.changelog-older {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  font-size: 12px;
  color: var(--muted);
}

.commits-btn {
  flex-shrink: 0;
  padding: 6px 14px;
  border-radius: 8px;
  /* 反色按钮：底用 --fg、字用 --bg，两种主题下都自动成立
     （深色 = 浅底深字，浅色 = 深底浅字），写死颜色必翻车 */
  background: var(--fg);
  color: var(--bg);
  font-size: 12px;
  font-weight: 600;
  text-decoration: none;
  transition: opacity 0.15s;
}

.commits-btn:hover {
  opacity: 0.85;
}

/* ===== 条款三列（layoutGrow + 等高对齐由 grid 保证）===== */
.info-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}

.info-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  background: var(--surface);
}

.info-icon {
  color: var(--accent);
  margin-bottom: 10px;
}

.info-title {
  margin: 0 0 6px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--fg);
}

.info-desc {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--muted);
  flex: 1;
}

.info-link {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent-strong);
  text-decoration: none;
}

.info-link:hover {
  text-decoration: underline;
}

.about-footer {
  text-align: center;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--faint);
  padding-top: 6px;
}

.footer-repo {
  color: var(--muted);
  text-decoration: none;
  border-bottom: 1px solid var(--border);
}

.footer-repo:hover {
  color: var(--accent-strong);
  border-bottom-color: var(--accent-strong);
}

@media (max-width: 640px) {
  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
