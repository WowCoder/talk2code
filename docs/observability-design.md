# 可观测性设计说明

Talk2Code 内置一套轻量级可观测性设施，目标是**每一次需求执行都可归因**：从一条日志定位到需求，从需求回查完整链路，从链路读出耗时与成本。

## 设计原则

1. **按需求聚合，而非按需求分流**。运行期保持日志单流追加，每条日志携带 `req_id` / `trace_id`；需求维度的聚合交给查询与归档完成。全局性共因（如模型端点不可达、数据库连接耗尽）需要跨需求视图才能识别，分文件反而会破坏它。
2. **自研轻量实现**。不引入 Prometheus / OpenTelemetry 等重型依赖；数据出口保持标准形态（结构化日志、JSON、可 SQL 查询的表），将来需要时可平滑接入第三方平台。
3. **未知不能当零**。没有上报的 usage、缓存或价格必须保留未知状态，同时给出数据覆盖率。

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
- `cache_hit_rate = 命中输入 token / 上报缓存信息的输入 token`；同时显示这些输入占全部已知输入的覆盖率。没有上报显示未知，实际零命中显示 0%。
- 每次真实 LLM HTTP 请求（含重试、错误、备用模型）生成唯一 `attempt_id`，由客户端统一写 `llm_turn`。业务埋点补阶段与轮次，不重复累计用量。
- 请求开始前冻结阶段与用途，意图识别、澄清归规划，任务反思归交付；旧数据缺失阶段时显示“未归类”。独立调用在后台单列，离线评测写隔离轨迹库，两者均不混入用户需求费用。
- 完成的请求先进入 `backend/logs/llm-accounting-pending/<目标库哈希>/` 持久队列，再落数据库；默认启动时及每30秒按 `attempt_id` 幂等补记。保留原时间、用量与价格快照，成功后删除，不重发请求。目录0700、文件0600，按目标库隔离；失败计数与待补记数量供后台对账。`LLM_ACCOUNTING_REPLAY_INTERVAL_SECONDS=0` 可关闭后台轮询。
- 普通输入、缓存读取、缓存写入和输出按协议归一化；金额以 Decimal 计算，事件保留价格快照与精确金额字符串。展示统一为估算成本 USD，不代表供应商实际扣费。
- 详情同时提供 `usage_summary` 需求累计和 trace 本次执行；运营列表、统计与需求累计复用事件汇总。

### 历史修复

更新后端并完成新增列迁移后，可先预览单需求，再显式应用：

```bash
cd backend
PYTHONPATH=. python scripts/rebuild_usage.py --requirement-id 123
PYTHONPATH=. python scripts/rebuild_usage.py --requirement-id 123 --apply
```

默认预览不写数据。应用只修复有原始 usage 的历史事件和 trace 汇总，新客户端事件保留当时价格快照。历史价格无法确认时标记历史估算；原本未记录的请求不能重建。重复应用保持结果一致。

具体字段和验收约定见 [用量计量设计](design/usage-accounting.md)。

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
