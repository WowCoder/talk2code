# -*- coding: utf-8 -*-
"""
AI 智能体 Prompts 配置
所有智能体的系统提示词和用户提示词模板都定义在这里
"""

from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate

# ==================== 智能体 Prompts ====================

# -------------------------
# 1. 研究员智能体
# 职责：分析市场需求和可行性
# -------------------------

# Deprecated: Use RESEARCHER_PROMPT instead
RESEARCHER_SYSTEM_PROMPT = """你是一位专业的产品需求分析师。请分析用户的产品需求，给出市场适配性分析。

请按照以下格式输出：
1. 市场需求：分析该类型应用的市场需求情况
2. 核心功能定位：建议的核心功能方向
3. 技术可行性：评估技术实现难度
4. 建议：给出架构和技术选型建议

保持简洁专业，控制在 300 字以内。"""

# Deprecated: Use RESEARCHER_PROMPT instead
RESEARCHER_USER_PROMPT = """用户需求：{requirement}

请分析这个需求的市场适配性。"""

# LangChain ChatPromptTemplate format (recommended)
RESEARCHER_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "你是一位专业的产品需求分析师。请分析用户的产品需求，给出市场适配性分析。\n\n"
        "请按照以下格式输出：\n"
        "1. 市场需求：分析该类型应用的市场需求情况\n"
        "2. 核心功能定位：建议的核心功能方向\n"
        "3. 技术可行性：评估技术实现难度\n"
        "4. 建议：给出架构和技术选型建议\n\n"
        "保持简洁专业，控制在 300 字以内。"
    ),
    HumanMessagePromptTemplate.from_template("用户需求：{requirement}\n\n请分析这个需求的市场适配性。")
])

# -------------------------
# 2. 产品经理智能体
# 职责：拆解需求，生成功能清单
# -------------------------
# Deprecated: Use PRODUCT_MANAGER_PROMPT instead
PRODUCT_MANAGER_SYSTEM_PROMPT = """你是一位资深产品经理。请根据用户需求拆解功能清单。

请按照以下格式输出：
1. 核心功能清单：列出 3-5 个主要功能模块
2. 交互逻辑：描述用户操作流程
3. 页面结构：建议的页面组成
4. 用户体验：设计建议

保持简洁，控制在 300 字以内。"""

# Deprecated: Use PRODUCT_MANAGER_PROMPT instead
PRODUCT_MANAGER_USER_PROMPT = """用户需求：{requirement}

请为这个需求规划产品功能。"""

# LangChain ChatPromptTemplate format (recommended)
PRODUCT_MANAGER_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "你是一位资深产品经理。请根据用户需求拆解功能清单。\n\n"
        "请按照以下格式输出：\n"
        "1. 核心功能清单：列出 3-5 个主要功能模块\n"
        "2. 交互逻辑：描述用户操作流程\n"
        "3. 页面结构：建议的页面组成\n"
        "4. 用户体验：设计建议\n\n"
        "保持简洁，控制在 300 字以内。"
    ),
    HumanMessagePromptTemplate.from_template("用户需求：{requirement}\n\n请为这个需求规划产品功能。")
])

# -------------------------
# 3. 架构师智能体
# 职责：设计技术方案
# -------------------------

# Deprecated: Use ARCHITECT_PROMPT instead
ARCHITECT_SYSTEM_PROMPT = """你是一位资深系统架构师。请根据产品需求设计纯前端技术方案。

重要：这是一个纯前端应用，不需要后端服务器，所有数据使用 LocalStorage 存储。

请按照以下格式输出：
1. 技术栈选择：前端框架/库（使用原生 HTML/CSS/JS）、UI 组件库（如 Tailwind CSS）、数据持久化方案（LocalStorage）
2. 数据结构：核心数据模型设计（使用 JavaScript 对象/数组，存储于 LocalStorage）
3. 组件设计：主要组件/模块划分（HTML 结构、CSS 样式、JS 逻辑）
4. 代码组织：项目文件结构建议（index.html、style.css、script.js）

保持简洁，控制在 300 字以内。"""

# Deprecated: Use ARCHITECT_PROMPT instead
ARCHITECT_USER_PROMPT = """产品需求：{requirement}

请为这个应用设计纯前端技术架构（HTML/CSS/JavaScript + LocalStorage 数据持久化）。"""

