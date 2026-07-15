## 1. Completion Contract 基础设施

- [x] 1.1 创建 `backend/harness/constraints/completion_contract.py`：实现 `CompletionContract` 类（从 `implementation_order` 生成 `.task/contract.json`，支持 `mark_created`/`mark_validated`/`all_completed`/`pending_files` 方法）
- [x] 1.2 创建 `backend/harness/constraints/progress_hooks.py`：实现 `block_unnecessary_read` Hook（写入后 2 轮内阻断 `read_file` 同一文件）和 `block_premature_completion` Hook（contract 未全部完成时阻断 `task_complete`）
- [x] 1.3 在 `backend/harness/constraints/__init__.py` 中注册新 Hook 到 HookManager
- [x] 1.4 在 `backend/harness/instructions/file_coder.py` 中集成 CompletionContract：编码开始前初始化 contract，`write_file` 成功后更新状态，编码结束前检查 `all_completed()`
- [x] 1.5 在 `backend/harness/runtime.py` 的 `_execute_tool` 中集成 contract 更新：`write_file` 成功后自动调用 `contract.mark_created()`
- [x] 1.6 编写 CompletionContract 和 progress_hooks 的单元测试

## 2. 修复 lint_js ES Module 误报

- [x] 2.1 修改 `backend/harness/tools/code_tools.py` 的 `lint_js()` 方法：检测文件是否包含 ES Module 语法（`export`/`import` 关键字），使用 `--input-type=module` 参数
- [x] 2.2 添加 ES Module 语法检测的正则匹配逻辑（`re.search(r'\b(export|import)\s+[\{\*]', content)`）
- [x] 2.3 处理 Node.js 不可用和超时的降级情况
- [x] 2.4 更新 `lint_js` 的 tool description，注明支持 ES Module 检测
- [x] 2.5 编写 lint_js ES Module 检测的单元测试（ES Module 文件正确通过、CommonJS 文件正确通过、真实语法错误正确报告）

## 3. write_file 返回元数据 + 防回读

- [x] 3.1 修改 `backend/harness/runtime.py` 的 `_execute_tool` 中 `write_file` 的结果格式：从返回文件内容改为仅返回元数据（文件名、行数、字符数）
- [x] 3.2 在 `write_file` 成功后自动更新文件摘要缓存（`_build_file_summaries` 增量更新而非全量重建）
- [x] 3.3 在系统 prompt 模板 `coding/file_aware_coder.md` 中强化：明确 write_file 返回后文件已完整写入，禁止 read_file 验证
- [x] 3.4 在系统 prompt 模板 `coding/coder_ml.md` 中同步更新 write_file 行为说明

## 4. edit_file 提升为默认编辑工具

- [x] 4.1 更新 `backend/harness/instructions/prompts/coding/file_aware_coder.md`：新增 edit_file 使用说明，明确"修改已有文件用 edit_file，创建新文件用 write_file"
- [x] 4.2 更新 `backend/harness/instructions/prompts/coding/coder_ml.md`：同步 edit_file 说明
- [x] 4.3 更新 `backend/harness/instructions/prompts/coding/coder_s.md`：同步 edit_file 说明
- [x] 4.4 更新 `backend/harness/instructions/prompts/coding/coder_xs.md`：同步 edit_file 说明
- [x] 4.5 在 prompt 中增加 lint 使用规范：全部文件完成后再统一验证，不需要每文件单独 lint

## 5. Fresh-Context Evaluator

- [x] 5.1 创建 `backend/harness/instructions/prompts/verify/evaluator.md`：Evaluator 的系统 prompt，定义评估维度（功能完整性、运行时正确性、UI 质量、验收条件、代码质量）、输出格式（PASS/NEEDS_WORK JSON）、工作流程（读 SPEC → 读代码 → 运行预览 → 输出评估）
- [x] 5.2 在 `backend/harness/instructions/nodes.py` 中实现 `verify_node(state)`：创建独立 LLM 上下文（不含编码历史），调用 `run_preview` 获取浏览器输出，传递评估 prompt，解析结构化 JSON 结果
- [x] 5.3 Verify 节点只授予 Read 工具（`read_file`、`list_files`），不授予 `write_file`/`edit_file`
- [x] 5.4 将评估结果写入 `.task/evaluator/result.json`
- [x] 5.5 在 `backend/harness/graph.py` 中注册 `verify` 节点并连接路由（PASS→END, NEEDS_WORK→repair）

