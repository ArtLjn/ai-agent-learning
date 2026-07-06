// 工单相关
export interface Ticket {
  ticket_id: string
  content: string
  user_id?: string
  category: string | null
  priority: string | null
  processing_result: string | null
  references: string[]
  review_score: number | null
  retry_count: number
  status: TicketStatus
  error: string | null
  satisfied?: boolean | number | null
  created_at: string
}

export type TicketStatus = 'received' | 'classifying' | 'processing' | 'reviewing' | 'pending_human_review' | 'waiting_user_input' | 'completed' | 'failed'
export type TicketCategory = 'technical' | 'billing' | 'complaint' | 'inquiry'
export type TicketPriority = 'P0' | 'P1' | 'P2' | 'P3'

// 用户（U-02 / U-03 / U-04）
export interface UserProfile {
  user_id: string | null
  username: string | null
  nickname: string | null
  contact: string | null
  vip_level: number
  preferred_categories: TicketCategory[]
  created_at: string | null
  status: string
}

export interface RegisterRequest {
  username: string
  password: string
  nickname?: string
}

export interface RegisterResponse {
  user: UserProfile
}

export interface UpdateMeRequest {
  nickname?: string
  contact?: string
  preferred_categories?: TicketCategory[]
}

export interface ChangePasswordRequest {
  old_password: string
  new_password: string
}

export interface ChangePasswordResponse {
  success: boolean
  redirect: string
}

export interface TicketCreateRequest {
  content: string
  user_id?: string
}