# LangChain ChatPromptTemplate format (recommended)
ARCHITECT_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "你是一位资深系统架构师。请根据产品需求设计纯前端技术方案。\n\n"
        "重要：这是一个纯前端应用，不需要后端服务器，所有数据使用 LocalStorage 存储。\n\n"
        "请按照以下格式输出：\n"
        "1. 技术栈选择：前端框架/库（使用原生 HTML/CSS/JS）、UI 组件库（如 Tailwind CSS）、数据持久化方案（LocalStorage）\n"
        "2. 数据结构：核心数据模型设计（使用 JavaScript 对象/数组，存储于 LocalStorage）\n"
        "3. 组件设计：主要组件/模块划分（HTML 结构、CSS 样式、JS 逻辑）\n"
        "4. 代码组织：项目文件结构建议（index.html、style.css、script.js）\n\n"
        "保持简洁，控制在 300 字以内。"
    ),
    HumanMessagePromptTemplate.from_template("产品需求：{requirement}\n\n请为这个应用设计纯前端技术架构（HTML/CSS/JavaScript + LocalStorage 数据持久化）。")
])

# -------------------------
# 4. 工程师智能体
# 职责：生成代码
# -------------------------

# Deprecated: Use ENGINEER_PROMPT instead
ENGINEER_SYSTEM_PROMPT = """你是一位资深前端工程师。请根据需求生成完整的 Web 应用代码。

重要要求：
1. **实现核心功能** - 根据产品功能清单，实现所有规划的功能
2. **代码完整可运行** - 包含所有必要的 HTML 结构、CSS 样式、JavaScript 逻辑

技术要求：
1. 生成 3 个文件：index.html、style.css、script.js
2. 使用原生 HTML/CSS/JavaScript，不依赖构建工具
3. 可以使用 Tailwind CSS CDN 进行样式设计
4. 数据使用 LocalStorage 持久化

输出格式：
以 JSON 数组格式返回：
[{"filename": "index.html", "content": "..."}, {"filename": "style.css", "content": "..."}, {"filename": "script.js", "content": "..."}]

不要输出其他解释文字，只返回 JSON。"""

# Deprecated: Use ENGINEER_PROMPT instead
ENGINEER_USER_PROMPT = """请为以下需求生成完整的 Web 应用代码：

用户需求：{requirement}

{context}

请严格按照产品功能规划和技术架构设计来实现代码。"""

# LangChain ChatPromptTemplate format (recommended)
ENGINEER_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template(
        "你是一位资深前端工程师。请根据需求生成完整的 Web 应用代码。\n\n"
        "重要要求：\n"
        "1. **实现核心功能** - 根据产品功能清单，实现所有规划的功能\n"
        "2. **代码完整可运行** - 包含所有必要的 HTML 结构、CSS 样式、JavaScript 逻辑\n\n"
        "技术要求：\n"
        "1. 生成 3 个文件：index.html、style.css、script.js\n"
        "2. 使用原生 HTML/CSS/JavaScript，不依赖构建工具\n"
        "3. 可以使用 Tailwind CSS CDN 进行样式设计\n"
        "4. 数据使用 LocalStorage 持久化\n\n"
        "输出格式：\n"
        "以 JSON 数组格式返回：\n"
        "[{\"filename\": \"index.html\", \"content\": \"...\"}, {\"filename\": \"style.css\", \"content\": \"...\"}, {\"filename\": \"script.js\", \"content\": \"...\"}]\n\n"
        "不要输出其他解释文字，只返回 JSON。"
    ),
    HumanMessagePromptTemplate.from_template("请为以下需求生成完整的 Web 应用代码：\n\n用户需求：{requirement}\n\n{context}\n\n请严格按照产品功能规划和技术架构设计来实现代码。")
])

# 工程师的上下文模板
ENGINEER_CONTEXT_PROMPT = """---
前面的讨论：
{context}
---
"""

# ==================== 代码修改 Diff Prompts ====================

