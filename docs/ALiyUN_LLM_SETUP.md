# 阿里云百炼大模型配置说明

## 获取 API Key

1. 访问 [阿里云百炼控制台](https://bailian.console.aliyun.com/)
2. 登录阿里云账号
3. 进入 **API-KEY 管理** 页面
4. 创建新的 API Key

## 配置方式

### 方式 1：环境变量（推荐）

```bash
export DASHSCOPE_API_KEY="你的 API Key"
export DASHSCOPE_MODEL="qwen-plus"  # 可选：qwen-plus, qwen-turbo, qwen-max
```

### 方式 2：修改 config.py

编辑 `backend/config.py`：

```python
DASHSCOPE_API_KEY = "你的 API Key"
DASHSCOPE_MODEL = "qwen-plus"
```

## 可用模型

| 模型 | 说明 |
|------|------|
| qwen-turbo | 速度快，成本低，适合简单任务 |
| qwen-plus | 能力强，性价比高（默认） |
| qwen-max | 最强能力，适合复杂任务 |

## 启动服务

```bash
cd backend
python app.py
```

服务启动后会显示在终端：
- 如果 API Key 未配置，智能体将使用预设模板输出
- 如果 API Key 已配置，智能体将调用通义千问模型生成内容

## 验证配置

访问 http://localhost:5001/login.html

登录并提交一个需求（如"开发一个待办清单 App"），观察 AI 对话面板：
- 如果看到智能体输出带有 `[注：LLM 调用失败，使用预设模板]`，说明 API 调用失败
- 如果看到智能体输出个性化内容，说明 LLM 调用成功

## 常见问题

### 1. "请配置 DASHSCOPE_API_KEY" 错误

确保已正确配置 API Key，重启服务生效。

### 2. API 调用超时

检查网络连接，阿里云百炼需要访问外网。

### 3. 余额不足

访问百炼控制台查看账户余额，确保有足够的额度。

### 4. 模型权限

某些模型可能需要单独申请权限，在控制台查看模型状态。
