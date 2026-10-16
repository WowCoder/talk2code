# Usage Accounting Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. User has authorized execution in this chat.

**Goal:** 可靠采集每次 LLM 请求并统一用量、美元估价和 prompt 缓存展示。

**Architecture:** 在客户端 HTTP 边界采集实际尝试，复用 llm_turn 事件；语义埋点只补充上下文。协议归一化与十进制计价独立，需求累计与后台共用聚合。

**Tech Stack:** Python/Flask/SQLAlchemy, Vue 3/TypeScript, pytest.

**Spec:** docs/design/usage-accounting.md

## Global Constraints

- 保留已有未提交变更，沿用 get_client 与数据库 session 管理约定。
- 网络失败未知用量不得伪装成零，缓存读取属于总输入，禁止重复记账。
- 第一批实施；不部署、不重启，不自动改写历史生产数据。

## Review Focus

- reasoning 重试前一次成功 HTTP 的 usage 必须保留。
- 备用端点实际模型、协议、缓存价必须跟随事件。
- 无需求号的评测和有需求号的执行上下文都可归因。
- 旧事件及客户端 mock 不带新标记仍能汇总，不与新事件双计。
- 多线程共享客户端无串台，暂停确认和异常不会遗留活动 trace。

## Tasks

- [x] Task 1: usage 协议归一化、精确价格匹配、独立缓存价格、Decimal 计价与价格快照；27 个新测试先失败后通过。
- [x] Task 2: 客户端每次 HTTP 尝试采集、唯一 ID、错误未知状态；语义事件补充、移除重复成本累计；9 个请求边界集成回归通过。
- [x] Task 3: 共用事件汇总、需求累计与 trace生命周期、运营统计统一；专项 91 项通过。
- [x] Task 4: 全站 USD 与精度、需求累计/本次执行、缓存命中与覆盖率/未知展示；前端全部检查与构建通过。
- [x] Task 5: 历史预览/幂等应用、文档、集成回归、独立审查。

## Execution ledger

- Working tree already contains the user's related edits. Integrate in place because managed worktrees omit uncommitted changes; no Git history mutation is required.
- Parallel ownership: pricing/normalization, frontend, lifecycle/API; controller owns client and trace writer. Shared interfaces are the spec's summary fields.
- 历史修复默认预览，含 before/after 样本；5 个隔离数据库测试验证不写入、幂等、价格快照稳定、缺失数据保留及 trace 重载字段完整。
- 独立审查指出流式 HTTP 错误 usage 丢失、长 writer seq 缓存受中央写入影响；均已补失败测试并修复。
- 最终全量 backend pytest：1453 passed、2 skipped、9 deselected；最后历史重载修复另跑 5 passed。前端 check/build 与 git diff --check 通过。
- 独立审查未发现遗留 P1；最后历史重载 P2 已修复并独立复核幂等与字段恢复。测试退出保留既有 factory 日志流关闭噪声，pytest exit 0。
- 实施完成，变更保留工作区；未提交、未部署、未重启、未自动应用历史修复。
