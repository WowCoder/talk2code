---
name: landing
description: 落地页 / 产品官网类需求：首屏完整、零外链资源、响应式、锚点导航
when_to_use: 需求是产品官网、落地页、个人主页、作品集这类展示型页面
level: L1
triggers: [落地页, 官网, 首页, 宣传页, 介绍页, 作品集, landing]
---

# 落地页规范

## P0 首屏必须完整可见

页面加载后，主标题、副标题、主 CTA 按钮**必须立刻可见**。
禁止把首屏内容藏在默认 `hidden` 的容器里，或依赖滚动/点击才显形——
打开是空白页，用户直接判定失败。

同样禁止用「打字机动画」延迟主标题出现：动画没跑完时首屏看起来是空的，
自动验收在这一刻截图就会判不通过。要动画就写在次要元素上，
并让文字的最终状态在 HTML 里就存在。

## P0 零外链资源

没有网络，以下全部不可用：
- Google Fonts / 任何字体 CDN → 用系统字体栈：
  `font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;`
- 图标字体（Font Awesome 等）→ 内联 SVG 图标，或用 CSS 画
- 图片 CDN / 占位图服务 → 内联 SVG、CSS 渐变、或 base64 内联（小图）
- 任何 `https://` 开头的 `src` / `href`

写完自查：全文搜 `https://` 和 `//cdn` 应 0 命中。

## P0 响应式

至少覆盖两档，且**移动端不能横向溢出**：

```css
@media (max-width: 768px) {
  .hero { padding: 48px 20px; }
  .grid { grid-template-columns: 1fr; }
  .nav-links { display: none; }      /* 或改成汉堡菜单 */
}
```

常见溢出源：固定 `width` 的大容器、不换行的长英文单词
（加 `overflow-wrap: break-word`）、多列 grid 没在窄屏降为单列。

## P1 锚点导航

导航点击滚动到对应区块，用 CSS 平滑滚动即可，不需要 JS：

```css
html { scroll-behavior: smooth; }
section { scroll-margin-top: 64px; }   /* 避开固定导航栏遮挡 */
```

漏掉 `scroll-margin-top` 时，跳转后标题会被固定导航盖住。

## P1 视觉层次

- 一个页面只设**一个**主 CTA（主色实心按钮），其余用次要样式
- 区块之间用留白或分隔线区分，不要靠背景色堆砌
- 真正的卖点用 3-4 个卡片并列呈现，每项「图标 + 标题 + 一句说明」

## P1 内容要有实质

标题和文案必须是**针对该需求写的具体内容**，不能是
"Lorem ipsum""示例文本""你的标题"。空壳页面一眼就看出来。

## 自验收清单

- [ ] 打开即看到主标题 + CTA，无空白、无需要交互才显示的内容
- [ ] 无任何 `https://` 外链
- [ ] 窗口收窄到 375px 无横向滚动条
- [ ] 导航锚点点击能定位到对应区块，标题不被导航栏遮挡
- [ ] 控制台无报错
