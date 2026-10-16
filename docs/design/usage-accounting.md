# LLM 用量与缓存计量

用户已于 2026-10-16 确认实施第一批优化。目标是完整记录每次实际请求的已知消耗，并让需求详情、运营后台和导出在相同范围内一致。

## 范围与不变量

- 在现有工作区集成已有未提交统计修复，保留其内容；不发布生产环境。
- 复用 AgentEvent 的 llm_turn 表示一次实际 HTTP 尝试，call_id 使用唯一 attempt ID；业务 record_llm_turn 对客户端已采集事件只补阶段/轮次等语义，不重复记账。
- 普通请求、工具请求、reasoning 救援、HTTP 错误、网络错误、备用模型均经过同一采集入口；失败时已有 usage 仍统计，无 usage 保持未知。
- requirement_id、trace_id、turn_index、stage、purpose、origin 在每次 HTTP 发出前冻结。阶段和用途覆盖辅助调用及失败尝试；意图识别与澄清属于规划，结束反思属于交付。
- origin 区分 requirement / independent / evaluation；独立调用使用 requirement_id=0 保留全部记录，后台单独查询，不混入需求费用。离线评测始终写独立轨迹库，预检也保留记录。
- 历史缺少origin的llm_turn中，requirement_id=0同样归独立调用；有正需求ID的旧记录（包括需求已删除的孤儿轨迹）保持原需求范围，不凭缺失业务行改写历史归属。
- 规划暂停时保存阶段报告，确认后新执行；需求累计由全部需求事件计算。旧记录缺少阶段时显示未归类桶，不从阶段合计中消失。
- 每次完成的请求先写本地持久补记文件，再尝试数据库落账；仅确认成功后删除文件。重启后及每30秒重放当前目标库的待补记记录，保留原始时间、用量、价格快照和归属，不重发LLM请求、不重算价格；attempt_id唯一约束保证幂等。文件目录0700、内容0600，不存API密钥或数据库连接串，未补回记录不自动过期。
- 流式结束缺少终止标记记interrupted，取消记cancelled；关闭连接或诊断日志异常不跳过记账、不覆盖原始传输异常。补记文件失败仍尝试数据库，并累计失败告警；磁盘与数据库同时不可用仍可能丢失，后台同时显示待补记量与进程累计写入失败量。
- 原始 usage 与统一输入/输出/缓存读取/缓存写入并存。Anthropic 总输入是普通输入、缓存读取、缓存创建之和；OpenAI 总输入已包含缓存部分。
- 缓存率只统计上报样本；同时展示输入 token 覆盖率、上报调用数和 usage 覆盖率。没有上报显示未知。
- 价格先剥离命名空间再精确匹配；已知版本别名有明确映射，族价标记估算，未知价标记兜底。各类 token 价格独立，Decimal 运算，价格快照保留版本与 USD 币种。
- 新事件保存计价来源和精确金额字符串；现有金额接口保持数值兼容。所有显示/CSV标明估算成本 USD。
- 历史重建提供只读预览和显式应用；缺失 usage、历史价格不可推断，保留未知/历史估算标记。

## 汇总接口

保留 tokens/tokens_in/tokens_out/cost/llm_calls/cached_tokens/cache_hit_rate/cache_reported_tokens_in/cache_reported_calls。

新增 cache_coverage（已上报缓存的输入/已知输入）、usage_reported_calls、usage_coverage、cost_fallback_calls、cost_currency=USD、cost_is_estimate=true、cache_savings（已知价格下含缓存写入成本的净节省）。需求详情返回 usage_summary（需求累计）；trace 保留本次执行用量。

## 验收

真实客户端 mock 证明多次请求分别记账、失败usage不丢、备用模型归属正确、业务埋点不双计；协议和别名/价格回归；暂停恢复后需求累计与事件和一致；缓存无数据/零命中/部分覆盖区分；历史预览不写入、应用幂等。运行 backend pytest 和 frontend npm run build。
