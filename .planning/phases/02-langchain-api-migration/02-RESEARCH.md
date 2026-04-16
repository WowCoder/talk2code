# Phase 2: LangChain API 迁移 - Research

**Researched:** 2026-04-16
**Domain:** LangChain 1.x API Migration, LangGraph StateGraph
**Confidence:** HIGH

## Summary

LangChain 1.x 是一个稳定性优先的发布，核心 API（`langchain-core`、`langgraph`）在 v1.0 中保持向后兼容。项目的现有代码使用 `langchain-core` 和 `langgraph`，这正是推荐的标准包结构。

**主要发现：**
1. **Import 路径已正确** - 代码已使用 `from langchain_core.messages` 和 `from langgraph.graph import StateGraph`，符合 1.x 标准
2. **StateGraph API 未变化** - `add_node`、`add_edge`、`add_conditional_edges`、`compile` 方法签名保持不变
3. **已废弃 API** - `__call__` 和 `run()` 方法已在 1.0 移除，但项目代码未使用这些模式
4. **Python 3.11 兼容** - LangChain 1.x 要求 Python 3.10+，项目已升级到 Python 3.11.11，满足要求

**Primary recommendation:** 项目代码已使用正确的 `langchain-core` 导入路径，主要迁移工作是验证无弃用警告，并确保与 langchain-core 1.2.x 和 langgraph 1.1.x 的兼容性。

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| LLM Client (`llm/client.py`) | API / Backend | — | 直接使用 DashScope API，LangChain 仅作为可选封装 |
| Prompt Templates (`prompts.py`) | API / Backend | — | 字符串模板定义，无框架依赖 |
| Agent Nodes (`agents/nodes.py`) | API / Backend | — | 业务逻辑层，调用 LLM 客户端 |
| Workflow Graph (`agents/workflow.py`) | API / Backend | — | LangGraph 状态图定义，编排层 |
| State Definition (`agents/state.py`) | API / Backend | — | TypedDict 状态定义，数据结构层 |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| langchain-core | 1.2.31 (current) | 核心抽象（Messages, Prompts, Runnables） | LangChain 官方推荐的轻量核心包 [VERIFIED: pip index] |
| langgraph | 1.1.6 (current) | 状态图编排（StateGraph, END） | LangChain 官方图编排库，1.0 后 API 稳定 [VERIFIED: pip index] |
| pydantic | 2.x | 数据验证 | LangChain 内部依赖，项目已使用 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| requests | 2.x | HTTP 请求 | 项目当前 LLM 客户端直接使用 DashScope REST API |
| typing-extensions | 4.x | Python 类型提示增强 | langchain-core 依赖，提供 Annotated, operator 等 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `langchain-core` | `langchain` (full) | `langchain` 包含更多集成但体积更大，`langchain-core` 更轻量 |
| Direct DashScope API | `langchain-dashscope` | 直接 API 调用更可控，但需要手动处理重试/流式；LangChain 集成更简洁但有依赖开销 |

**Installation:**
项目 `requirements.txt` 已指定正确版本：
```bash
langchain-core>=1.0.0
langgraph>=0.1.0
```

**Version verification:**
```bash
$ pip index versions langgraph
langgraph (1.1.6)
Available versions: 1.1.6, 1.1.5, ..., 1.0.0, 0.6.x, ...
```

当前项目已安装 `langchain-core 1.2.31`，满足 `>=1.0.0` 要求。

## Architecture Patterns

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Flask App (app.py)                        │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │           generate_requirement_data()                       │ │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │ │
│  │  │  Researcher  │→│  Product Mgr │→│  Architect   │      │ │
│  │  │   Node       │  │   Node       │  │   Node       │      │ │
│  │  └──────────────┘  └──────────────┘  └──────────────┘      │ │
│  │         ↓                  ↓                  ↓             │ │
│  │  ┌──────────────────────────────────────────────────────┐   │ │
│  │  │              Engineer Node (final)                    │   │ │
│  │  └──────────────────────────────────────────────────────┘   │ │
│  │                            ↓                                  │ │
│  │  ┌──────────────────────────────────────────────────────┐   │ │
│  │  │           CodeGenerator → SSE Push                    │   │ │
│  │  └──────────────────────────────────────────────────────┘   │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
           │
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  LangGraph Workflow (agents/workflow.py)                        │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │  Researcher │───→│ Product Mgr │───→│  Architect  │          │
│  └─────────────┘    └─────────────┘    └──────┬──────┘          │
│                                              │                  │
│                         ┌────────────────────┼──────────────┐   │
│                         │  should_proceed_to_engineer()      │   │
│                         └────────────────────┼──────────────┘   │
│                                              ↓                  │
│                                       ┌─────────────┐           │
│                                       │  Engineer   │           │
│                                       └──────┬──────┘           │
│                                              ↓                  │
│                                       ┌─────────────┐           │
│                                       │    END      │           │
│                                       └─────────────┘           │
└─────────────────────────────────────────────────────────────────┘
           │
           ↓
