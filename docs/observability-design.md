# 可观测性设计说明

Talk2Code 内置一套轻量级可观测性设施，目标是**每一次需求执行都可归因**：从一条日志定位到需求，从需求回查完整链路，从链路读出耗时与成本。

## 设计原则

1. **按需求聚合，而非按需求分流**。运行期保持日志单流追加，每条日志携带 `req_id` / `trace_id`；需求维度的聚合交给查询与归档完成。全局性共因（如模型端点不可达、数据库连接耗尽）需要跨需求视图才能识别，分文件反而会破坏它。
2. **自研轻量实现**。不引入 Prometheus / OpenTelemetry 等重型依赖；数据出口保持标准形态（结构化日志、JSON、可 SQL 查询的表），将来需要时可平滑接入第三方平台。
3. **宁可低估，不可高估**。统计口径取不到时归零，绝不产出看似漂亮的假数字。

## 组成

五条通道 + 两类端点：

| 通道 | 内容 |
|---|---|
| 应用日志 | `app` / `agent` / `llm` 三个文件，按模块互斥分流，按天轮转 |
| 链路追踪 | `agent_traces` 表，span 级耗时，trace 级 token 与成本汇总 |
| 成本统计 | 按 trace 累计 token 与费用，含 KV-cache 命中量 |
| 指标端点 | `/api/metrics`（Prometheus 文本格式）、`/api/health` 系列健康检查 |
| 执行明细 | 开关控制的 JSONL，记录每轮 LLM 请求/响应与工具调用（开发排查用，默认关闭） |

## 日志

- **位置**：`backend/logs/`，全部日志收敛于同一目录，不随启动目录漂移
- **分流**：`harness.*` → `agent.log`；`llm.*` → `llm.log`；其余 → `app.log`。互斥路由，一条日志只进一个文件
- **字段**：每条日志携带 `req=<需求ID> trace=<追踪ID>`，由 `LogRecordFactory` 在记录创建时注入。上下文存于 `contextvar`；线程池线程在任务入口显式绑定、任务结束清理（`contextvar` 不被线程池自动继承，且线程会复用）
- **保留**：`agent` / `llm` 30 天，`app` 90 天（读自配置）
- **LLM 流量**：`llm_traffic.log` 为独立通道，JSON 行格式，request / response 按 `call_id` 配对，保留 7 天

### 查看方式

```bash
# 按需求查看全部日志
grep 'req=76' backend/logs/*.log

# 从日志中的 trace=xxx 回查链路明细
#   SELECT data FROM agent_traces WHERE trace_id = 'xxx';

# 指标报表：KV-cache 命中率 + 节点耗时分解
cd backend && PYTHONPATH=. python -m harness.observability.metrics_report
```

## 链路追踪

- 每次（重）执行生成唯一 `trace_id`，整条 trace（spans、tokens、cost）落 `agent_traces` 表（JSON data + 冗余统计列），跨重启可查
- span 覆盖编码迭代轮次、verify 评估与缺陷修复；trace 级汇总 `total_tokens` / `cached_tokens` / `cache_hit_rate` / `total_cost` / `duration_ms`
- `cache_hit_rate = 命中输入 token / 总输入 token`，用于度量提示词缓存优化的实际效果

## 指标体系

北极星指标：**首次通过率**——无需人工干预、无需进入修复循环，直接产出可运行应用的比例。其余指标都是对它的解释。

| 层 | 代表指标 |
|---|---|
| 质量 | 首次通过率、修复成功率、验收条件失败分布 |
| Agent 编排 | 节点耗时分解、修复触发率、迭代轮次分布 |
| 工具 | 工具失败分布、重复回读次数、守卫拦截次数 |
| LLM | KV-cache 命中率、token 成本、重试与熔断次数 |
| 系统 | 线程池饱和度、SSE 连接数、浏览器验证耗时 |

线上指标维度与离线评测集对齐，使「线上发现 → 评测复现 → 修复 → 回归验证」形成闭环。

## 演进方向

1. 第二批：评估结果落库，上线质量层指标（首次通过率、验收条件失败分布）
2. 补全 span 覆盖，形成完整的节点级调用树
3. 前端单需求执行时间线视图（读 `agent_traces`）
