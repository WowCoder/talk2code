---
name: scaffold
description: 项目脚手架技能——按固定架构生成纯前端项目骨架
when_to_use: 开始一个新页面 / 小游戏 / 工具类需求时优先使用，从源头规避常见错误
level: L1
type: workflow
triggers: [脚手架, 初始化项目, 项目结构, 搭建项目, 骨架]
---

# 项目脚手架技能（scaffold）

在开始写任何业务代码前，先用本技能把项目骨架固定下来。
本项目是**纯前端应用**，所有架构决策必须符合以下硬约束。

## 固定架构模板

```
index.html              # 入口文件，必须存在
css/
  style.css             # 全局样式（按需拆分）
js/
  storage.js            # 数据持久化封装（localStorage / IndexedDB）
  app.js                # 主逻辑入口，依赖顺序最后引入
  （按需拆分的业务模块）
assets/                 # 图片等静态资源（可选）
```

## 硬约束（写进每一个文件）

1. **禁止 ES Module**：每个 JS 文件用 IIFE 包裹
   `(function (global) { ... })(window)`，通过 `window.XXX` 暴露接口；
   HTML 用普通 `<script src="js/xxx.js"></script>` **按依赖顺序**引入
   （storage.js 在前，app.js 最后）。
2. **相对路径**：所有引用（css/js/资源）使用相对路径，保证 file:// 可加载。
3. **无外部 CDN / 远程资源**：样式与脚本本地化，第三方能力用原生实现等价替代。
4. **无构建工具**：不产生 npm / bundler 依赖，交付即目录本身。
5. **数据持久化**：简单键值用 localStorage，大量结构化数据用 IndexedDB，
   统一封装在 `js/storage.js`（getItem/setItem 或增删改查接口），
   应用启动时做数据完整性检查并提供降级方案。
6. **无 XSS 风险**：动态内容用 `textContent` / `createElement`，禁止 `innerHTML` / `eval`。
7. **初始化时机**：事件绑定与数据加载在 DOM 就绪后进行（`DOMContentLoaded` 或脚本置于 body 尾部）。

## 执行步骤

1. 用 `list_files` 检查工作区现状，避免覆盖已有文件。
2. 按 `page_type` 决定骨架规模：
   - **landing / form**（简单单页）：index.html + css/style.css + js/app.js
   - **dashboard / general**（工具/仪表盘）：再加 js/storage.js 与按需业务模块
   - **game**（小游戏）：index.html + css/style.css + js/game.js + js/storage.js（存档用）
3. 用 `write_file` 依次生成骨架文件：**先 index.html，再 css，最后 js（按依赖顺序）**。
4. 每个生成文件内置上述硬约束注释，作为后续编码的规范基线。
5. 汇报生成的文件清单与已落实的约束。

## 输出

按 output_schema 返回：`structure`（文件清单）+ `conventions_applied`（落实的约束）。
