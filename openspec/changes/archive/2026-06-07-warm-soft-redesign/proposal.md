# 前端页面 Warm Soft 风格重构

## Why

当前 3 个前端页面（login/index/detail）使用杂乱的紫色渐变 + Tailwind 内联样式方案，视觉风格过时且缺乏设计一致性。设计原型稿提供了完整的 "Warm Soft" 设计系统——基于 OKLch 色彩空间、暖色调、卡片式布局的统一视觉语言。需要用新设计系统重构所有页面，同时新增 "历史对话" 和 "设置" 两个功能页面。

## What Changes

### 1. 全局设计系统（CSS Variables）
- 从 Tailwind CDN 内联样式迁移到自定义 CSS Custom Properties
- 颜色系统：OKLch 暖色调（beige 背景、terra cotta 主色）
- 字体系统：衬线标题字体 + 系统无衬线正文字体
- 组件规范：圆角 12-20px、卡片阴影、渐变装饰

### 2. login.html 重构
- 紫色渐变背景 → 暖米色居中布局
- 毛玻璃卡片 → 带径向渐变的白色卡片
- 统一 OKLch 色板、新按钮 hover 效果
- 保持登录/注册双 Tab 切换 + 测试账号提示

### 3. index.html 重构  
- 深色顶栏 → 毛玻璃粘性导航
- 新增 Hero 区域（衬线标题 + 副标题）
- 需求输入区改为圆角卡片 + 径向渐变装饰
- 示例 Chips 样式更新
- 导航增加"历史对话""设置"入口

### 4. detail.html 重构
- 网格分栏 → Flex 分栏（40%/60%）
- 紫色顶栏 → 紧凑返回导航 + 状态指示器
- CodeMirror CDN → 自定义代码编辑器（文件树 + 语法高亮）
- 新增设备预览切换（桌面/平板/手机）
- 保持 SSE 实时推送、对话面板、代码/预览 Tab 切换逻辑

### 5. 新增 history.html
- 历史项目列表页（搜索框 + 状态筛选）
- 状态徽章（已完成/生成中/排队中/失败）
- 空状态引导
- 点击跳转 detail.html

### 6. 新增 settings.html
- 侧边栏布局：个人资料 / 外观 / 账户安全 / 关于
- Toggle 开关组件（深色模式、自动保存）
- 密码修改表单
- 账户注销入口
- 版本信息

## Capabilities

### New Capabilities
- `warm-soft-design-system`: 全局设计系统（CSS 变量、OKLch 色板、字体、组件规范）
- `history-page`: 历史对话列表页，支持搜索和状态展示
- `settings-page`: 设置页，支持个人资料、外观、账户安全管理

### Modified Capabilities
<!-- None — first spec-based change -->

## Impact

**修改文件：**
- `frontend/login.html` — 完全重写样式和结构
- `frontend/index.html` — 完全重写样式和结构（新增导航、Hero）
- `frontend/detail.html` — 完全重写样式和结构（自建编辑器替代 CodeMirror）

**新增文件：**
- `frontend/history.html` — 历史对话页
- `frontend/settings.html` — 设置页
- `frontend/css/design-system.css` — 共享设计系统 CSS（可选抽取）

**不影响：**
- 后端 API 路由、SSE、认证逻辑不变
- 前端 JS 业务逻辑保留（SSE 连接、对话渲染、代码生成）
- `js/security.js` 保留，XSS 防护逻辑不变