export interface TicketMessage {
  message_id: string
  ticket_id: string
  sender_type: 'user' | 'reviewer' | 'system' | 'agent' | string
  sender_id: string | null
  content: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface TicketMessageCreateRequest {
  content: string
  sender_id?: string
}

// Trace 相关
export interface Trace {
  trace_id: string
  ticket_id: string
  status: 'running' | 'completed' | 'failed'
  start_time: number
  end_time: number | null
  duration: number | null
  total_tokens: number
  total_tool_calls: number
  node_count: number
  error: string | null
  ticket_summary?: string | null
  ticket_category?: TicketCategory | null
  ticket_priority?: TicketPriority | null
  ticket_result?: string | null
  ticket_review_score?: number | null
  reference_count?: number
  references?: string[]
}

export interface Span {
  span_id: string
  trace_id: string
  parent_span_id: string | null
  span_type: 'node' | 'react_iter' | 'llm_call' | 'tool_call' | 'memory_call' | 'human_decision' | string
  name: string
  status: 'ok' | 'error' | 'fallback' | string
  input_data: Record<string, unknown> | null
  output_data: Record<string, unknown> | null
  start_time: number
  end_time: number | null
  duration: number | null
  metadata: Record<string, unknown> | null
  children: Span[]
}

export interface TraceDetail {
  trace_id: string
  ticket_id: string
  status: string
  duration: number | null
  total_tokens: number
  total_tool_calls: number
  node_count: number
  start_time: number
  end_time: number | null
  ticket_summary?: string | null
  ticket_category?: TicketCategory | null
  ticket_priority?: TicketPriority | null
  ticket_result?: string | null
  ticket_review_score?: number | null
  reference_count?: number
  references?: string[]
  spans: Span[]
}

export type DecisionType = 'routing' | 'branching' | 'quality_gate' | 'boundary' | 'tool_selection' | 'escalation'

export interface DecisionOption {
  value: string
  score: number
  reason: string
}

export interface TraceDecision {
  span_id: string
  span_name: string
  span_type: string
  decision_type: DecisionType | string
  trigger: Record<string, unknown> | null
  options_count: number
  options: DecisionOption[]
  selection_value: string
  confidence: number | null
  reason: string | null
  start_time: number
  duration: number | null
}

export interface TraceDecisionsResponse {
  trace_id: string
  decision_count: number
  decisions: TraceDecision[]
}

export interface TraceListResponse {
  traces: Trace[]
  count: number
  total: number
  limit: number
  offset: number
}

// 统计相关
export interface ResolutionStats {
  total: number
  completed: number
  failed: number
  avg_retries: number
  success_rate: number
}

export interface EfficiencyStats {
  avg_tokens_per_ticket: number
  avg_duration_seconds: number
  avg_tool_calls: number
}

export interface Analytics {
  category_distribution: Record<string, number>
  priority_distribution: Record<string, number>
  resolution_stats: ResolutionStats
  daily_stats: DailyStat[]
  efficiency: EfficiencyStats
  evaluation: {
    total: number
    completed: number
    failed: number
    avg_retries: number
    success_rate: number
    avg_tokens_per_ticket: number
    avg_duration_seconds: number
    avg_tool_calls: number
    avg_review_score: number
    satisfaction_rate: number
    total_feedback: number
  }
}

export interface DailyStat {
  date: string
  total: number
  completed: number
  failed: number
  created?: number
}

// 知识库（v2 纯对齐 rag-service /collections/{name}/documents）
export interface KnowledgeTextUploadRequest {
  title?: string
  content: string
  category?: string
}

export interface KnowledgeDocument {
  doc_id: string
  collection: string
  source: string
  category: string
  chunk_count: number
  content_hash?: string
  extra?: Record<string, unknown>
  ingested_at: string
}

export interface KnowledgeListResponse {
  total: number
  page: number
  page_size: number
  documents: KnowledgeDocument[]
}

export interface KnowledgeIngestResult {
  status: string
  doc_id: string
  chunk_count: number
  collection: string
  action: string
}

export interface KnowledgeEvaluationMetrics {
  recall_at_k?: Record<string, number>
  precision_at_k?: Record<string, number>
  ndcg_at_k?: Record<string, number>
  mrr?: number
  hit_rate?: number
  sample_count?: number
}

export interface KnowledgeEvaluationSample {
  query: string
  collection: string
  tags?: string[]
  relevant: string[]
  retrieved: string[]
  retrieved_aliases?: string[][]
  first_hit_rank: number | null
  hit: boolean
  actual_mode?: string
  warning?: string | null
}

export interface KnowledgeEvaluationReport {
  schema_version: string
  started_at: string
  finished_at: string
  dataset_path: string
  mode: string
  top_k: number
  k_values: number[]
  summary: {
    sample_count: number
    metrics: KnowledgeEvaluationMetrics
  }
  samples: KnowledgeEvaluationSample[]
  report_path?: string
}

export interface KnowledgeEvaluationLatestResponse {
  available: boolean
  report: KnowledgeEvaluationReport | null
}

export interface TicketListParams {
  status?: string
  category?: string
  limit?: string
  offset?: string
}

export interface TicketCreateResponse {
  ticket_id: string
  status: string
}

export interface TicketFeedbackResponse {
  status: string
  ticket_id: string
  satisfied: boolean
}

export interface TraceStatsResponse {
  trace_id: string
  total_duration: number
  node_count: number
  llm_calls: number
  tool_calls: number
  total_tokens: number
  slowest_node: string | null
  error_nodes: string[]
}

export type ApiRecord = Record<string, unknown>

// WebSocket
export interface WSMessage {
  ticket_id: string
  status: string
  message: string
  timestamp: string
  node?: string
  data?: Record<string, unknown>
  // 兼容 review_requested / review_decided 事件
  type?: string
  trigger_type?: string
  trigger_reason?: string | null
  priority?: string | null
  review_id?: string
  decision?: string
  reviewer_id?: string
  next_node?: string
}

// 人工审核相关
export type TriggerType = 'escalate' | 'review_failed' | 'error_fallback' | 'user_request'
export type ReviewDecision = 'approve' | 'reject' | 'rewrite' | 'reprocess' | 'request_info'
export type ReviewStatus = 'pending' | 'decided'

export interface AISuggestion {
  recommended_decision: ReviewDecision
  confidence: number  // 0-1
  reasoning: string
  key_concerns: string[]
}

export interface HumanReview {
  review_id: string
  ticket_id: string
  trigger_type: TriggerType | string
  trigger_reason: string | null
  ai_suggestion: AISuggestion | null
  decision: ReviewDecision | string | null
  decision_reason: string | null
  rewritten_result: string | null
  reviewer_id: string | null
  status: ReviewStatus
  created_at: string
  decided_at: string | null
}

export interface ReviewQueueItem {
  review_id: string
  ticket_id: string
  trigger_type: TriggerType | string
  trigger_reason: string | null
  content_preview: string
  category: string | null
  priority: string | null
  ai_suggestion: AISuggestion | null
  waiting_seconds: number
  created_at: string
}

export interface ReviewDetail {
  ticket_id: string
  content: string
  category: string | null
  priority: string | null
  status: string
  processing_result: string | null
  review_score: number | null
  retry_count: number
  current_review: HumanReview | null
  history_reviews: HumanReview[]
  trace_summary: {
    trace_id: string
    node_count: number
    duration: number
  } | null
}

export interface ReviewQueueResponse {
  queue: ReviewQueueItem[]
  total: number
  limit: number
  offset: number
}

export interface ReviewStats {
  pending_count: number
  decided_today: number
  decision_distribution: {
    approve: number
    rewrite: number
    reprocess: number
    reject: number
  }
  avg_decision_seconds: number
  ai_adoption_rate: number  // 0-1
}

export interface ReviewDecisionRequest {
  decision: ReviewDecision
  decision_reason: string
  rewritten_result?: string
  reviewer_id: string
}

export interface ReviewDecisionResponse {
  status: 'ok'
  ticket_id: string
  next_node: 'notify' | 'process' | 'complete'
  workflow_resumed: boolean
}

export interface ReviewRequestedEvent {
  type: 'review_requested'
  ticket_id: string
  timestamp: string
  trigger_type: string
  priority: string | null
  trigger_reason: string | null
  review_id: string
}

export interface ReviewDecidedEvent {
  type: 'review_decided'
  ticket_id: string
  timestamp: string
  decision: string
  reviewer_id: string
  next_node: string
}

// 系统设置
export interface SystemSettings {
  llm_base_url: string
  llm_api_key_configured: boolean
  llm_api_key: string
  llm_model: string
  embedding_base_url: string
  embedding_model: string
  embedding_dim: number
  model_routes: Record<string, string>
  fallback_model: string
  max_retries: number
  review_threshold: number
  max_react_iterations: number
  max_messages: number
  max_concurrency: number
  qdrant_url: string
  qdrant_collection: string
  knowledge_available: boolean
  cache_enabled: boolean
  cache_max_size: number
  cache_ttl: number
  checkpoint_ttl: number
}

// A-06 系统配置（只读脱敏，6 类）
export interface SystemConfigLLM {
  base_url: string
  model: string
  temperature: number
  api_key_configured: boolean
  fallback_model: string
  model_routes: Record<string, string>
}

export interface SystemConfigEmbedding {
  base_url: string
  model: string
  dim: number
  api_key_configured: boolean
}

export interface SystemConfigQdrant {
  url: string
  collection: string
  top_k: number
  score_threshold: number
  batch_size: number
  api_key_configured: boolean
}

export interface SystemConfigRagService {
  status: string
  base_url: string | null
  api_key_configured: boolean
}

export interface SystemConfigDatabase {
  driver: string | null
  host: string | null
  port: number | null
  database: string | null
  username_configured: boolean
  password_configured: boolean
  parse_error?: boolean
}

export interface SystemConfigAuth {
  auth_enabled: boolean
  session_cookie: string
  session_max_age_days: number
  password_hash_configured: boolean
  session_secret_configured: boolean
}

export interface SystemConfig {
  llm: SystemConfigLLM
  embedding: SystemConfigEmbedding
  qdrant: SystemConfigQdrant
  rag_service: SystemConfigRagService
  database: SystemConfigDatabase
  auth: SystemConfigAuth
  _meta: {
    readonly: boolean
    version: string
    note: string
  }
}

// A-07 操作日志审计
export interface AuditLogEntry {
  id: number
  admin_id: string | null
  admin_username: string | null
  action: string
  action_label: string
  target_type: string | null
  target_id: string | null
  detail: Record<string, unknown> | null
  ip: string | null
  created_at: string | null
}

export interface AuditLogListResponse {
  items: AuditLogEntry[]
  total: number
  page: number
  page_size: number
  actions: Record<string, string>
}

// D-02 Prompt 版本管理
export type PromptAgentName = 'intent' | 'classify' | 'process' | 'review' | 'coordinator'

export interface PromptVersion {
  prompt_id: number
  agent_name: PromptAgentName
  version: number
  template: string
  is_active: boolean
  note: string | null
  created_at: string | null
  activated_at: string | null
}

export interface PromptVersionListResponse {
  items: PromptVersion[]
  total: number
  page: number
  page_size: number
}

// ============================================================
// D-01 / D-04 开发人员工作台（admin trace + token stats）
// ============================================================

// D-01 admin trace：完整 trace 树（与 TraceDetail 字段对齐 + decisions 抽取）
export interface AdminTraceDetail extends TraceDetail {
  decisions: AdminSpanDecision[]
  decision_count: number
}

export interface AdminSpanDecision {
  span_id: string
  span_name: string | null
  span_type: string | null
  decision_type: string | null
  trigger: Record<string, unknown> | null
  options_count: number
  options: Array<{ value: string; score: number; reason?: string }>
  selection_value: string | null
  confidence: number | null
  reason: string | null
  execution: Record<string, unknown> | null
  start_time: number | null
  duration: number | null
}

export interface AdminTraceListResponse {
  items: Trace[]
  total: number
  page: number
  page_size: number
}

export interface PromptDiffResponse {
  agent_name: PromptAgentName
  from_version: number
  to_version: number
  diff: string
  has_diff: boolean
}

// D-05 Agent 调用统计
export interface AgentStatEntry {
  agent_name: PromptAgentName
  span_name: string | null
  call_type: string
  call_count: number
  avg_duration_ms: number
  max_duration_ms: number
  success_rate: number
  error_count: number
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  request_count: number
}

export interface AgentStatsResponse {
  days: number
  since: string
  agents: AgentStatEntry[]
}

// D-04 token stats
export interface TokenSummaryBucket {
  model: string
  call_type: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
}

export interface TokenSummaryResponse {
  days: number
  total_tokens: number
  total_requests: number
  by_model: Record<string, TokenSummaryBucket>
}

export interface TokenDailyItem {
  model: string
  call_type: string
  ticket_id: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
  estimated_cost_cny: number
}

export interface TokenDailyResponse {
  date: string
  items: TokenDailyItem[]
}

export interface TokenHourlyItem {
  date: string
  hour: string | null
  model: string
  call_type: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
}

export interface TokenHourlyResponse {
  date: string
  items: TokenHourlyItem[]
  hours: string[]
  total_tokens: number
  total_requests: number
}
