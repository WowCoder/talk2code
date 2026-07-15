# tool-output-reform Specification

## Purpose
TBD - created by archiving change agent-harness-hard-constraints. Update Purpose after archive.
## Requirements
### Requirement: lint_js ES Module 检测
系统 SHALL 在 `lint_js` 工具中自动检测文件是否使用 ES Module 语法，并使用正确的 Node.js 参数进行语法检查。

#### Scenario: 检测到 ES Module 语法
- **WHEN** 被检查的 JS 文件包含 `export` 或 `import` 语句
- **THEN** 使用 `node --check --input-type=module -` 命令检查语法

#### Scenario: CommonJS 或普通脚本
- **WHEN** 被检查的 JS 文件不包含 ES Module 语法
- **THEN** 使用 `node --check -` 命令检查语法

#### Scenario: ES Module 语法正确
- **WHEN** ES Module 文件语法正确
- **THEN** 返回"JavaScript 语法检查通过"

#### Scenario: 真实语法错误
- **WHEN** JS 文件存在真实的语法错误（如括号不匹配）
- **THEN** 返回包含具体错误行号和消息的结果

### Requirement: write_file 仅返回元数据
系统 SHALL 在 `write_file` 工具成功写入后仅返回元数据，不返回文件完整内容。

#### Scenario: 写入成功
- **WHEN** Agent 调用 `write_file` 成功写入文件
- **THEN** 返回结果包含：文件名、行数、字符数。格式：`"已创建 {filename} ({lines} 行, {chars} 字符)"`

#### Scenario: 返回结果不包含文件内容
- **WHEN** Agent 调用 `write_file` 写入 500 行的文件
- **THEN** 返回结果中不包含文件的任何代码内容，Agent 不需要也无法"验证"写入结果

### Requirement: write_file 文件摘要更新
系统 SHALL 在 `write_file` 成功后自动更新文件摘要缓存，使下一轮 system prompt 中的文件摘要反映最新状态。

#### Scenario: 摘要缓存更新
- **WHEN** Agent 成功写入 `js/app.js`
- **THEN** 下一轮 `_build_system_prompt` 调用时，文件摘要中包含 `js/app.js` 的最新结构信息（函数列表、DOM 引用等）

### Requirement: edit_file 默认编辑定位
系统 SHALL 在所有编码 prompt 模板中明确：`edit_file` 用于修改已有文件，`write_file` 仅用于创建新文件。

#### Scenario: file_aware_coder prompt 中包含 edit_file 说明
- **WHEN** 加载 `coding/file_aware_coder.md` prompt 模板
- **THEN** 模板中包含"修改已有文件请使用 edit_file 做局部修改，不要用 write_file 重写整个文件"

#### Scenario: coder_ml prompt 中包含 edit_file 说明
- **WHEN** 加载 `coding/coder_ml.md` prompt 模板
- **THEN** 模板中包含 edit_file 的使用说明

### Requirement: 移除 lint 重复调用提示
系统 SHALL 在系统 prompt 中明确：`lint_js`/`lint_css` 只需在全部文件完成后调用一次，不需要每写入一个文件就检查。

#### Scenario: coder prompt 包含 lint 使用规范
- **WHEN** 加载编码相关的 prompt 模板
- **THEN** 模板中包含 lint 工具的使用时机说明：全部文件创建完成后统一验证

