## 实现策略（standard 完整流程）
1. 先创建入口文件 index.html（按依赖顺序以普通 `<script src>` 引入所有本地 js/css）
2. 按模块逐层组织（使用子目录如 css/、js/），每个模块单一职责
3. 一次创建 2-3 个相关模块文件（如 CSS + JS），每个文件必须完整
4. 全部文件创建完成后一次性验证（validate_html / lint_css / lint_js）
5. **调用 run_preview 在真实浏览器中验证 → 修复错误 → 再验证通过 → 任务完成**

## 要求
- 可以批量创建小文件（<300 行），大文件（>300 行）单独创建确保质量
- 按推荐文件结构创建，不使用构建工具
- JS 文件用 IIFE 包裹 `(function (global) { ... })(window)` 并通过 `window.XXX` 暴露接口（见环境硬约束 ENV-3）
- 复杂度 standard：需要考虑可维护性和扩展性
