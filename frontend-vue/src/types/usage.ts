/** 汇总接口的共同用量字段；可选字段兼容历史响应，缺失不冒充零。 */
export interface UsageAccounting {
  tokens?: number
  tokens_in?: number
  tokens_out?: number
  cost?: number | null
  llm_calls?: number
  cached_tokens?: number
  cache_hit_rate?: number | null
  cache_reported_tokens_in?: number
  cache_reported_calls?: number
  cache_coverage?: number | null
  usage_reported_calls?: number
  usage_coverage?: number | null
  cost_fallback_calls?: number
  cost_currency?: 'USD'
  cost_is_estimate?: true
  /** 已知样本中包含缓存写入成本的净节省，可能为负；无已知样本时为 null。 */
  cache_savings?: number | null
  cache_savings_reported_calls?: number
}

export interface RequirementUsageSummary extends UsageAccounting {
  tokens: number
  tokens_in: number
  tokens_out: number
  cost: number | null
  llm_calls: number
}
