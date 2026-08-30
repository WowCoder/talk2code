---
name: color
description: 色彩系统：色板结构、主色纪律、语义色、深色主题（原生 CSS 变量实现）
when_to_use: 任何有 UI 界面的任务（网页、游戏、工具、应用），需要配色方案、主题风格或视觉设计
level: L1
triggers: [ui, page, app, 界面, 页面, 网站, 应用, 设计, 视觉, 布局, 样式, 颜色, 风格, 主题, 暗色, 配色, 游戏, 工具, 仪表盘, 可视化, 计算器, 编辑器, 番茄钟, 倒计时, 看板, 时钟, 待办, 便签, 笔记, 博客, 聊天, 日历]
---

# 色彩系统规则

生成 Web 应用时必须遵守以下色彩规则。全部用原生 CSS 实现（CSS 变量 + 类名），不依赖任何外部样式库。

## 色板结构

一个协调的 UI 色彩系统包含四层：

| 层级 | 占比 | 原生 CSS 取值建议 |
|------|------|------------------|
| 中性色 | 70-90% | 一组灰阶变量：#f8fafc / #e2e8f0 / #64748b / #1e293b 等 |
| 主色（一个） | 5-10% | 选一个饱和色做品牌色（如 #2563eb 蓝 / #059669 绿 / #e11d48 玫红） |
| 语义色 | 0-5% | 成功绿 / 危险红 / 警告黄，各一个变量 |
| 特效色 | <1% | 渐变、发光（极少使用） |

## 主色纪律

- **每个屏幕最多使用 2 个主色元素**：典型配对：一个标签/徽章 + 一个主按钮。或一个导航激活态 + 一个 CTA
- 链接算主色消耗；如果同屏已有 CTA，链接降级为下划线灰色
- Hover/Focus 环也消耗主色配额（focus-visible 的 outline 用主色，注意别过量）

## 语义色推荐（原生 CSS 变量示例）

```css
:root {
  --color-success-bg: #f0fdf4;  --color-success-fg: #15803d;  --color-success-border: #bbf7d0;
  --color-warning-bg: #fefce8;  --color-warning-fg: #a16207;  --color-warning-border: #fde68a;
  --color-danger-bg:  #fef2f2;  --color-danger-fg:  #b91c1c;  --color-danger-border:  #fecaca;
  --color-info-bg:    #eff6ff;  --color-info-fg:    #1d4ed8;  --color-info-border:    #bfdbfe;
}
```

成功/警告/错误/信息提示条分别引用对应变量组合（浅底深字 + 同系边框）。

## 深色主题

如果应用支持暗色模式：
- 背景不使用纯黑 `#000`，使用 `#0f0f0f` 或 `#030712`
- 文字不使用纯白 `#fff`，使用 `#f0f0f0` 或 `#f9fafb`
- 暗色表面使用半透明白色边框 `rgba(255,255,255,0.08)` 代替纯色边框

## 命名原则

- 按用途命名，不按颜色值命名
- 好的：`--color-primary`, `--color-danger`
- 不好的：`--blue-500`, `--red-600`（锁定主题，难以切换）
- 在 `style.css` 的 `:root` 中定义全局颜色变量，组件样式一律引用变量
