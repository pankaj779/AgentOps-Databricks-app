export type DatabricksHealth = {
  configured: boolean
  host: string | null
  warehouse_id: string | null
  workspace_id: string | null
  sql_reachable: boolean | null
  server_time_utc: string | null
  catalogs_sample: string[] | null
  last_error: string | null
}

export type HealthResponse = {
  status: string
  service: string
  databricks: DatabricksHealth
}

export type OverviewResponse = {
  generated_at: string
  environment: string
  data_mode: string
  agents_monitored: number
  requests_24h: number
  requests_24h_source: string
  error_rate_pct: number
  est_monthly_cost_usd: number | null
  quality_score_avg: number | null
  databricks_sql_reachable: boolean | null
  inference_setup_hint: string | null
  count_7d: number | null
  gateway_tokens_24h: number | null
  gateway_tokens_7d: number | null
  gateway_usage_error: string | null
  window_hours?: number
  count_window?: number | null
  gateway_tokens_window?: number | null
}

export type AgentSummary = {
  id: string
  name: string
  status: string
  rpm: number
  p95_latency_ms: number
  error_rate_pct: number
  source?: string
  agent_key?: string | null
}

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(text || `Request failed: ${res.status}`)
  }
  return res.json() as Promise<T>
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch('/api/health')
  return parseJson<HealthResponse>(res)
}

export async function fetchOverview(hours = 168): Promise<OverviewResponse> {
  const res = await fetch(`/api/v1/overview?hours=${encodeURIComponent(String(hours))}`)
  return parseJson<OverviewResponse>(res)
}

export async function fetchAgents(): Promise<AgentSummary[]> {
  const res = await fetch('/api/v1/agents')
  return parseJson<AgentSummary[]>(res)
}

export async function fetchInferenceDiagnostics(): Promise<Record<string, unknown>> {
  const res = await fetch('/api/v1/inference/diagnostics')
  return parseJson<Record<string, unknown>>(res)
}

// --- Analytics ---

export type TimeseriesBucket = {
  bucket: string
  requests: number
  errors: number
}

export type HealthTimeseriesResponse = {
  hours: number
  buckets: TimeseriesBucket[]
  error: string | null
}

export type SloRollup = {
  id: string
  name: string
  requests_24h: number
  rpm: number
  p95_latency_ms: number
  error_rate_pct: number
  group_column?: string
}

export type HealthSloResponse = {
  p95_target_ms: number
  error_budget_pct: number
  global_p95_ms: number | null
  global_error_rate_pct: number | null
  agents_breaching_p95: number
  agents_over_error_budget: number
  rollups: SloRollup[]
  error: string | null
  source?: string | null
  note?: string | null
}

export type CostByDest = {
  destination: string
  requests: number
  est_tokens: number
}

export type CostHourly = {
  bucket: string
  est_tokens: number
}