CODE_EDIT_SYSTEM_PROMPT = """你是一位专业的 AI 编程助手，帮助用户修改代码。

当用户要求修改代码时，请返回 unified diff 格式，而不是完整代码。

**DIFF 格式说明：**

```diff
--- a/文件名
+++ b/文件名
@@ -原起始行，原行数 +新起始行，新行数 @@
 上下文行（不变）
-删除的行
+新增的行
 上下文行（不变）
```

**重要规则：**
1. 只返回有修改的文件的 diff
2. 上下文行必须与原代码完全匹配（用于精确定位）
3. 每个 hunk 至少包含 3 行上下文
4. 行号必须准确
5. 如果修改多处，使用多个 @@ hunk

**返回格式：**
直接返回 diff 内容，可选加上简短说明。

示例：
```diff
--- a/script.js
+++ b/script.js
@@ -10,7 +10,8 @@
 function init() {
     setupUI();
-    console.log('init');
+    setupEvents();
+    console.log('ready');
 }
```
"""

CODE_EDIT_USER_PROMPT = """用户需求：{requirement}

当前代码文件：
{current_code}

用户修改请求：{user_message}

---
历史对话上下文：
{dialogue_context}
---

当前是第 {modification_count} 次代码修改。

请返回修改的 diff。如果没有需要修改的代码，只返回文字说明。"""

# Note: CODE_EDIT_PROMPT kept as string templates (not ChatPromptTemplate)
# because the example code contains dots/brackets that conflict with f-string parsing.
# Use CODE_EDIT_SYSTEM_PROMPT and CODE_EDIT_USER_PROMPT strings directly for code editing.


# ==================== 智能体配置 ====================

# 智能体执行顺序
AGENT_ORDER = ['researcher', 'product_manager', 'architect', 'engineer']

# 智能体名称映射（用于前端显示）
AGENT_NAMES = {
    'researcher': '研究员',
    'product_manager': '产品经理',
    'architect': '架构师',
    'engineer': '工程师'
}

# 智能体 Prompt 配置
AGENT_PROMPTS = {
    'researcher': {
        'system': RESEARCHER_SYSTEM_PROMPT,
        'user': RESEARCHER_USER_PROMPT,
        'output_prefix': '【市场与需求分析】\n\n'
    },
    'product_manager': {
        'system': PRODUCT_MANAGER_SYSTEM_PROMPT,
        'user': PRODUCT_MANAGER_USER_PROMPT,
        'output_prefix': '【产品功能规划】\n\n'
    },
    'architect': {
        'system': ARCHITECT_SYSTEM_PROMPT,
        'user': ARCHITECT_USER_PROMPT,
        'output_prefix': '【技术架构设计】\n\n'
    },
    'engineer': {
        'system': ENGINEER_SYSTEM_PROMPT,
        'user': ENGINEER_USER_PROMPT,
        'output_prefix': ''  # 工程师输出是 JSON，不需要前缀
    }
}


# ==================== Fallback 提示词 ====================

# 当 LLM 调用失败时使用的预设回复
FALLBACK_RESPONSES = {
    'researcher': """基于您的需求「{requirement}...」，我进行了以下分析：

1. **市场需求**：该类型应用在市场上有较高需求，用户对简洁易用的工具类应用青睐有加

2. **核心功能定位**：
   - 核心功能应聚焦在主要用途上
   - 交互设计应简洁直观
   - 性能要求：响应迅速，无卡顿

3. **技术可行性**：使用现代 Web 技术栈可快速实现，开发周期短

4. **建议**：采用前后端分离架构，确保良好的可维护性和扩展性

[注：LLM 调用失败，使用预设模板]""",

    'product_manager': """针对「{requirement}...」，我规划了以下功能：

1. **核心功能清单**：
   - 主要功能模块 1：核心业务功能
   - 主要功能模块 2：数据管理功能
   - 主要功能模块 3：用户交互功能

2. **交互逻辑**：
   - 用户进入应用 → 查看主界面
   - 用户执行操作 → 实时反馈
   - 数据变更 → 自动保存

3. **页面结构**：
   - 首页：简洁的输入/操作区域
   - 功能区：核心功能展示和操作
   - 结果区：实时展示操作结果

4. **用户体验**：极简设计风格，突出核心功能

[注：LLM 调用失败，使用预设模板]""",

    'architect': """针对「{requirement}...」，我设计了以下技术方案：

1. **技术栈选择**：
   - 前端：HTML5 + CSS3 + JavaScript (原生)
   - 样式：Tailwind CSS (实用优先)
   - 编辑器：CodeMirror (代码高亮)

2. **数据结构**：
   - 核心数据模型：基于需求设计
   - 数据存储：本地存储 (LocalStorage) + 可选后端持久化

3. **组件设计**：
   - UI 组件：简洁、模块化
   - 逻辑层：清晰的事件处理
   - 数据层：统一的状态管理

4. **代码组织**：
   - index.html：页面结构
   - style.css：样式定义
   - script.js：交互逻辑

[注：LLM 调用失败，使用预设模板]"""
}