## 6. 简化 LangGraph 工作流

- [x] 6.1 修改 `backend/harness/instructions/nodes.py`：删除 `pm_node`、`architect_node`、`qa_node`、`tester_node`、`summarize_node` 函数定义
- [x] 6.2 删除 `backend/harness/instructions/role_executor.py`
- [x] 6.3 删除 `backend/harness/instructions/orchestrator.py`
- [x] 6.4 删除 `backend/harness/instructions/simple_coder.py`（逻辑合并入统一的 coder 节点）
- [x] 6.5 删除 `backend/harness/instructions/summarize.py`
- [x] 6.6 修改 `backend/harness/instructions/file_coder.py`：重命名为统一的 `coder_node`，内部根据 complexity 选择策略
- [x] 6.7 修改 `backend/harness/graph.py`：工作流从 8 节点简化为 4 节点（team_leader → coder → verify → repair），路由函数从 7 个减为 2 个（`route_after_tl`、`route_after_verify`）
- [x] 6.8 更新所有 import 路径和函数引用（`backend/services/requirement_service.py`、`backend/harness/__init__.py`）
- [x] 6.9 删除不再需要的 prompt 模板文件：`prompts/roles/product_manager.md`、`prompts/roles/architect.md`、`prompts/roles/qa_reviewer.md`、`prompts/tasks/pm_task.md`、`prompts/tasks/architect_task.md`、`prompts/tasks/qa_task.md`
- [x] 6.10 增强 TeamLeader prompt（`prompts/coding/tl_analysis.md`）：整合 PRD 和 SPEC 的关键输出字段（`tech_stack`、`acceptance_criteria`）

## 7. 前端适配

- [x] 7.1 修改 `frontend-vue/src/types/sse.ts`：新增 `evaluator_result` 事件类型，新增 `SSEEvaluatorResultData` 接口（含 verdict、findings、score），Task 状态联合类型增加 `'blocked' | 'failed'`
- [x] 7.2 修改 `frontend-vue/src/composables/useSSE.ts`：新增 `evaluator_result` 事件监听，将评估结果存入 store
- [x] 7.3 修改 `frontend-vue/src/stores/requirement.ts`：新增 `_evaluatorResult` 状态字段
- [x] 7.4 修改 `frontend-vue/src/components/detail/TaskPanel.vue`：`DevTask.status` 类型增加 `'blocked' | 'failed'`，新增对应的边框颜色和徽章样式（blocked=橙色、failed=红色），`badgeLabel()` 增加对应中文映射
- [x] 7.5 修改 `frontend-vue/src/components/detail/SpecPanel.vue`：新增 Evaluator 结果展示区域（评估结论 PASS/NEEDS_WORK、分维度评分、findings 列表）
- [x] 7.6 修改 `frontend-vue/src/views/DetailView.vue`：确保 evaluator 结果在界面上可见（可集成到 SpecPanel 或新增展示区域）
- [x] 7.7 验证前端构建 `cd frontend-vue && npm run build` 无报错

## 8. 清理和验证

- [x] 8.1 删除不再需要的 import（检查所有修改过的 Python 文件，移除未使用的 import）
- [x] 8.2 更新 `backend/tests/` 中受影响的测试用例：更新节点名称引用、新增 CompletionContract 和 progress_hooks 测试、新增 Evaluator 测试
- [x] 8.3 运行全量测试 `cd backend && pytest` 确保无回归
- [x] 8.4 运行 `cd backend && python -c "from app import app; print('OK')"` 确保 Flask 启动正常
- [x] 8.5 更新 `README.md`：修改目录结构中 harness/ 下的模块描述，更新架构图
