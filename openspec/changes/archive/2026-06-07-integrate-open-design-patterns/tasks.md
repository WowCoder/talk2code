## 1. Craft 设计质量规则层

- [x] 1.1 创建 `craft/` 目录，从 Open Design 项目移植并翻译 4 个规则文件：`anti-ai-slop.md`、`accessibility-baseline.md`、`typography.md`、`color.md`
- [x] 1.2 创建 `craft_loader.py` 工具模块，实现 `load_craft_rules(names: List[str]) -> str` 函数，从 `craft/*.md` 加载规则内容并拼接
- [x] 1.3 在 `config.py` 中增加 `LLM_CRAFT_ENABLED` 开关（默认 `True`）
- [x] 1.4 在 `ENGINEER_PROMPT` 的 System Prompt 末尾注入 Craft 规则，通过 `LLM_CRAFT_ENABLED` 开关控制
- [x] 1.5 测试验证：生成同样需求（"开发一个待办清单"）对比注入 Craft 前后的代码质量差异

## 2. Skill 可插拔模板系统

- [x] 2.1 创建 `skills/` 目录结构，包含 `todo/`、`calculator/`、`note/`、`calendar/`、`generic/` 五个子目录
- [x] 2.2 为每个 Skill 编写 `SKILL.md`，包含 YAML 元数据（name/description/triggers/craft_requires）和代码模板
- [x] 2.3 从 `prompts.py` 中提取各应用类型的 Fallback JSON 模板到对应 Skill 目录
- [x] 2.4 创建 `skill_loader.py` 工具模块，实现 `load_all_skills()` 和 `match_skill(requirement: str) -> Skill` 函数
- [x] 2.5 在 `agents/nodes.py` 的 `engineer_node` 中增加 Skill 匹配逻辑，Planner 输出中包含 `matched_skill` 字段
- [x] 2.6 修改 Fallback 逻辑，从 Skill 文件加载模板替代硬编码函数
- [x] 2.7 保留旧 Fallback 函数作为最终兜底（Skill 的 template.json 不存在时使用）

## 3. 交互式需求澄清

- [x] 3.1 在 `agents/nodes.py` 的 `planner_node` 开头增加需求评估逻辑（长度检查 + 关键词检测）
- [x] 3.2 增加 `_generate_clarify_questions()` 函数，用 LLM 生成结构化的澄清问题表单 JSON
- [x] 3.3 在 `services/requirement_service.py` 中增加 `question-form` 事件类型，通过 SSE 推送到前端
- [x] 3.4 在 `frontend/detail.html` 中增加问题表单渲染组件，支持 radio/text 两种问题类型
- [x] 3.5 增加 `POST /api/requirements/<id>/clarify` 端点，接收用户澄清答案并重新执行工作流
- [x] 3.6 限制澄清轮数为 1 次，通过 `[用户补充说明]` 标记检测

## 4. Artifact 协议升级

- [x] 4.1 更新 `ENGINEER_PROMPT` 的 System Prompt，要求 LLM 输出新 Artifact 格式（含 metadata）
- [x] 4.2 更新 `extract_json_from_response()` 解析函数，兼容新旧两种 JSON 格式
- [x] 4.3 更新前端 `appendCode()` 和数据模型，支持 Artifact metadata 字段
- [x] 4.4 回归测试：确保旧格式 `[{filename, content}]` 仍然正常工作

## 5. 预览沙箱增强

- [x] 5.1 在 iframe 预览中注入 Content Security Policy meta 标签
- [x] 5.2 增加 `sandbox` 属性细化：`allow-scripts allow-same-origin`（移除 allow-forms）
- [x] 5.3 增强 postMessage 通道验证，限制预览与父窗口的通信接口