# ==================== Fallback 代码生成 ====================

def generate_fallback_code(requirement: str) -> list:
    """
    生成备用代码（当 LLM 失败时使用）
    """
    requirement_lower = requirement.lower()

    if '待办' in requirement or 'todo' in requirement_lower or '清单' in requirement:
        return _generate_todo_app_code()
    elif '计算器' in requirement or '计算' in requirement:
        return _generate_calculator_app_code()
    elif '笔记' in requirement or '备忘录' in requirement:
        return _generate_note_app_code()
    elif '日历' in requirement or '日程' in requirement or 'calendar' in requirement_lower:
        return _generate_calendar_app_code()
    else:
        return _generate_generic_app_code(requirement)


def _generate_todo_app_code() -> list:
    """生成待办清单应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>待办清单 App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-2xl">
        <h1 class="text-4xl font-bold text-center text-gray-800 mb-8">📝 待办清单</h1>
        <div class="bg-white rounded-lg shadow-md p-6 mb-6">
            <div class="flex gap-3">
                <input type="text" id="todoInput" class="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="添加新的待办事项..."/>
                <button id="addBtn" class="px-6 py-3 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors font-medium">添加</button>
            </div>
        </div>
        <div class="flex gap-2 mb-4">
            <button class="filter-btn active" data-filter="all">全部</button>
            <button class="filter-btn" data-filter="active">未完成</button>
            <button class="filter-btn" data-filter="completed">已完成</button>
        </div>
        <div class="bg-white rounded-lg shadow-md">
            <ul id="todoList" class="divide-y divide-gray-200"></ul>
            <div id="emptyState" class="text-center py-12 text-gray-500"><p>暂无待办事项，添加一个吧！</p></div>
        </div>
        <div class="text-center mt-4 text-gray-500 text-sm">
            <span id="totalCount">0</span> 项待办 · <span id="completedCount">0</span> 项已完成
        </div>
    </div>
    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 32
        },
        {
            'filename': 'style.css',
            'content': '''/* 待办清单 App 样式 */
.filter-btn {
    @apply px-4 py-2 rounded-lg border transition-colors text-sm font-medium;
}
.filter-btn.active {
    @apply bg-blue-500 text-white border-blue-500;
}
.filter-btn:not(.active) {
    @apply bg-white text-gray-600 border-gray-300 hover:bg-gray-50;
}
.todo-item {
    @apply flex items-center gap-3 p-4 hover:bg-gray-50 transition-colors;
}
.todo-item.completed .todo-text {
    @apply line-through text-gray-400;
}
.todo-checkbox {
    @apply w-5 h-5 rounded border-gray-300 text-blue-500 focus:ring-blue-500 cursor-pointer;
}
.delete-btn {
    @apply px-3 py-1 text-red-500 hover:bg-red-50 rounded transition-colors text-sm;
}
#emptyState { display: none; }
#todoList:empty + #emptyState { display: block; }
''',
            'status': 'completed',
            'total_lines': 20
        },
        {
            'filename': 'script.js',
            'content': '''// 待办清单 App 逻辑
let todos = JSON.parse(localStorage.getItem('todos') || '[]');

function save() {
    localStorage.setItem('todos', JSON.stringify(todos));
    render();
}

function render() {
    const list = document.getElementById('todoList');
    const empty = document.getElementById('emptyState');
    list.innerHTML = '';
    todos.forEach((todo, index) => {
        const item = document.createElement('li');
        item.className = `todo-item ${todo.completed ? 'completed' : ''}`;
        item.innerHTML = `
            <input type="checkbox" class="todo-checkbox" ${todo.completed ? 'checked' : ''} onchange="toggle(${index})">
            <span class="todo-text">${todo.text}</span>
            <button class="delete-btn" onclick="remove(${index})">删除</button>
        `;
        list.appendChild(item);
    });
    empty.style.display = todos.length ? 'none' : 'block';
    document.getElementById('totalCount').textContent = todos.length;
    document.getElementById('completedCount').textContent = todos.filter(t => t.completed).length;
}

function add() {
    const input = document.getElementById('todoInput');
    const text = input.value.trim();
    if (text) {
        todos.push({ text, completed: false });
        input.value = '';
        save();
    }
}

function toggle(index) {
    todos[index].completed = !todos[index].completed;
    save();
}

function remove(index) {
    todos.splice(index, 1);
    save();
}

document.getElementById('addBtn').addEventListener('click', add);
document.getElementById('todoInput').addEventListener('keypress', e => { if (e.key === 'Enter') add(); });
document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    });
});

render();
''',
            'status': 'completed',
            'total_lines': 45
        }
    ]


def _generate_calculator_app_code() -> list:
    """生成计算器应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>计算器 App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gray-100 min-h-screen flex items-center justify-center">
    <div class="bg-white rounded-2xl shadow-2xl p-6 w-full max-w-sm">
        <div id="display" class="text-right text-4xl font-mono p-4 bg-gray-100 rounded-lg mb-4 min-h-[80px] overflow-hidden">0</div>
        <div class="grid grid-cols-4 gap-2">
            <button class="calc-btn op" data-op="C">C</button>
            <button class="calc-btn op" data-op="("> (</button>
            <button class="calc-btn op" data-op=")"> )</button>
            <button class="calc-btn op" data-op="/"> ÷</button>
            <button class="calc-btn num" data-num="7">7</button>
            <button class="calc-btn num" data-num="8">8</button>
            <button class="calc-btn num" data-num="9">9</button>
            <button class="calc-btn op" data-op="*">×</button>
            <button class="calc-btn num" data-num="4">4</button>
            <button class="calc-btn num" data-num="5">5</button>
            <button class="calc-btn num" data-num="6">6</button>
            <button class="calc-btn op" data-op="-"> −</button>
            <button class="calc-btn num" data-num="1">1</button>
            <button class="calc-btn num" data-num="2">2</button>
            <button class="calc-btn num" data-num="3">3</button>
            <button class="calc-btn op" data-op="+"> +</button>
            <button class="calc-btn num" data-num="0">0</button>
            <button class="calc-btn num" data-num=".">.</button>
            <button class="calc-btn op" data-op="back">⌫</button>
            <button class="calc-btn eq" data-op="=">=</button>
        </div>
    </div>
    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 28
        },
        {
            'filename': 'style.css',
            'content': '''/* 计算器 App 样式 */
.calc-btn {
    @apply p-4 text-xl font-semibold rounded-lg transition-all active:scale-95;
}
.calc-btn.num {
    @apply bg-gray-100 hover:bg-gray-200 text-gray-800;
}
.calc-btn.op {
    @apply bg-orange-100 hover:bg-orange-200 text-orange-600;
}
.calc-btn.eq {
    @apply bg-orange-500 hover:bg-orange-600 text-white;
}
#display {
    word-break: break-all;
}
''',
            'status': 'completed',
            'total_lines': 15
        },
        {
            'filename': 'script.js',
            'content': '''// 计算器 App 逻辑
let expression = '';
const display = document.getElementById('display');

function updateDisplay() {
    display.textContent = expression || '0';
}

function handleNum(num) {
    expression += num;
    updateDisplay();
}

function handleOp(op) {
    if (op === 'C') {
        expression = '';
    } else if (op === 'back') {
        expression = expression.slice(0, -1);
    } else if (op === '=') {
        try {
            expression = String(eval(expression));
        } catch (e) {
            expression = 'Error';
        }
    } else {
        expression += op;
    }
    updateDisplay();
}

document.querySelectorAll('.num').forEach(btn => {
    btn.addEventListener('click', () => handleNum(btn.dataset.num));
});

document.querySelectorAll('.op').forEach(btn => {
    btn.addEventListener('click', () => handleOp(btn.dataset.op));
});

document.querySelector('.eq').addEventListener('click', () => handleOp('='));

document.addEventListener('keydown', e => {
    if (e.key >= '0' && e.key <= '9') handleNum(e.key);
    else if (['+', '-', '*', '/', '(', ')', '.'].includes(e.key)) handleOp(e.key);
    else if (e.key === 'Enter' || e.key === '=') handleOp('=');
    else if (e.key === 'Backspace') handleOp('back');
    else if (e.key === 'Escape') handleOp('C');
});
''',
            'status': 'completed',
            'total_lines': 32
        }
    ]


