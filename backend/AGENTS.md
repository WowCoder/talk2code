# AI 智能体调用逻辑说明

## 整体流程

```
用户提交需求
     │
     ▼
创建需求记录 (status=pending)
     │
     ▼
启动后台线程处理
     │
     ▼
process_requirement_with_agents(requirement_id)
     │
     ├─ 创建 LLM 实例 (per requirement)
     ├─ 加载历史对话到 Memory
     ├─ 更新状态为 processing
     │
     ▼
按顺序执行 4 个智能体（前面智能体的输出会传递给后面智能体作为上下文）
┌─────────────────────────────────────────────────────────────────┐
│  1. 研究员    →  2. 产品经理  →  3. 架构师  →  4. 工程师          │
│     ↓              ↓             ↓            ↓                 │
│  市场分析      功能规划       技术设计      代码生成            │
│     │              │             │            ▲                 │
│     └──────────────┴─────────────┴────────────┘                 │
│                    传递给工程师作为上下文                          │
└─────────────────────────────────────────────────────────────────┘
     │
     ▼
每个智能体执行步骤:
1. 保存"开始分析"状态到对话历史
2. 发送 SSE 进度消息
3. 调用智能体函数 (LLM) - 传入前面智能体的输出
4. 保存智能体输出到对话历史
5. 提交数据库
6. 发送 SSE 对话更新
```

## 智能体调用链路

### 入口点
```python
# app.py:240-247
thread = threading.Thread(
    target=process_requirement_with_agents,
    args=(requirement_id,),
    daemon=False
).start()
```

### 智能体执行（带上下文传递）
```python
# app.py:502-507
agent_outputs = []  # 收集前面智能体的输出

agents = [
    ('研究员', agent_researcher_with_memory, requirement.content, llm),
    ('产品经理', agent_product_manager_with_memory, requirement.content, llm),
    ('架构师', agent_architect_with_memory, requirement.content, llm),
    ('工程师', agent_engineer_with_memory, requirement.content, llm)
]

for agent_name, agent_func, agent_input, agent_llm in agents:
    # 传递前面智能体的输出作为上下文
    agent_output = agent_func(agent_input, agent_llm, agent_outputs)

    # 收集输出，传递给下一个智能体
    agent_outputs.append({'name': agent_name, 'output': agent_output})
```

### 工程师接收的上下文
```python
# app.py:730-738
def agent_engineer_with_memory(requirement: str, llm, agent_outputs=None) -> str:
    # 构建上下文：整合前面智能体的输出
    context = ""
    if agent_outputs:
        context_parts = []
        for output in agent_outputs:
            context_parts.append(f"{output['name']}输出:\n{output['output']}")
        context = "\n\n".join(context_parts)

    # 上下文包含：
    # - 研究员：市场与需求分析
    # - 产品经理：产品功能规划
    # - 架构师：技术架构设计

    user_prompt = ENGINEER_USER_PROMPT.format(
        requirement=requirement,
        context=context
    )
```

### 智能体函数结构
```python
# prompts.py: 定义 Prompt 模板
RESEARCHER_SYSTEM_PROMPT = "..."
RESEARCHER_USER_PROMPT = "..."

# app.py:660-683: 智能体函数
def agent_researcher_with_memory(requirement: str, llm) -> str:
    system_prompt = RESEARCHER_SYSTEM_PROMPT
    user_prompt = RESEARCHER_USER_PROMPT.format(requirement=requirement)

    if llm:
        response = llm.chat(user_prompt, system_prompt, use_memory=True)
    else:
        response = chat_with_llm(user_prompt, system_prompt)

    return f"【市场与需求分析】\n\n{response}"
```

## LLM 调用层次

```
agent_xxx_with_memory(requirement, llm)
         │
         ▼
┌─────────────────────┐
│  llm.chat()         │ ← LangChain 封装
│  (use_memory=True)  │
└─────────────────────┘
         │
    ┌────┴────┐
    │ 成功    │ 失败
    ▼         ▼
┌─────────┐ ┌──────────────┐
│ 返回响应 │ │ Fallback 模板 │
└─────────┘ └──────────────┘
```

