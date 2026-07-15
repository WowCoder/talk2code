## ADDED Requirements

### Requirement: ToolHandler 基类
系统 SHALL 提供 `ToolHandler` 抽象基类，定义 `execute(self, args: dict, workspace, state: AgentState) -> ToolResult` 方法签名。所有工具处理器 MUST 继承此基类并实现 `execute` 方法。

#### Scenario: 自定义工具继承 ToolHandler
- **WHEN** 开发者创建 `class MyTool(ToolHandler)` 并实现 `execute` 方法
- **THEN** 系统接受该处理器作为合法工具处理器进行注册

### Requirement: 装饰器注册
系统 SHALL 提供 `@register_tool("tool_name")` 装饰器，将 ToolHandler 子类自动注册到全局 `ToolRegistry`。装饰器 MUST 在模块导入时执行注册。

#### Scenario: 装饰器自动注册工具
- **WHEN** Python 解释器导入包含 `@register_tool("my_tool")` 装饰的 ToolHandler 子类的模块
- **THEN** 该工具自动出现在 `ToolRegistry.list_tools()` 中，无需手动调用 `registry.register()`

### Requirement: 基于注册的工具分派
`ToolCallLoop._execute_tool()` SHALL 通过 `ToolRegistry` 获取工具处理器并调用其 `execute` 方法，而不是通过硬编码 `handler_map` 字典。对于内置工具（read_file / write_file / edit_file 等），MUST 迁移为 ToolHandler 子类实现。

#### Scenario: 新增工具无需修改 ToolCallLoop
- **WHEN** 开发者在 `tools/` 目录下新增一个 ToolHandler 子类并用 `@register_tool` 装饰
- **THEN** ToolCallLoop 自动发现并支持该工具，无需修改 `runtime.py`

#### Scenario: 内置工具仍正常工作
- **WHEN** Agent 调用 `read_file` 工具
- **THEN** ToolCallLoop 通过注册表分派到 `ReadFileHandler.execute()`，行为与重构前一致

### Requirement: 向后兼容 ToolDefinition
现有 `ToolDefinition` dataclass 和 `ToolRegistry` SHALL 保持兼容。`@register_tool` 装饰器内部 MUST 创建 `ToolDefinition` 并调用 `registry.register()`。旧的 `create_tool_registry()` 工厂函数 MUST 继续返回有效的 ToolRegistry。

#### Scenario: 旧代码兼容
- **WHEN** 现有代码调用 `registry.execute("read_file", args)`
- **THEN** 返回有效的 `ToolResult`，行为与重构前一致