┌─────────────────────────────────────────────────────────────────┐
│  LLM Client (llm/client.py) → DashScope API                     │
│  - Custom HTTP client (requests)                                │
│  - Stream/Non-stream support                                    │
│  - Retry with exponential backoff                               │
│  - Session memory management                                    │
└─────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure

当前项目结构已符合 LangChain 1.x 标准：

```
backend/
├── llm/
│   └── client.py          # LLM 客户端（自定义 DashScope 实现）
├── agents/
│   ├── state.py           # AgentState TypedDict 定义
│   ├── nodes.py           # 智能体节点函数
│   └── workflow.py        # LangGraph StateGraph 定义
├── prompts.py             # Prompt 模板定义
└── config.py              # 配置管理
```

### Pattern 1: TypedDict State with Operators

**What:** LangGraph 状态定义使用 `TypedDict` 和 `operator.add` 实现自动累积。

**When to use:** 多节点工作流，需要累积输出（如对话历史、智能体输出）。

**Example:**
```python
# Source: backend/agents/state.py
from typing import TypedDict, List, Annotated, Optional
import operator

class AgentState(TypedDict):
    """智能体工作流状态"""
    requirement_id: int
    requirement_content: str
    agent_outputs: Annotated[List[dict], operator.add]  # 自动累积
    current_step: str
    code_files: Optional[List[dict]]
    error: Optional[str]
    dialogue_history: Annotated[List[dict], operator.add]  # 自动累积
    metadata: dict
```

**验证:** 该模式符合 LangGraph 1.x 标准 [VERIFIED: langgraph StateGraph docs]。

### Pattern 2: Sequential Workflow with Conditional Edge

**What:** 顺序执行节点，在关键点使用条件分支。

**When to use:** 需要基于前序节点结果决定是否继续的工作流。

**Example:**
```python
# Source: backend/agents/workflow.py
from langgraph.graph import StateGraph, END

def create_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("product_manager", product_manager_node)
    workflow.add_node("architect", architect_node)
    workflow.add_node("engineer", engineer_node)
    
    workflow.set_entry_point("researcher")
    workflow.add_edge("researcher", "product_manager")
    workflow.add_edge("product_manager", "architect")
    
    # 条件边：架构师 → 工程师
    workflow.add_conditional_edges(
        "architect",
        should_proceed_to_engineer,
        {
            "to_engineer": "engineer",
            "retry_architect": "architect"
        }
    )
    
    workflow.add_edge("engineer", END)
    return workflow.compile()
```

### Anti-Patterns to Avoid

- **直接突变 State:** 节点函数应返回 `dict` 更新，而非修改传入的 `state` 参数。
- **在条件函数中修改状态:** `should_proceed_to_engineer` 等条件函数应只读检查状态。
- **使用 `langchain.schema`:** 已废弃，应使用 `langchain_core.messages`。

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 状态管理 | 自定义状态合并逻辑 | `Annotated[T, operator.add]` | LangGraph 自动处理，避免并发更新冲突 [CITED: langgraph StateGraph] |
| 条件分支 | if/else 硬编码 | `add_conditional_edges` | 声明式定义，支持可视化调试 [CITED: langgraph docs] |
| 消息类型 | 自定义消息类 | `langchain_core.messages.HumanMessage` | 标准化，兼容所有 LangChain 组件 [VERIFIED: langchain-core docs] |
| Prompt 模板 | f-string 拼接 | `ChatPromptTemplate.from_messages` | 支持变量替换、消息历史管理 [CITED: langchain-core prompts] |

**Key insight:** LangChain 1.x 的核心价值在于标准化接口。项目当前自定义 LLM 客户端 (`llm/client.py`) 是合理的（直接使用 DashScope API），但 Prompt 和消息处理应使用 `langchain-core` 标准类型。

## Runtime State Inventory

> 本节适用于重构/迁移阶段。本项目为 API 升级，不涉及运行时状态变更。

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | 无 | 无 — 代码升级为纯代码变更 |
| Live service config | 无 | 无 |
| OS-registered state | 无 | 无 |
| Secrets/env vars | `DASHSCOPE_API_KEY` 等 | 无需变更 — API 端点不变 |
| Build artifacts | 无 | 无 |

## Common Pitfalls

### Pitfall 1: Import Path Confusion
**What goes wrong:** 从旧教程复制 `from langchain.schema import HumanMessage` 导致导入错误。

**Why it happens:** LangChain 在 0.x 到 1.x 迁移中重构了包结构，`langchain.schema` 已移除。

