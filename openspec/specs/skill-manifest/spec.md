# skill-manifest Specification

## Purpose
TBD - created by archiving change adopt-openhands-extensibility. Update Purpose after archive.
## Requirements
### Requirement: Skill Manifest 定义
每个 Skill 目录下 SHALL 包含 `manifest.json` 文件，定义以下字段：
- `name` (string, 必填): Skill 唯一标识
- `trigger` (string, 必填): 用于匹配用户需求的关键词正则表达式
- `type` (string, 必填): 取值为 `task` / `knowledge` / `repository`
- `priority` (integer, 可选, 默认 0): 匹配优先级（多个 Skill 同时匹配时，高分优先）
- `description` (string, 可选): 简短描述

#### Scenario: 合法 manifest 被加载
- **WHEN** Skill 目录包含有效 `manifest.json`，且所有必填字段均有合法值
- **THEN** `SkillLoader` 解析并缓存该 Skill 的元数据

#### Scenario: 缺失 manifest 的 Skill 被跳过
- **WHEN** Skill 目录缺少 `manifest.json`
- **THEN** `SkillLoader` 跳过该 Skill 并记录 WARNING 日志，不阻塞其他 Skill 加载

### Requirement: 关键词匹配触发
`SkillLoader` SHALL 在 Agent 编码阶段，将用户需求文本与所有已加载 Skill 的 `trigger` 正则进行匹配。匹配成功的 Skill，其 `SKILL.md` 内容 MUST 被注入到编码 Prompt 的 craft rules 部分。

#### Scenario: 需求匹配多个 Skill
- **WHEN** 用户需求 "做一个坦克大战游戏，要有排行榜" 同时匹配 `game` (trigger: "游戏|game") 和 `leaderboard` (trigger: "排行|leaderboard")
- **THEN** 两个 Skill 的 SKILL.md 内容均被注入，按 priority 降序排列

#### Scenario: 无匹配 Skill
- **WHEN** 用户需求不匹配任何已注册 Skill 的 trigger
- **THEN** 编码 Prompt 不注入 Skill 内容，行为与重构前一致

### Requirement: 扫描结果缓存
`SkillLoader` SHALL 在首次请求时扫描 `prompts/skills/` 目录并缓存索引。后续请求 MUST 复用缓存。当 `manifest.json` 文件修改时间发生变化时，缓存 MUST 自动失效并重建。

#### Scenario: 热加载 Skill
- **WHEN** 开发者在运行时新增或修改 Skill 的 `manifest.json`
- **THEN** 下一次请求自动加载新配置，无需重启服务

### Requirement: 向后兼容 _get_craft_context
现有的 `ToolCallLoop._get_craft_context()` 方法 SHALL 内部委托给 `SkillLoader`，保持方法签名和返回值格式不变。

#### Scenario: 旧调用路径兼容
- **WHEN** 现有代码调用 `self._get_craft_context(requirement)`
- **THEN** 返回的 `(rules_text, skill_instructions)` 元组格式与重构前一致

