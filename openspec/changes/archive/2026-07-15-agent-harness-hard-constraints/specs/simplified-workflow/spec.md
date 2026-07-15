## ADDED Requirements

### Requirement: 简化工作流节点
系统 SHALL 将 LangGraph 工作流从当前的 8 节点简化为 4 节点：TeamLeader → Coder → Verify → Repair。

#### Scenario: 正常 M/L 复杂度流程
- **WHEN** 用户提交 M 或 L 复杂度的开发需求
- **THEN** 工作流按 TeamLeader → Coder → Verify 顺序执行，Repair 仅在 Verify 失败时触发

#### Scenario: XS/S 复杂度流程
- **WHEN** 用户提交 XS 或 S 复杂度的开发需求
- **THEN** 工作流按 TeamLeader → Coder → Verify 顺序执行（与 M/L 相同），Coder 内部根据复杂度调整策略

### Requirement: TeamLeader 整合设计输出
系统 SHALL 要求 TeamLeader 在单个 LLM 调用中输出包含需求分析、技术选型、文件结构、接口契约、验收条件的完整 Plan JSON。

#### Scenario: Plan 完整性
- **WHEN** TeamLeader 完成分析
- **THEN** 输出的 Plan JSON 包含 `tech_stack`、`file_structure`、`tasks`（含 description/exports/imports/dependencies）、`interfaces`、`implementation_order`、`acceptance_criteria` 字段

#### Scenario: Plan 缺少关键字段
- **WHEN** TeamLeader 输出的 Plan 缺少 `implementation_order`
- **THEN** 系统记录警告，回退到从 `tasks` 字段提取文件列表

### Requirement: 删除多角色模拟节点
系统 SHALL 移除 PM、Architect、QA、Summarize、Tester 节点及其相关文件。

#### Scenario: 节点不可达
- **WHEN** 工作流编译
- **THEN** 图中不存在 `pm`、`architect`、`qa`、`summarize`、`tester` 节点

#### Scenario: 旧角色 prompt 文件删除
- **WHEN** 代码部署
- **THEN** `prompts/roles/product_manager.md`、`prompts/roles/architect.md`、`prompts/roles/qa_reviewer.md` 文件不存在

### Requirement: 统一 Coder 节点
系统 SHALL 用统一的 Coder 节点替代当前的 `simple_coder_node` 和 `file_by_file_coder_node`，内部根据 `complexity` 和是否有 `implementation_order` 选择执行策略。

#### Scenario: M/L 复杂度逐文件编码
- **WHEN** complexity 为 M 或 L 且有 `implementation_order`
- **THEN** Coder 内部使用逐文件编码策略（保留 FileByFileCoder 逻辑）

#### Scenario: XS/S 复杂度直接编码
- **WHEN** complexity 为 XS 或 S
- **THEN** Coder 内部使用直接 ToolCallLoop 编码（保留 SimpleCoder 逻辑）

### Requirement: 路由简化
系统 SHALL 将路由函数从 7 个减少为 2 个（`route_after_tl` 和 `route_after_verify`）。

#### Scenario: TL 后路由
- **WHEN** TeamLeader 完成
- **THEN** clarify → END，其他全部 → Coder

#### Scenario: Verify 后路由
- **WHEN** Verify 完成
- **THEN** PASS → END，NEEDS_WORK 且 repair_count < 3 → Repair，NEEDS_WORK 且 repair_count >= 3 → END