export type AiGatewayCostByModel = {
  model: string
  requests: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export type AiGatewayCostSummary = {
  hours: number
  total_requests: number | null
  total_input_tokens: number | null
  total_output_tokens: number | null
  total_tokens: number | null
  by_model: AiGatewayCostByModel[]
  error: string | null
  note: string
  gateway_model_filter?: string | null
  gateway_models_filter?: string[] | null
  gateway_request_id_filter?: string | null
  gateway_no_rows?: boolean
}

export type BillingEndpointRow = {
  sku_name: string
  endpoint_name: string
  dbu: number
  usd_per_dbu: number | null
  list_usd: number | null
}

/** Unscoped MODEL_SERVING billing rollup for the same window when scoped endpoint_name filter matched nothing. */
export type BillingWorkspaceReference = {
  hours?: number
  total_dbu: number | null
  total_list_usd: number | null
  currency_code?: string
  by_endpoint?: BillingEndpointRow[]
  pricing_partial?: boolean
  note?: string
}

export type BillingDiagnostic = {
  configured_workspace_id?: string | null
  top_workspaces?: {
    workspace_id: string
    billing_origin_product: string
    usage_quantity: number
  }[]
  products_for_configured_workspace?: {
    billing_origin_product: string
    usage_quantity: number
  }[]
  error?: string | null
}

export type BillingCostSummary = {
  hours: number
  total_dbu: number | null
  total_list_usd: number | null
  currency_code: string
  by_endpoint: BillingEndpointRow[]
  pricing_partial: boolean
  error: string | null
  note: string
  attribution?: string
  endpoint_match_terms?: string[] | null
  workspace_reference?: BillingWorkspaceReference | null
  diagnostic?: BillingDiagnostic | null
  usage_source?: string | null
  total_list_usd_billing_table?: number | null
  model_serving_products?: { billing_origin_product: string; usage_quantity: number }[]
}

export type CostByRequestRow = {
  request_id: string
  gateway_input_tokens?: number | null
  gateway_output_tokens?: number | null
  gateway_total_tokens?: number | null
  est_payload_tokens?: number
  inference_requests?: number
  est_list_usd_prorated?: number | null
  destination_model?: string | null
}

export type CostSummaryResponse = {
  hours: number
  method: string
  note: string
  total_est_tokens: number | null
  by_destination: CostByDest[]
  hourly: CostHourly[]
  by_request?: CostByRequestRow[]
  error: string | null
  ai_gateway?: AiGatewayCostSummary | null
  billing?: BillingCostSummary | null
  token_primary_source?: string | null
  token_cost_estimate?: {
    usd_per_1m_tokens?: number | null
    estimated_usd?: number | null
    note?: string | null
  } | null
  filter?: {
    source_table: string | null
    gateway_model: string | null
    source_tables?: string[] | null
    gateway_models?: string[] | null
    request_id?: string | null
    gateway_destination_ids_derived?: string[] | null
    billing_endpoint_terms?: string[] | null
    resolved_fqn: string | null
  } | null
}

export type TraceRow = {
  request_id: string | null
  event_time: string
  status_code: number | null
  latency_ms: number | null
  destination_id: string | null
  url: string | null
  api_type: string | null
  requester: string | null
  /** Parsed from request body when agent logs OpenAI `user` or metadata (email, display_name, …). */
  request_actor?: string | null
  request_preview: string | null
  response_preview: string | null
  comparison_group_id?: string | null
  /** From inference row when logged; see trace page for AI Gateway metering. */
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
}

export type TracesListResponse = {
  traces: TraceRow[]
  error: string | null
}

export type ComparisonRow = {
  request_id: string | null
  event_time: string | null
  destination_id: string | null
  model_or_destination: string | null
  status_code: unknown
  latency_ms: unknown
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  est_list_usd_prorated: number | null
  ai_gateway_usage: Record<string, unknown> | null
}

export type ComparisonGroupResponse = {
  group_id?: string
  error?: string
  rows: ComparisonRow[]
  comparison_column?: string
  tokens_sum_gateway?: number | null
  billing_window?: Record<string, unknown>
  ai_gateway_batch_error?: string | null
  note?: string
}

export type ReplayTargetInfo = {
  id: string
  label: string
}

export type ReplayTargetsResponse = {
  targets: ReplayTargetInfo[]
  diagnostics?: {
    configured_from?: string
    raw_length?: number
    parse_error?: string
    hint?: string
    load_notes?: string[]
  }
}

export type CompareResultRow = {
  target_id: string
  label: string
  status_code: number | null
  latency_ms: number
  usage: { input_tokens?: unknown; output_tokens?: unknown; total_tokens?: unknown } | null
  answer?: string | null
  response_preview?: string | null
  model?: string | null
  est_list_usd?: number | null
  cost_source?: string | null
  error: string | null
  hint?: string | null
}

export type CompareObjectiveSummary = {
  fastest_target_id?: string | null
  cheapest_target_id?: string | null
  fewest_tokens_target_id?: string | null
  note?: string
}

export type CompareCostEstimate = {
  usd_per_1m_tokens?: number | null
  cost_source?: string | null
  note?: string | null
}

export type ReplayResultRow = CompareResultRow

export type ReplayRunResponse = {
  request_id?: string
  error?: string | null
  question?: string | null
  track_in_dashboard?: boolean
  objective_summary?: CompareObjectiveSummary | null
  cost_estimate?: CompareCostEstimate | null
  tracking_note?: string | null
  results: ReplayResultRow[]
}

export type BenchmarkResultRow = CompareResultRow

export type BenchmarkPromptResponse = {
  error?: string | null
  hint?: string
  note?: string
  question?: string | null
  track_in_dashboard?: boolean
  objective_summary?: CompareObjectiveSummary | null
  cost_estimate?: CompareCostEstimate | null
  tracking_note?: string | null
  results: BenchmarkResultRow[]
}

export async function postBenchmarkPrompt(body: {
  messages: { role: string; content: string }[]
  max_tokens?: number
  temperature?: number
  target_ids?: string[]
  track_in_dashboard?: boolean
}): Promise<BenchmarkPromptResponse> {
  const res = await fetch('/api/v1/benchmark/prompt', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return parseJson<BenchmarkPromptResponse>(res)
}

export type LineageGraphNode = {
  id: string
  title: string
  detail: string
  kind: string
}

export type LineageGraph = {
  nodes: LineageGraphNode[]
  edges: { from: string; to: string }[]
}

export type TraceDetailResponse = {
  request_id?: string
  comparison_group_id?: string | null
  error?: string
  record?: Record<string, unknown>
  request_json?: unknown
  response_json?: unknown
  /** Full response string when JSON parse fails or for quick display */
  response_raw?: string | null
  reasoning_summary?: string | null
  internal_lineage?: { step: string; detail: string }[]
  lineage_graph?: LineageGraph | null
  ai_gateway_usage?: Record<string, unknown> | null
  ai_gateway_usage_error?: string | null
  cost_attribution?: CostAttribution | null
  /** Which inference columns were resolved for bodies (debug UX). */
  payload_columns_resolved?: { request_body?: string | null; response_body?: string | null } | null
}

export type QualityObservabilityResponse = {
  window_hours: number
  avg_latency_ms: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  error_rate_pct: number | null
  requests_sampled_for_json: number
  responses_with_reasoning_pct: number | null
  error: string | null
  source?: string | null
  note?: string | null
  fallback_gateway_metrics?: boolean
  inference_notes?: string | null
  latency_column_used?: string | null
  response_column_used?: string | null
}

export type QualityTrendPoint = {
  day: string
  avg_latency_ms: number
  error_rate_pct: number
}

export type QualityTrendResponse = {
  days: number
  points: QualityTrendPoint[]
  error: string | null
}

export type LineageNode = {
  id: string
  label: string
  fqn: string
}

export type LineageEdge = {
  source: string | null
  target: string | null
  entity_type: string | null
  created_by: string | null
  event_time: string
}

export type RuntimeFlowRoute = {
  destination_id: string | null
  url: string | null
  api_type: string | null
  requests: number
}

export type RuntimeFlowModel = {
  model: string | null
  requests: number
}

export type RuntimeFlowCaller = {
  requester: string
  requests: number
  last_seen?: string | null
}

export type RuntimeFlowResponse = {
  window_days: number
  distinct_callers: number | null
  total_requests: number | null
  routes: RuntimeFlowRoute[]
  models: RuntimeFlowModel[]
  callers?: RuntimeFlowCaller[]
  error: string | null
  note?: string | null
}

export type TelemetryNodeUsage = {
  role?: string
  fqn?: string
  requests_7d?: number | null
  est_tokens_7d?: number | null
  used_by?: string[]
}

export type TelemetryHierarchyNode = {
  id: string
  kind: string
  label: string
  detail?: string | null
  meta?: string | null
  usage?: TelemetryNodeUsage | null
}

export type TelemetryHierarchyGraph = {
  nodes: TelemetryHierarchyNode[]
  edges: { from: string; to: string }[]
}

export type GovernanceLineageResponse = {
  inference_table: string | null
  inference_table_fqns?: string[] | null
  inference_table_primary?: string | null
  inference_table_display?: string | null
  edges: LineageEdge[]
  nodes: LineageNode[]
  error: string | null
  hint: string
  runtime_flow: RuntimeFlowResponse
  telemetry_hierarchy?: TelemetryHierarchyGraph | null
  cost_tokens_hierarchy?: TelemetryHierarchyGraph | null
  has_uc_lineage?: boolean
  system_tables_doc_url: string
  lineage_system_table_doc_url: string
  uc_lineage_query_error: string | null
  workspace_lineage_recent?: LineageEdge[]
  workspace_lineage_note?: string
  workspace_lineage_recent_error?: string | null
  pii_scan?: PiiScanResponse | null
}

export type MlflowRunRow = {
  run_id: string | null
  experiment_id: string | null
  run_name: string | null
  status: string | null
  start_time: string
  created_by: string | null
}

export type MlflowExperimentRow = {
  experiment_id: string | null
  name: string | null
}

export type MlflowOverviewResponse = {
  workspace_id_filter: string | null
  experiments_count: number | null
  runs_count: number | null
  experiments_sample: MlflowExperimentRow[]
  recent_runs: MlflowRunRow[]
  error: string | null
  doc_url: string
}

export type GovernanceAuditEvent = {
  request_id: string | null
  event_time: string
  requester: string | null
  status_code: number | null
  latency_ms: number | null
  destination_id: string | null
}

export type GovernanceAuditResponse = {
  events: GovernanceAuditEvent[]
  error: string | null
}

export async function fetchAgentsCatalog(): Promise<AgentsCatalogResponse> {
  const res = await fetch('/api/v1/agents/catalog')
  return parseJson<AgentsCatalogResponse>(res)
}

export type AgentCatalogEntry = {
  key: string
  kind: string
  label: string
  fqn?: string
  gateway_model?: string
  requests_preview?: number
  total_tokens_preview?: number
}

export type AgentsCatalogResponse = {
  agents: AgentCatalogEntry[]
  catalog_mode?: 'unified_gateway' | 'inference_only' | string
  payload_table_count?: number
  gateway_distinct_models?: number
  fqns_note?: string | null
  gateway_error?: string | null
  gateway_hours_sampled?: number | null
  error?: string | null
}

export type AnalyticsAgentOpts = {
  agent?: string | null
  agents?: string[] | null
  /** Single pinned request (legacy). */
  task?: string | null
  requestId?: string | null
  /** Multiple pinned requests — combined cost/tokens (OR). */
  tasks?: string[] | null
}

function appendAnalyticsScope(q: URLSearchParams, opts?: AnalyticsAgentOpts) {
  if (opts?.agents?.length) {
    for (const a of opts.agents) {
      if (a) q.append('agents', a)
    }
  } else if (opts?.agent) {
    q.set('agent', opts.agent)
  }
  if (opts?.tasks?.length) {
    for (const t of opts.tasks) {
      if (t?.trim()) q.append('tasks', t.trim())
    }
  } else {
    const rid = (opts?.task?.trim() || opts?.requestId?.trim()) ?? ''
    if (rid) q.set('task', rid)
  }
}

export async function fetchHealthTimeseries(
  hours = 168,
  opts?: AnalyticsAgentOpts,
): Promise<HealthTimeseriesResponse> {
  const q = new URLSearchParams({ hours: String(hours) })
  appendAnalyticsScope(q, opts)
  const res = await fetch(`/api/v1/analytics/health/timeseries?${q}`)
  return parseJson<HealthTimeseriesResponse>(res)
}

export async function fetchHealthSlo(
  p95TargetMs = 2000,
  errorBudgetPct = 1,
  opts?: AnalyticsAgentOpts,
): Promise<HealthSloResponse> {
  const q = new URLSearchParams({
    p95_target_ms: String(p95TargetMs),
    error_budget_pct: String(errorBudgetPct),
  })
  appendAnalyticsScope(q, opts)
  const res = await fetch(`/api/v1/analytics/health/slo?${q}`)
  return parseJson<HealthSloResponse>(res)
}

export async function fetchCostSummary(
  hours = 168,
  opts?: AnalyticsAgentOpts,
): Promise<CostSummaryResponse> {
  const q = new URLSearchParams({ hours: String(hours) })
  appendAnalyticsScope(q, opts)
  const res = await fetch(`/api/v1/analytics/cost/summary?${q}`)
  return parseJson<CostSummaryResponse>(res)
}

export async function fetchTraces(limit = 40, opts?: AnalyticsAgentOpts): Promise<TracesListResponse> {
  const q = new URLSearchParams({ limit: String(limit) })
  appendAnalyticsScope(q, opts)
  const res = await fetch(`/api/v1/analytics/traces?${q}`)
  return parseJson<TracesListResponse>(res)
}

export async function fetchTraceDetail(requestId: string): Promise<TraceDetailResponse> {
  const res = await fetch(`/api/v1/analytics/traces/${encodeURIComponent(requestId)}`)
  return parseJson<TraceDetailResponse>(res)
}

export async function fetchComparisonGroup(groupId: string): Promise<ComparisonGroupResponse> {
  const q = new URLSearchParams({ group_id: groupId })
  const res = await fetch(`/api/v1/analytics/comparison?${q}`)
  return parseJson<ComparisonGroupResponse>(res)
}

export async function fetchReplayTargets(): Promise<ReplayTargetsResponse> {
  const res = await fetch('/api/v1/replay/targets')
  return parseJson<ReplayTargetsResponse>(res)
}

export async function postReplayRun(
  requestId: string,
  options?: { targetIds?: string[]; trackInDashboard?: boolean },
): Promise<ReplayRunResponse> {
  const res = await fetch('/api/v1/replay/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      request_id: requestId,
      target_ids: options?.targetIds ?? null,
      track_in_dashboard: options?.trackInDashboard ?? false,
    }),
  })
  return parseJson<ReplayRunResponse>(res)
}

