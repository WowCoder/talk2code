## Context

Talk2Code 当前是一个 Prompt-based 代码生成器：用户输入需求 → Planner 调用 LLM 输出 Plan JSON → Coder 调用 LLM 输出 Code JSON → 前端渲染。LLM 没有工具可用，无法验证代码，不能迭代修复，也没有跨会话记忆。

完整技术设计详见 `docs/design/harness-6layer-architecture.md`（约 2000 行）。本文档聚焦核心架构决策。

**当前状态**：
- Flask 后端 + 原生前端 (HTML/JS/Tailwind) + SQLite
- LangGraph 双节点工作流 (Planner → Coder)
- SSE 实时通信（progress/dialogue/code/complete 事件）
- LLM 通过 `llm/client.py` 统一接入，支持 OpenAI/Anthropic 双协议

## Goals / Non-Goals

**Goals:**
- Agent 拥有工具调用能力（文件读写、代码验证、Web 搜索），从单次生成变为多轮迭代
- 代码生成后可在沙箱中验证，错误自动修复
- 用户偏好和项目背景跨会话记忆
- 设计质量规则从"建议"变为"强制检查"
- 完整可观测性（追踪、成本、日志）

**Non-Goals:**
- 不引入容器级沙箱（Docker/gVisor）—— 初期使用 subprocess 隔离
- 不引入消息队列（Redis/Kafka）—— 保持 SQLite + ThreadPool
- 不改变前端框架 —— 继续使用原生 HTML/JS/Tailwind
- 不支持后端代码生成 —— 保持纯前端应用定位
- 不引入 embedding 服务的独立部署 —— 记忆 ≤10 条时纯 LLM 判断

## Decisions

| 决策 | 选项 | 选择 | 理由 |
|------|------|:---:|------|
| Agent 架构 | 单 ReAct Agent vs Planner + ReAct Coder | Planner + ReAct Coder | 架构设计需要结构化思维（Planner），代码实现需要迭代验证（ReAct Coder），两者分开更高效 |
| 工具描述格式 | OpenAI function calling vs 自定义 | OpenAI format | 通用性最好，Anthropic 协议也可适配 |
| 代码执行沙箱 | subprocess Node.js vs Docker vs PyMiniRacer | subprocess Node.js | 简单可控，不需要额外依赖 |
| Hook 失败反馈 | 中断任务 vs 反馈 Agent 修复 vs 静默记录 | 反馈 Agent 修复 | 符合棘轮原则，让 Agent 学习纠正；3 次失败后放过 |
| 长期记忆检索 | 关键词 vs embedding+LLM vs 纯LLM | ≤10条纯LLM，>10条embedding+LLM | 初期记忆少，纯LLM零额外成本；后期按需升级 |
| Skills 数量 | 5 个特定 vs 1 个通用 | 1 个通用 | 当前 Skill 是代码模板，Agent 用工具可以动态生成；通用 Skill 提供领域知识更有价值 |
| 日志存储 | /var/log vs 项目根目录 logs/ | 项目根目录 logs/ | 方便开发调试 |
| 文件隔离 | 仅 requirement_id vs user_id + requirement_id + 路径校验 | user_id + requirement_id + `_validate()` | 三层隔离，防止路径穿越和跨用户访问 |
| 旧代码处理 | 兼容层 + 开关 vs 直接删除 | 直接删除 | 完整重构，不保留回滚 |

## Risks / Trade-offs

- [工具调用增加延迟] 单次需求从 ~10s 增加到 ~60s → 设最大 10 轮限制，前端每步展示让用户感知进度
- [LLM 需要支持 function calling] 部分模型不可用 → 在 LLMClient 中检测，不支持则降级到旧版单次生成
- [沙箱逃逸风险] subprocess 隔离不如容器 → 进程级超时 + 内存限制 + 无网络权限；后续升级到 Docker
- [记忆存储增长] 长期记忆可能膨胀 → 重要性衰减 + 30 天未访问清理 + 用户可手动管理
- [Hook 误拦截] 检查工具本身 bug 导致正确代码被拒 → 3 次失败后自动放过，用户可手动跳过

## Open Questions

<!-- All resolved during design phase -->