**How to avoid:** 始终使用 `from langchain_core.messages import *` 或 `from langchain.messages import *`。

**Warning signs:** `ModuleNotFoundError: No module named 'langchain.schema'` [CITED: GitHub issue #8527]。

### Pitfall 2: State Mutation in Nodes
**What goes wrong:** 节点函数直接修改 `state["outputs"].append(...)` 而非返回更新。

**Why it happens:** 开发者习惯命令式编程，忽略了 LangGraph 的函数式状态更新模型。

**How to avoid:** 节点函数始终返回 `dict`，如 `return {"outputs": [new_output]}`。

**Warning signs:** 状态更新不累积，节点间数据丢失。

### Pitfall 3: Conditional Edge Double Execution
**What goes wrong:** 当条件边和普通边指向同一节点时，该节点执行两次。

**Why it happens:** LangGraph 0.3.x 到 1.0.x 的已知问题 [CITED: GitHub issue #6166]。

**How to avoid:** 避免在同一节点同时使用 `add_edge` 和 `add_conditional_edges` 指向同一目标。

**Warning signs:** 节点日志显示执行两次，输出重复。

### Pitfall 4: Deprecated Method Usage
**What goes wrong:** 使用 `chain.run()` 或 `model.__call__()` 触发弃用警告或错误。

**Why it happens:** 这些方法在 0.1.x 弃用，1.0 移除。

**How to avoid:** 使用 `invoke()` 替代所有调用模式。

**Warning signs:** `LangChainDeprecationWarning: The function run was deprecated...` [VERIFIED: langchain-core deprecation]。

### Pitfall 5: Visualization Bugs in LangGraph 1.x
**What goes wrong:** 生成的 Mermaid 图显示不正确的条件边（如意外指向 `__end__`）。

**Why it happens:** LangGraph 1.0.x 可视化组件的已知 bug [CITED: GitHub issue #4394]。

**How to avoid:** 以实际代码为准，不依赖可视化调试关键逻辑。

**Warning signs:** 图表显示额外边，但实际执行正常。

## Code Examples

### Creating Messages (langchain-core 1.x)
```python
# Source: https://reference.langchain.com/python/langchain-core/messages
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

messages = [
    SystemMessage(content="You are a helpful assistant."),
    HumanMessage(content="What is your name?")
]

# With ChatModel
response = model.invoke(messages)
# Returns: AIMessage(content="My name is...")
```

### Using ChatPromptTemplate
```python
# Source: https://reference.langchain.com/python/langchain-core/prompts/chat/ChatPromptTemplate
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a professional {role}."),
    ("human", "{input}")
])

# Invoke
messages = prompt.invoke({"role": "developer", "input": "Hello"})
# Returns: ChatMessageList with formatted messages
```

### Defining LangGraph State
```python
# Source: backend/agents/state.py (current implementation)
from typing import TypedDict, List, Annotated, Optional
import operator

class AgentState(TypedDict):
    requirement_id: int
    requirement_content: str
    agent_outputs: Annotated[List[dict], operator.add]
    current_step: str
    code_files: Optional[List[dict]]
    error: Optional[str]
    dialogue_history: Annotated[List[dict], operator.add]
    metadata: dict
```

### Creating StateGraph
```python
# Source: backend/agents/workflow.py (current implementation)
from langgraph.graph import StateGraph, END

def create_workflow():
    workflow = StateGraph(AgentState)
    workflow.add_node("node_name", node_function)
    workflow.add_edge("start", "end")
    workflow.add_conditional_edges(
        "node",
        condition_function,
        {"true": "next_node", "false": "retry"}
    )
    workflow.set_entry_point("first_node")
    return workflow.compile()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `from langchain.schema import HumanMessage` | `from langchain_core.messages import HumanMessage` | langchain-core 0.1.x | 包结构简化，核心抽象移至 `langchain-core` |
| `chain.run(input)` | `chain.invoke(input)` | langchain-core 0.1.7 (deprecated), 1.0 (removed) | LCEL 统一接口 |
| `model.__call__(input)` | `model.invoke(input)` | langchain-core 0.1.7 (deprecated), 1.0 (removed) | Runnable 标准方法 |
| `langchain.prebuilt` | `langchain.agents` | langgraph 1.0 | 预构建智能体移至 `langchain` 命名空间 |

**Deprecated/outdated:**
- `langchain.schema.*`: 已移除，使用 `langchain_core.*` 或 `langchain.*` 新路径
- `ConversationChain`: 0.2.7 弃用，1.0 移除，使用 LCEL 组合模式
- `LLMChain`: 0.1.x 弃用，1.0 移除，使用 `prompt | model` LCEL 链

## Assumptions Log

> 本节列出所有标记为 `[ASSUMED]` 的声明。本项目研究主要基于官方文档和已验证的已安装版本，无假设声明。

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| — | — | — | 无 — 所有声明已验证或引用 |

**验证状态:** 本研究无 `[ASSUMED]` 声明，所有信息来自：
- [VERIFIED] `pip index versions` 确认当前版本
- [VERIFIED] 项目代码审查确认导入路径正确
- [CITED] LangChain 官方文档和 GitHub issues

## Open Questions

1. **是否需要迁移到 `langchain-dashscope` 集成？**
   - What we know: 当前使用自定义 `requests` 客户端直接调用 DashScope API
   - What's unclear: 官方 `langchain-dashscope` 是否提供更简洁的接口
   - Recommendation: 保持现状，自定义客户端已满足需求且更可控

2. **是否需要添加 LangSmith 追踪？**
   - What we know: LangChain 1.x 与 LangSmith 深度集成
   - What's unclear: 项目是否需要生产环境的 LLM 调用监控
   - Recommendation: Phase 3 测试阶段评估

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Runtime | ✓ | 3.11.11 | — |
| langchain-core | Prompts, Messages | ✓ | 1.2.31 | — |
| langgraph | StateGraph | ✓ (installable) | 1.1.6 | — |
| requests | LLM Client | ✓ | 2.x | — |
| DashScope API | LLM | ✓ | — | 需要 API Key |

**Missing dependencies with no fallback:**
- 无 — 所有依赖已安装或可通过 `pip install -r requirements.txt` 安装

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.x + pytest-cov 4.x + pytest-mock 3.x |
| Config file | `pytest.ini` or `pyproject.toml` (需确认) |
| Quick run command | `pytest -x` |
| Full suite command | `pytest --cov=backend` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| API-01 | Import statements use `langchain_core.*` | unit | `pytest tests/test_imports.py -x` | ❌ Wave 0 |
| API-02 | `llm/client.py` uses new API | unit | `pytest tests/test_llm_client.py -x` | ❌ Wave 0 |
| API-03 | `agents/nodes.py` uses new prompts | unit | `pytest tests/test_nodes.py -x` | ❌ Wave 0 |
| API-04 | `agents/workflow.py` StateGraph works | integration | `pytest tests/test_workflow.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest -x` (fast fail)
- **Per wave merge:** `pytest --cov=backend` (coverage check)
- **Phase gate:** All tests green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_imports.py` — verify all imports from `langchain_core.*`
- [ ] `tests/test_llm_client.py` — LLM client integration tests
- [ ] `tests/test_nodes.py` — node function unit tests
- [ ] `tests/test_workflow.py` — LangGraph workflow integration tests
- [ ] `tests/conftest.py` — shared fixtures (mock LLM responses)

## Security Domain

> Phase 2 is code-only (API migration), no new security controls introduced.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Phase 1 covered (Flask-JWT-Extended) |
| V3 Session Management | no | Phase 1 covered |
| V4 Access Control | no | N/A |
| V5 Input Validation | yes | pydantic 2.x (already in requirements) |
| V6 Cryptography | no | N/A |

### Known Threat Patterns for LangChain

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Prompt Injection | Tampering | Input sanitization, output validation |
| LLM API Key Exposure | Information Disclosure | Environment variables only (see `config.py`) |
| State Pollution | Tampering | TypedDict validation, reducer functions |

## Sources

### Primary (HIGH confidence)
- [LangChain v1 migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1) - Import paths, deprecated APIs
- [LangGraph v1 release notes](https://changelog.langchain.com/announcements/langgraph-1-0-is-now-generally-available) - StateGraph API stability
- [langchain-core deprecation docs](https://aidoczh.com/langchain/v0.2/docs/versions/v0_2/deprecations/) - `__call__`, `run()` removal
- [LangChain Reference Docs](https://reference.langchain.com/python/langchain-core) - Messages, Prompts API
- [GitHub issue #8527](https://github.com/langchain-ai/langchain/issues/8527) - `langchain.schema` import error
- [GitHub issue #6166](https://github.com/langchain-ai/langgraph/issues/6166) - Conditional edge double execution
- [pip index](https://pypi.org/pypi/langgraph/json) - Current version 1.1.6

### Secondary (MEDIUM confidence)
- WebSearch results on LangChain 1.x migration patterns
- Community tutorials (Medium, Zhihu) on LangGraph best practices

### Tertiary (LOW confidence)
- Reddit discussions on LangGraph limitations
- Third-party blog posts on state management patterns

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - Verified against pip registry and official docs
- Architecture: HIGH - Based on current project code review
- Pitfalls: HIGH - Cross-referenced with GitHub issues and official troubleshooting docs

**Research date:** 2026-04-16
**Valid until:** 2026-07-16 (90 days - LangChain 1.x API is stable, no breaking changes until 2.0)