def _generate_note_app_code() -> list:
    """生成笔记应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>笔记 App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-6xl">
        <h1 class="text-4xl font-bold text-center text-gray-800 mb-8">📝 笔记应用</h1>
        <div class="flex gap-4 mb-6">
            <input type="text" id="noteTitle" class="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500" placeholder="笔记标题..."/>
            <button id="addBtn" class="px-6 py-3 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors font-medium">新建笔记</button>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" id="notesGrid"></div>
        <div id="emptyState" class="text-center py-12 text-gray-500"><p>暂无笔记，创建一个吧！</p></div>
    </div>
    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 18
        },
        {
            'filename': 'style.css',
            'content': '''/* 笔记 App 样式 */
.note-card {
    @apply bg-white rounded-lg shadow-md p-4 hover:shadow-lg transition-shadow cursor-pointer;
}
.note-card:hover .delete-note {
    @apply opacity-100;
}
.delete-note {
    @apply absolute top-2 right-2 px-2 py-1 text-red-500 hover:bg-red-50 rounded opacity-0 transition-opacity text-sm;
}
.note-title {
    @apply font-semibold text-gray-800 text-lg mb-2;
}
.note-preview {
    @apply text-gray-600 text-sm line-clamp-3;
}
''',
            'status': 'completed',
            'total_lines': 14
        },
        {
            'filename': 'script.js',
            'content': '''// 笔记 App 逻辑
let notes = JSON.parse(localStorage.getItem('notes') || '[]');

function save() {
    localStorage.setItem('notes', JSON.stringify(notes));
    render();
}

function render() {
    const grid = document.getElementById('notesGrid');
    const empty = document.getElementById('emptyState');
    grid.innerHTML = '';
    notes.forEach((note, index) => {
        const card = document.createElement('div');
        card.className = 'note-card relative';
        card.innerHTML = `
            <button class="delete-note" onclick="event.stopPropagation(); remove(${index})">删除</button>
            <div class="note-title">${note.title}</div>
            <div class="note-preview">${note.content}</div>
            <div class="text-xs text-gray-400 mt-2">${note.updatedAt}</div>
        `;
        card.onclick = () => edit(index);
        grid.appendChild(card);
    });
    empty.style.display = notes.length ? 'none' : 'block';
}

function add() {
    const titleInput = document.getElementById('noteTitle');
    const title = titleInput.value.trim() || '无标题笔记';
    const note = {
        title,
        content: '点击编辑笔记内容...',
        updatedAt: new Date().toLocaleString('zh-CN')
    };
    notes.unshift(note);
    titleInput.value = '';
    save();
}

function edit(index) {
    const note = notes[index];
    const newContent = prompt('编辑笔记内容:', note.content);
    if (newContent !== null) {
        note.content = newContent;
        note.updatedAt = new Date().toLocaleString('zh-CN');
        save();
    }
}

function remove(index) {
    if (confirm('确定删除此笔记？')) {
        notes.splice(index, 1);
        save();
    }
}

document.getElementById('addBtn').addEventListener('click', add);
document.getElementById('noteTitle').addEventListener('keypress', e => { if (e.key === 'Enter') add(); });

render();
''',
            'status': 'completed',
            'total_lines': 48
        }
    ]


def _generate_calendar_app_code() -> list:
    """生成日历应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>日历 App</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8 max-w-4xl">
        <div class="bg-white rounded-lg shadow-md p-6">
            <div class="flex justify-between items-center mb-6">
                <button id="prevBtn" class="px-4 py-2 bg-gray-200 hover:bg-gray-300 rounded-lg">◀ 上月</button>
                <h2 id="currentMonth" class="text-2xl font-bold text-gray-800"></h2>
                <button id="nextBtn" class="px-4 py-2 bg-gray-200 hover:bg-gray-300 rounded-lg">下月 ▶</button>
            </div>
            <div class="grid grid-cols-7 gap-2 mb-2 text-center font-semibold text-gray-600">
                <div>日</div><div>一</div><div>二</div><div>三</div><div>四</div><div>五</div><div>六</div>
            </div>
            <div id="calendarGrid" class="grid grid-cols-7 gap-2"></div>
        </div>
        <div class="bg-white rounded-lg shadow-md p-6 mt-4">
            <h3 class="font-semibold text-gray-800 mb-4">📅 日程</h3>
            <div id="eventList" class="space-y-2"></div>
        </div>
    </div>
    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 28
        },
        {
            'filename': 'style.css',
            'content': '''/* 日历 App 样式 */
.calendar-day {
    @apply aspect-square border border-gray-200 rounded-lg p-2 hover:bg-gray-50 cursor-pointer transition-colors;
}
.calendar-day.today {
    @apply bg-blue-500 text-white font-bold;
}
.calendar-day.other-month {
    @apply text-gray-300;
}
.calendar-day.has-event::after {
    content: '';
    @apply absolute bottom-1 left-1/2 transform -translate-x-1/2 w-1 h-1 bg-blue-500 rounded-full;
}
''',
            'status': 'completed',
            'total_lines': 13
        },
        {
            'filename': 'script.js',
            'content': '''// 日历 App 逻辑
let currentDate = new Date();
let events = JSON.parse(localStorage.getItem('events') || '{}');

function saveEvents() {
    localStorage.setItem('events', JSON.stringify(events));
}

function getEventsKey(date) {
    return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function renderCalendar() {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth();
    document.getElementById('currentMonth').textContent = `${year}年${month + 1}月`;

    const firstDay = new Date(year, month, 1);
    const lastDay = new Date(year, month + 1, 0);
    const startDay = firstDay.getDay();
    const totalDays = lastDay.getDate();

    const grid = document.getElementById('calendarGrid');
    grid.innerHTML = '';

    const today = new Date();
    for (let i = 0; i < startDay; i++) {
        grid.innerHTML += '<div class="calendar-day other-month"></div>';
    }
    for (let day = 1; day <= totalDays; day++) {
        const date = new Date(year, month, day);
        const isToday = date.toDateString() === today.toDateString();
        const hasEvent = events[getEventsKey(date)]?.length > 0;
        grid.innerHTML += `<div class="calendar-day ${isToday ? 'today' : ''} ${hasEvent ? 'has-event' : ''}" data-date="${date.toISOString()}">${day}</div>`;
    }
    grid.querySelectorAll('.calendar-day').forEach(el => {
        el.addEventListener('click', () => showEvents(new Date(el.dataset.date)));
    });
}

function showEvents(date) {
    const key = getEventsKey(date);
    const eventList = document.getElementById('eventList');
    const dayEvents = events[key] || [];
    eventList.innerHTML = dayEvents.map(e => `<div class="p-2 bg-blue-50 rounded">${e}</div>`).join('') || '<p class="text-gray-500">暂无日程</p>';
}

function addEvent(date) {
    const content = prompt('添加日程:');
    if (content) {
        const key = getEventsKey(date);
        if (!events[key]) events[key] = [];
        events[key].push(content);
        saveEvents();
        renderCalendar();
        showEvents(date);
    }
}

document.getElementById('prevBtn').addEventListener('click', () => {
    currentDate.setMonth(currentDate.getMonth() - 1);
    renderCalendar();
});
document.getElementById('nextBtn').addEventListener('click', () => {
    currentDate.setMonth(currentDate.getMonth() + 1);
    renderCalendar();
});

renderCalendar();
''',
            'status': 'completed',
            'total_lines': 50
        }
    ]


def _generate_generic_app_code(requirement: str) -> list:
    """生成通用应用代码"""
    return [
        {
            'filename': 'index.html',
            'content': f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>应用</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="style.css">
</head>
<body class="bg-gray-100 min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <h1 class="text-4xl font-bold text-center text-gray-800 mb-8">应用</h1>
        <div class="bg-white rounded-lg shadow-md p-6">
            <p class="text-gray-600">需求：{requirement[:100]}...</p>
            <p class="text-gray-500 mt-4">这是一个基础模板，请根据具体需求实现功能。</p>
        </div>
    </div>
    <script src="script.js"></script>
</body>
</html>''',
            'status': 'completed',
            'total_lines': 18
        },
        {
            'filename': 'style.css',
            'content': '''/* 通用应用样式 */
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
''',
            'status': 'completed',
            'total_lines': 4
        },
        {
            'filename': 'script.js',
            'content': '''// 通用应用逻辑
console.log('应用已加载');
// 请根据具体需求实现功能
''',
            'status': 'completed',
            'total_lines': 4
        }
    ]
