# Design: Warm Soft 风格重构

## Context

5 个 HTML 原型页面提供了完整的视觉设计参考。实现策略：直接基于原型 HTML/CSS 重构，保留后端 API 接口和 JS 业务逻辑不变。

## Goals / Non-Goals

**Goals:**
- 5 个页面全部使用统一的 OKLch 设计系统
- login/index/detail 功能逻辑完整保留
- history/settings 新增页面集成真实 API

**Non-Goals:**
- 不修改后端代码
- 不抽取 CSS 文件（保持单文件部署简单）
- 不引入构建工具或 npm 依赖

## Decisions

1. **纯 CSS Custom Properties** — 不使用 Tailwind CDN，每个页面 `:root` 块定义相同变量
2. **自建代码编辑器** — 替代 CodeMirror CDN，使用 `<pre><code>` + 自定义语法 Token 着色，减少外部依赖
3. **原型即代码** — 原型 HTML 直接作为重构基线，仅替换静态示例为动态数据绑定

## Risks

- 自建编辑器功能不如 CodeMirror 完善 → 逐步迭代增强
- 原型中部分交互为静态 mock → 需补充真实 API 调用