## LangChain Memory 机制

```python
# 1. 创建 LLM 实例 (per requirement)
llm_instance_id = f"requirement_{requirement_id}"
llm = get_llm(llm_instance_id)

# 2. 从数据库加载历史
dialogue_history = requirement.dialogue_history or []
llm.load_memory_from_db(dialogue_history)

# 3. 调用时自动携带历史
llm.chat(user_prompt, system_prompt, use_memory=True)
# ↓ 内部会:
# - 从 memory 读取历史消息
# - 构建 messages 数组 [system, history..., user]
# - 调用 LLM
# - 保存新的对话到 memory
```

## 数据持久化

```
对话历史保存流程:
┌──────────────┐
│ 智能体输出    │
└──────┬───────┘
       ▼
┌─────────────────────────────────┐
│ requirement.dialogue_history    │
│ .append({                       │
│   'role': 'agent',              │
│   'name': '研究员',             │
│   'content': '...',             │
│   'timestamp': '...',           │
│   'status': 'completed'         │
│ })                              │
└──────┬──────────────────────────┘
       ▼
┌──────────────┐
│ db.commit()  │
└──────┬───────┘
       ▼
┌──────────────┐
│ SQLite 数据库 │
└──────────────┘
```

## SSE 实时推送

```python
# 1. 进度消息
SSEMessage.progress_message(agent_name, progress, 'processing')
# → event: progress
#   data: {"current_agent": "研究员", "progress": 25, "status": "processing"}

# 2. 对话消息
SSEMessage.dialogue_message('agent', agent_name, agent_output, timestamp)
# → event: dialogue
#   data: {"role": "agent", "name": "研究员", "content": "...", ...}

# 3. 完成消息
SSEMessage.complete_message(requirement_id)
# → event: complete
#   data: {"requirement_id": 12}
```

## 关键代码位置

| 功能 | 文件 | 行号 |
|------|------|------|
| 智能体处理入口 | app.py | 461-625 |
| 智能体定义 | app.py | 660-853 |
| Prompts 配置 | prompts.py | 全部 |
| LLM 客户端 | langchain_client.py | 全部 |
| 对话持久化 | app.py | 549-584 |
| SSE 推送 | app.py | 527-597 |
| 消息格式 | utils.py | 57-150 |

## 智能体 Prompt 汇总

| 智能体 | 系统 Prompt | 用户 Prompt | 输出前缀 |
|--------|-----------|-----------|---------|
| 研究员 | 产品需求分析师，市场适配性分析 | "用户需求：{requirement}\n\n请分析这个需求的市场适配性。" | 【市场与需求分析】 |
| 产品经理 | 资深产品经理，功能清单拆解 | "用户需求：{requirement}\n\n请为这个需求规划产品功能。" | 【产品功能规划】 |
| 架构师 | 资深系统架构师，**纯前端**技术方案设计 | "产品需求：{requirement}\n\n请为这个应用设计纯前端技术架构（HTML/CSS/JS + LocalStorage）。" | 【技术架构设计】 |
| 工程师 | 资深前端工程师，代码生成 | "请为以下需求生成完整的 Web 应用代码..."<br>**参考前面智能体的输出作为上下文** | (JSON，无前缀) |

## 修改 Prompt 的步骤

1. 编辑 `prompts.py` 中对应的 Prompt 常量
2. 重启后端服务
3. 创建新需求测试效果

> 注意：修改 Prompt 只影响新生成的需求，已有需求的对话历史不会改变。

## 架构设计原则

当前项目定位为**纯前端应用**，架构师智能体输出的技术方案遵循以下原则：

- **无后端服务器**：所有逻辑在浏览器中运行
- **LocalStorage 持久化**：数据存储在用户浏览器本地
- **原生技术栈**：HTML/CSS/JavaScript，无需构建工具
- **Tailwind CSS**：通过 CDN 引入，快速样式开发