export async function fetchQualityObservability(hours = 168): Promise<QualityObservabilityResponse> {
  const res = await fetch(`/api/v1/analytics/quality/observability?hours=${hours}`)
  return parseJson<QualityObservabilityResponse>(res)
}

export async function fetchQualityTrend(days = 14): Promise<QualityTrendResponse> {
  const res = await fetch(`/api/v1/analytics/quality/trend?days=${days}`)
  return parseJson<QualityTrendResponse>(res)
}

export async function fetchGovernanceLineage(limit = 80): Promise<GovernanceLineageResponse> {
  const res = await fetch(`/api/v1/analytics/governance/lineage?limit=${limit}`)
  return parseJson<GovernanceLineageResponse>(res)
}

export async function fetchMlflowOverview(limit = 20): Promise<MlflowOverviewResponse> {
  const res = await fetch(`/api/v1/analytics/mlflow/overview?limit=${limit}`)
  return parseJson<MlflowOverviewResponse>(res)
}

export async function fetchGovernanceAudit(limit = 40): Promise<GovernanceAuditResponse> {
  const res = await fetch(`/api/v1/analytics/governance/audit?limit=${limit}`)
  return parseJson<GovernanceAuditResponse>(res)
}

export type StatusSummary = {
  generated_at: string
  sql_ok: boolean
  sql_error?: string | null
  agent_count: number
  payload_table_count: number
  fqns_note?: string | null
  exclude_test_requests: boolean
  host?: string | null
}

