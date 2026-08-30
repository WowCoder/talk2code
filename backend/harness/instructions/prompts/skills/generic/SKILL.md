---
name: generic
description: 通用前端开发知识：存储方案选择、自包含交付、文件组织、常见易错点
when_to_use: 任何前端开发任务都应启用（提供 localStorage/IndexedDB、原生 CSS、文件结构等基础模式）
level: L0
always: true
triggers: []
---

## 核心原则

1. **纯前端应用** — 不涉及后端服务器，所有功能在浏览器中完成
2. **浏览器存储** — 使用 localStorage / IndexedDB 进行数据持久化
3. **文件组织** — 根据复杂度自由组织文件结构，支持子目录（css/、js/、assets/）
4. **技术栈** — 原生 HTML/CSS/JS，样式本地化（禁止外部 CDN）。**禁止 ES Module（`import`/`export`）**，因为预览/验证环境用 file:// 协议加载，ES Module 会被 CORS 拦截导致页面完全无功能

## 设计要点

- 根据需求内容自主决定布局和交互
- 保持设计简洁实用
- 所有交互元素必须可访问

## 数据持久化方案选择

| 存储方案 | 适用场景 | 容量 |
|---------|---------|:---:|
| localStorage | 简单键值数据、用户设置 | 5-10MB |
| IndexedDB | 结构化数据、大量记录、需要查询 | 50MB+ |
| sessionStorage | 会话级临时数据 | 5-10MB |

原则：
- 简单数据用 localStorage 封装 getItem/setItem
- 大量数据用 IndexedDB 封装增删改查接口
- 数据操作封装在独立的 js/storage.js 文件中
- 应用启动时检查数据完整性，异常时提供降级方案

## 常见易错点

1. innerHTML 有 XSS 风险，使用 textContent 或 createElement 替代
2. eval() 存在安全风险，禁止使用
3. 文件路径使用相对路径，确保 preview 可以正确加载
4. 自包含交付：不使用 npm/构建工具，也不引入外部 CDN；第三方能力用原生实现等价替代
5. 必须有一个 index.html 作为入口文件
6. **禁止使用 ES Module（`<script type="module">`、`import`、`export`）** — 预览用 file:// 协议加载，ES Module 被 CORS 拦截会导致所有 JS 不执行。正确做法：每个 JS 文件用 IIFE 包裹 `(function (global) { ... })(window)`，通过 `window.XXX` 或全局变量暴露接口，HTML 里用普通 `<script src="js/xxx.js"></script>` 按依赖顺序引入

## 检查清单

- [ ] index.html 作为入口文件
- [ ] 所有资源使用相对路径
- [ ] 无 innerHTML / eval / document.write
- [ ] 无 ES Module（import/export），用普通 script + IIFE
- [ ] 数据通过 localStorage/IndexedDB 持久化
- [ ] 代码通过 JS/CSS 语法检查