export type ChangeAlert = {
  severity: string
  title: string
  detail: string
  metric?: string
  model?: string
}

export type HealthCheckItem = {
  name: string
  ok: boolean
  detail: string
  fix_hint?: string | null
}

export type HealthCheckResponse = {
  checks: HealthCheckItem[]
  passed: number
  total: number
  all_ok: boolean
  error?: string
}

export type CompareScorecardRow = {
  route: string
  label: string
  fqn?: string | null
  requests_7d: number
  total_tokens_7d: number
  input_tokens_7d: number
  output_tokens_7d: number
  display_labels?: string[]
  in_replay_targets: boolean
}

export type CompareScorecardResponse = {
  window_hours: number
  rows: CompareScorecardRow[]
  gateway_error?: string | null
  target_count: number
}

export type UserSettings = {
  exclude_test_requests: boolean
  default_compare_prompt?: string
  pinned_dashboard_note?: string | null
}

export type PiiScanResponse = {
  matches: number
  samples: { request_id: string | null; kinds: string[]; preview: string }[]
  error?: string | null
  scanned_rows: number
  window_days?: number
  note?: string
}

export type CostAttribution = {
  request_id: string
  gateway_tokens: number | null
  /** How tokens were correlated (ai_gateway row vs heuristic vs completion.usage). */
  metering_source?: string | null
  list_usd: number | null
  dbu: number | null
  attribution?: string | null
  by_endpoint?: { endpoint_name?: string; dbu?: number; list_usd?: number }[]
  note?: string | null
  error?: string | null
}

export async function fetchStatusSummary(): Promise<StatusSummary> {
  const res = await fetch('/api/v1/status/summary')
  return parseJson<StatusSummary>(res)
}

export async function fetchAlerts(hours = 168): Promise<{ alerts: ChangeAlert[]; window_hours: number }> {
  const res = await fetch(`/api/v1/alerts/changes?hours=${hours}`)
  return parseJson(res)
}

export async function runHealthCheck(): Promise<HealthCheckResponse> {
  const res = await fetch('/api/v1/health-check/workspace')
  return parseJson<HealthCheckResponse>(res)
}

export async function fetchCompareScorecard(hours = 168): Promise<CompareScorecardResponse> {
  const res = await fetch(`/api/v1/compare/scorecard?hours=${hours}`)
  return parseJson<CompareScorecardResponse>(res)
}

export async function fetchSettings(): Promise<UserSettings> {
  const res = await fetch('/api/v1/settings')
  return parseJson<UserSettings>(res)
}

export async function patchSettings(patch: Partial<UserSettings>): Promise<UserSettings> {
  const res = await fetch('/api/v1/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return parseJson<UserSettings>(res)
}
