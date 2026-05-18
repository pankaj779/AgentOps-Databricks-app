import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { X } from 'lucide-react'
import { ModelCompareResults } from '@/components/ModelCompareResults'
import { RequestLineageGraph } from '@/components/RequestLineageGraph'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'
import type {
  ComparisonGroupResponse,
  MlflowOverviewResponse,
  QualityObservabilityResponse,
  QualityTrendResponse,
  ReplayRunResponse,
  ReplayTargetsResponse,
  TraceDetailResponse,
  TraceRow,
  TracesListResponse,
} from '@/lib/api'
import {
  fetchComparisonGroup,
  fetchMlflowOverview,
  fetchQualityObservability,
  fetchQualityTrend,
  fetchReplayTargets,
  fetchTraceDetail,
  fetchTraces,
  postReplayRun,
} from '@/lib/api'

const axisProps = {
  stroke: 'var(--color-muted)',
  tick: { fill: 'var(--color-muted)', fontSize: 10 },
}

export function QualityView() {
  const location = useLocation()
  const { agents: scopeAgents, task, setTask } = useWorkspaceSelection()
  const [obs, setObs] = useState<QualityObservabilityResponse | null>(null)
  const [trend, setTrend] = useState<QualityTrendResponse | null>(null)
  const [traces, setTraces] = useState<TracesListResponse | null>(null)
  const [mlf, setMlf] = useState<MlflowOverviewResponse | null>(null)
  const [detail, setDetail] = useState<TraceDetailResponse | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [comp, setComp] = useState<ComparisonGroupResponse | null>(null)
  const [compLoading, setCompLoading] = useState(false)
  const [replayTargets, setReplayTargets] = useState<ReplayTargetsResponse | null>(null)
  const [replayBusy, setReplayBusy] = useState(false)
  const [replayResult, setReplayResult] = useState<ReplayRunResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [traceSearch, setTraceSearch] = useState('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [o, t, m] = await Promise.all([
          fetchQualityObservability(),
          fetchQualityTrend(14),
          fetchMlflowOverview(25),
        ])
        if (!cancelled) {
          setObs(o)
          setTrend(t)
          setMlf(m)
          setErr(null)
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : 'Failed to load')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const tr = await fetchTraces(30, { agents: scopeAgents.length ? scopeAgents : null })
        if (!cancelled) setTraces(tr)
      } catch (e) {
        if (!cancelled)
          setTraces({ traces: [], error: e instanceof Error ? e.message : 'Failed to load traces' })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [scopeAgents.join('\0')])

  useEffect(() => {
    setTraceSearch('')
  }, [scopeAgents.join('\0')])

  useEffect(() => {
    if (!task) {
      setDetail(null)
      return
    }
    let cancelled = false
    setLoadingDetail(true)
    setDetail(null)
    setReplayResult(null)
    setComp(null)
    void fetchTraceDetail(task)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch((e) => {
        if (!cancelled)
          setDetail({
            error: e instanceof Error ? e.message : 'failed',
            request_id: task,
          })
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false)
      })
    return () => {
      cancelled = true
    }
  }, [task])

  useEffect(() => {
    if (!task || !detail?.comparison_group_id) {
      setComp(null)
      return
    }
    let cancelled = false
    setCompLoading(true)
    void fetchComparisonGroup(detail.comparison_group_id)
      .then((c) => {
        if (!cancelled) setComp(c)
      })
      .catch(() => {
        if (!cancelled) setComp({ error: 'Failed to load comparison group', rows: [] })
      })
      .finally(() => {
        if (!cancelled) setCompLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [task, detail?.comparison_group_id])

  useEffect(() => {
    if (!task) return
    let cancelled = false
    void fetchReplayTargets()
      .then((t) => {
        if (!cancelled) setReplayTargets(t)
      })
      .catch(() => {
        if (!cancelled) setReplayTargets({ targets: [] })
      })
    return () => {
      cancelled = true
    }
  }, [task])

  const openTrace = (row: TraceRow) => {
    if (!row.request_id) return
    setTask(row.request_id)
  }

  const filteredTraces = useMemo(() => {
    const rows = traces?.traces ?? []
    const q = traceSearch.trim().toLowerCase()
    if (!q) return rows
    return rows.filter((t) => {
      const id = (t.request_id ?? '').toLowerCase()
      const grp = (t.comparison_group_id ?? '').toLowerCase()
      const caller = (t.requester ?? '').toLowerCase()
      return id.includes(q) || grp.includes(q) || caller.includes(q)
    })
  }, [traces?.traces, traceSearch])

  const trendPts =
    trend?.points.map((p) => ({
      day: p.day.slice(5),
      avg_latency_ms: Math.round(p.avg_latency_ms),
      error_rate_pct: Number(p.error_rate_pct.toFixed(2)),
    })) ?? []

  return (
    <div className="space-y-6 p-6">
      {err ? <p className="text-sm text-[var(--color-danger)]">{err}</p> : null}
      {obs?.error ? <p className="text-sm text-[var(--color-warn-fg)]">{obs.error}</p> : null}

      <Card title="Production observability">
        {obs ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Metric title="p50 latency" value={obs.p50_latency_ms != null ? `${Math.round(obs.p50_latency_ms)} ms` : '—'} />
            <Metric title="p95 latency" value={obs.p95_latency_ms != null ? `${Math.round(obs.p95_latency_ms)} ms` : '—'} />
            <Metric title="Avg latency" value={obs.avg_latency_ms != null ? `${Math.round(obs.avg_latency_ms)} ms` : '—'} />
            <Metric
              title="Error rate (24h)"
              value={obs.error_rate_pct != null ? `${obs.error_rate_pct.toFixed(2)}%` : '—'}
            />
            <Metric
              title="Responses w/ reasoning"
              value={
                obs.responses_with_reasoning_pct != null
                  ? `${obs.responses_with_reasoning_pct}% (n=${obs.requests_sampled_for_json})`
                  : '—'
              }
            />
          </div>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">Loading…</p>
        )}
      </Card>

      <Card title="MLflow (system tables)">
        {mlf?.error ? (
          <p className="text-sm text-[var(--color-warn-fg)]">{mlf.error}</p>
        ) : !mlf ? (
          <p className="text-sm text-[var(--color-muted)]">Loading…</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-3 text-sm">
              <Badge tone="teal">{mlf.experiments_count ?? 0} experiments</Badge>
              <Badge tone="neutral">{mlf.runs_count ?? 0} runs</Badge>
              {mlf.workspace_id_filter ? (
                <span className="text-xs text-[var(--color-muted)]">workspace {mlf.workspace_id_filter}</span>
              ) : null}
            </div>
            <a
              href={mlf.doc_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-xs text-[var(--color-teal)] underline"
            >
              Databricks MLflow system tables reference
            </a>
            {(mlf.experiments_sample ?? []).length > 0 ? (
              <div className="mt-4">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  Recent experiments
                </div>
                <ul className="mt-2 max-h-32 space-y-1 overflow-y-auto text-xs">
                  {(mlf.experiments_sample ?? []).map((ex) => (
                    <li key={ex.experiment_id ?? ex.name} className="font-mono text-[var(--color-fg)]">
                      {ex.name ?? '—'} <span className="text-[var(--color-muted)]">({ex.experiment_id})</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {(mlf.recent_runs ?? []).length > 0 ? (
              <div className="mt-4 max-h-48 overflow-auto rounded-lg border border-[var(--color-border)]">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                    <tr>
                      <th className="px-2 py-2">Run</th>
                      <th className="px-2 py-2">Status</th>
                      <th className="px-2 py-2">Started</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--color-border)]">
                    {(mlf.recent_runs ?? []).map((r) => (
                      <tr key={r.run_id}>
                        <td className="max-w-[200px] truncate px-2 py-2 font-mono text-[var(--color-fg)]">
                          {r.run_name ?? r.run_id}
                        </td>
                        <td className="px-2 py-2">
                          <Badge tone="neutral">{r.status ?? '—'}</Badge>
                        </td>
                        <td className="whitespace-nowrap px-2 py-2 text-[var(--color-muted)]">
                          {r.start_time.slice(5, 19).replace('T', ' ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="mt-4 text-sm text-[var(--color-muted)]">
                No MLflow runs in <code className="text-xs">system.mlflow.runs_latest</code> for this workspace yet — run
                an experiment or notebook with MLflow tracking, then refresh.
              </p>
            )}
          </>
        )}
      </Card>

      <Card
        title="Which model is cheapest for the same ask?"
        subtitle="Two honest approaches — use both when you can."
      >
        <ul className="list-inside list-disc space-y-2 text-xs text-[var(--color-muted)]">
          <li>
            <span className="font-medium text-[var(--color-fg)]">Historical traces</span> — compare token and cost
            distributions from production (Cost tab + trace drawer). This reflects your real workload mix, but{' '}
            <span className="text-[var(--color-fg)]">does not guarantee the same prompt</span> across models.
          </li>
          <li>
            <span className="font-medium text-[var(--color-fg)]">Controlled replay</span> — same request JSON, same decode
            assumptions, different endpoints. When a trace has a{' '}
            <span className="font-mono text-[var(--color-fg)]">comparison_group_id</span>, the request drawer shows a
            side-by-side table. You can also configure{' '}
            <span className="font-mono text-[10px] text-[var(--color-fg)]">AGENTOPS_REPLAY_TARGETS_JSON</span> and use{' '}
            <span className="font-medium text-[var(--color-fg)]">Run all replay targets</span> inside an open trace (
            <span className="text-[var(--color-fg)]">localhost / reachable URLs only</span>).
          </li>
        </ul>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Latency & error trend">
          {trend?.error ? (
            <p className="text-sm text-[var(--color-warn-fg)]">{trend.error}</p>
          ) : trendPts.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">No points yet.</p>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={trendPts} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.6} />
                  <XAxis dataKey="day" {...axisProps} />
                  <YAxis yAxisId="l" {...axisProps} width={40} />
                  <YAxis yAxisId="r" orientation="right" {...axisProps} width={36} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--color-surface-elevated)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 8,
                      color: 'var(--color-fg)',
                      fontSize: 12,
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line
                    yAxisId="l"
                    type="monotone"
                    dataKey="avg_latency_ms"
                    name="Avg latency (ms)"
                    stroke="var(--color-teal)"
                    strokeWidth={2}
                    dot={{ r: 2 }}
                  />
                  <Line
                    yAxisId="r"
                    type="monotone"
                    dataKey="error_rate_pct"
                    name="Error %"
                    stroke="var(--color-accent)"
                    strokeWidth={2}
                    dot={{ r: 2 }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="Trace explorer">
          {scopeAgents.length ? (
            <p className="mb-2 text-xs text-[var(--color-muted)]">
              Showing traces scoped to{' '}
              {scopeAgents.length === 1 ? (
                <span className="font-mono text-[var(--color-fg)]">{scopeAgents[0]}</span>
              ) : (
                <span className="font-mono text-[var(--color-fg)]">
                  {scopeAgents.length} agents (combined OR)
                </span>
              )}
              . Clear scope from the banner for all tasks.
            </p>
          ) : (
            <p className="mb-2 text-xs text-[var(--color-muted)]">
              Workspace-wide traces. Scope an agent from Overview for a shorter list.
            </p>
          )}
          <input
            type="search"
            value={traceSearch}
            onChange={(e) => setTraceSearch(e.target.value)}
            placeholder="Filter by request id, comparison group, or caller…"
            className="mb-2 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5 text-xs text-[var(--color-fg)] placeholder:text-[var(--color-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40"
            aria-label="Filter traces"
          />
          {traces?.error ? (
            <p className="text-sm text-[var(--color-warn-fg)]">{traces.error}</p>
          ) : (
            <div className="max-h-[320px] overflow-auto rounded-lg border border-[var(--color-border)]">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 z-10 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  <tr>
                    <th className="px-2 py-2">Time</th>
                    <th className="px-2 py-2">Group</th>
                    <th className="px-2 py-2">Status</th>
                    <th className="px-2 py-2">ms</th>
                    <th className="px-2 py-2">Caller</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-border)] text-[var(--color-fg)]">
                  {filteredTraces.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-2 py-8 text-center text-[var(--color-muted)]">
                        {(traces?.traces ?? []).length === 0
                          ? 'No traces in this window — confirm inference logging, or clear the Focus banner if the scope is too narrow.'
                          : 'No rows match your filter.'}
                      </td>
                    </tr>
                  ) : (
                    filteredTraces.map((t) => (
                      <tr
                        key={t.request_id ?? t.event_time}
                        className="cursor-pointer hover:bg-[var(--color-accent-soft)]/40"
                        onClick={() => openTrace(t)}
                      >
                        <td className="px-2 py-2 whitespace-nowrap text-[var(--color-muted)]">
                          {t.event_time.slice(5, 16).replace('T', ' ')}
                        </td>
                        <td className="max-w-[72px] truncate px-2 py-2 text-[var(--color-muted)]" title={t.comparison_group_id ?? ''}>
                          {t.comparison_group_id ? (
                            <span className="rounded bg-[var(--color-accent-soft)] px-1 font-mono text-[10px] text-[var(--color-fg)]">
                              {t.comparison_group_id.length > 10
                                ? `${t.comparison_group_id.slice(0, 8)}…`
                                : t.comparison_group_id}
                            </span>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td className="px-2 py-2">
                          <Badge tone={t.status_code != null && t.status_code >= 400 ? 'warn' : 'teal'}>
                            {t.status_code ?? '—'}
                          </Badge>
                        </td>
                        <td className="px-2 py-2 tabular-nums text-[var(--color-muted)]">
                          {t.latency_ms != null ? Math.round(t.latency_ms) : '—'}
                        </td>
                        <td className="max-w-[140px] truncate px-2 py-2 text-[var(--color-muted)]">
                          {t.requester ?? '—'}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      {task ? (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center bg-black/60 p-4 sm:items-center"
          role="dialog"
          aria-modal="true"
        >
          <div className="max-h-[90vh] w-full max-w-5xl overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-xl">
            <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-[var(--color-fg)]">Request trace</div>
                <div className="font-mono text-[11px] text-[var(--color-muted)]">{detail?.request_id}</div>
                {detail?.request_id ? (
                  <Link
                    className="mt-1 inline-block text-[11px] text-[var(--color-teal)] hover:underline"
                    to={{
                      pathname: `/trace/${encodeURIComponent(detail.request_id)}`,
                      search: location.search,
                    }}
                  >
                    Open full-page detail
                  </Link>
                ) : null}
              </div>
              <button
                type="button"
                className="rounded-lg p-2 text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]"
                onClick={() => setTask(null)}
                aria-label="Close"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="max-h-[calc(90vh-52px)] overflow-y-auto p-4 text-sm">
              {loadingDetail ? <p className="text-[var(--color-muted)]">Loading trace…</p> : null}
              {detail?.error ? (
                <p className="text-[var(--color-danger)]">{detail.error}</p>
              ) : null}
              {!loadingDetail && !detail?.comparison_group_id && detail?.request_id ? (
                <div className="mb-4 rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface-elevated)]/40 p-3 text-xs text-[var(--color-muted)]">
                  <div className="font-semibold text-[var(--color-fg)]">Compare same task across models</div>
                  <p className="mt-1">
                    <strong className="text-[var(--color-fg)]">Live benchmark:</strong> edit
                    <code className="text-[10px]"> backend/replay_targets.json</code> with your 5 gateway URLs, restart the API.
                  </p>
                  <p className="mt-1">
                    <strong className="text-[var(--color-fg)]">Production compare:</strong> log the same
                    <code className="text-[10px]"> comparison_group_id</code> on each route for the same question.
                  </p>
                </div>
              ) : null}
              {!loadingDetail && detail?.comparison_group_id ? (
                <div className="mb-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/50 p-3 text-xs">
                  <div className="font-semibold text-[var(--color-teal)]">Cross-model comparison</div>
                  <div className="mt-1 font-mono text-[var(--color-muted)]">group: {detail.comparison_group_id}</div>
                  {compLoading ? <p className="mt-2 text-[var(--color-muted)]">Loading comparison…</p> : null}
                  {comp?.error ? <p className="mt-2 text-[var(--color-warn-fg)]">{comp.error}</p> : null}
                  {!compLoading && comp?.rows && comp.rows.length > 0 ? (
                    <>
                      <div className="mt-2 max-h-48 overflow-auto rounded-lg border border-[var(--color-border)]">
                        <table className="w-full text-left text-[11px]">
                          <thead className="sticky top-0 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                            <tr>
                              <th className="px-2 py-1.5">Model / route</th>
                              <th className="px-2 py-1.5">In</th>
                              <th className="px-2 py-1.5">Out</th>
                              <th className="px-2 py-1.5">Total</th>
                              <th className="px-2 py-1.5">Est $</th>
                              <th className="px-2 py-1.5">Latency</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[var(--color-border)]">
                            {comp.rows.map((r, i) => (
                              <tr key={r.request_id ?? `row-${i}`}>
                                <td className="max-w-[180px] truncate px-2 py-1.5 font-mono text-[var(--color-fg)]">
                                  {r.model_or_destination ?? r.destination_id ?? '—'}
                                </td>
                                <td className="px-2 py-1.5 tabular-nums text-[var(--color-muted)]">{r.input_tokens ?? '—'}</td>
                                <td className="px-2 py-1.5 tabular-nums text-[var(--color-muted)]">{r.output_tokens ?? '—'}</td>
                                <td className="px-2 py-1.5 tabular-nums text-[var(--color-fg)]">{r.total_tokens ?? '—'}</td>
                                <td className="px-2 py-1.5 tabular-nums text-[var(--color-muted)]">
                                  {r.est_list_usd_prorated != null ? `$${r.est_list_usd_prorated.toFixed(4)}` : '—'}
                                </td>
                                <td className="px-2 py-1.5 tabular-nums text-[var(--color-muted)]">
                                  {typeof r.latency_ms === 'number' ? `${Math.round(r.latency_ms)}` : '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {comp.note ? <p className="mt-2 text-[10px] text-[var(--color-muted)]">{comp.note}</p> : null}
                    </>
                  ) : !compLoading && detail.comparison_group_id && !comp?.error ? (
                    <p className="mt-2 text-[var(--color-muted)]">No other rows in this group yet.</p>
                  ) : null}
                </div>
              ) : null}
              {!loadingDetail && (replayTargets?.targets?.length ?? 0) > 0 && detail?.request_id ? (
                <div className="mb-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/50 p-3 text-xs">
                  <div className="font-semibold text-[var(--color-teal)]">Replay on alternate endpoints</div>
                  <p className="mt-1 text-[var(--color-muted)]">
                    POSTs the logged request JSON to targets from <code className="text-[10px]">AGENTOPS_REPLAY_TARGETS_JSON</code> (
                    localhost only — requires reachable URLs).
                  </p>
                  <button
                    type="button"
                    disabled={replayBusy}
                    className="mt-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-[var(--color-fg)] enabled:hover:bg-[var(--color-surface-elevated)] disabled:opacity-50"
                    onClick={() => {
                      if (!detail.request_id) return
                      setReplayBusy(true)
                      setReplayResult(null)
                      void postReplayRun(detail.request_id)
                        .then(setReplayResult)
                        .catch((e) =>
                          setReplayResult({
                            error: e instanceof Error ? e.message : 'replay failed',
                            results: [],
                          }),
                        )
                        .finally(() => setReplayBusy(false))
                    }}
                  >
                    {replayBusy ? 'Running…' : 'Run all replay targets'}
                  </button>
                  {replayResult?.error ? (
                    <p className="mt-2 text-[var(--color-warn-fg)]">{replayResult.error}</p>
                  ) : null}
                  <ModelCompareResults
                    results={replayResult?.results ?? []}
                    question={replayResult?.question}
                    objectiveSummary={replayResult?.objective_summary}
                    costEstimate={replayResult?.cost_estimate}
                  />
                </div>
              ) : null}
              {!loadingDetail && detail?.lineage_graph?.nodes?.length ? (
                <div className="mb-4">
                  <RequestLineageGraph graph={detail.lineage_graph} />
                </div>
              ) : null}
              {!loadingDetail &&
              detail?.ai_gateway_usage &&
              detail.ai_gateway_usage.total_tokens != null ? (
                <div className="mb-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/50 px-3 py-2 text-xs text-[var(--color-fg)]">
                  <span className="font-semibold text-[var(--color-teal)]">AI Gateway tokens (this request)</span>
                  <span className="mt-1 block font-mono text-[var(--color-muted)]">
                    in {String(detail.ai_gateway_usage.input_tokens ?? '—')} · out{' '}
                    {String(detail.ai_gateway_usage.output_tokens ?? '—')} · total{' '}
                    {String(detail.ai_gateway_usage.total_tokens ?? '—')}
                  </span>
                </div>
              ) : null}
              {!loadingDetail && detail?.ai_gateway_usage_error ? (
                <p className="mb-3 text-[11px] text-[var(--color-warn-fg)]">{detail.ai_gateway_usage_error}</p>
              ) : null}
              {!loadingDetail && detail?.internal_lineage?.length ? (
                <div className="mb-4 space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                    Internal flow (from logged payload)
                  </div>
                  <ol className="space-y-2 border-l-2 border-[var(--color-teal)] pl-3">
                    {detail.internal_lineage.map((step) => (
                      <li key={step.step}>
                        <div className="font-medium text-[var(--color-fg)]">{step.step}</div>
                        <div className="text-xs text-[var(--color-muted)]">{step.detail}</div>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}
              {detail?.reasoning_summary ? (
                <div className="mb-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/50 p-3">
                  <div className="text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                    Model reasoning (summary)
                  </div>
                  <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs text-[var(--color-fg)]">
                    {detail.reasoning_summary}
                  </pre>
                </div>
              ) : null}
              {detail?.request_json != null ? (
                <details className="mb-3">
                  <summary className="cursor-pointer text-xs font-semibold text-[var(--color-teal)]">
                    Request JSON
                  </summary>
                  <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-black/20 p-2 text-[10px] text-[var(--color-muted)]">
                    {typeof detail.request_json === 'string'
                      ? detail.request_json
                      : JSON.stringify(detail.request_json, null, 2)}
                  </pre>
                </details>
              ) : null}
              {detail?.response_json != null || detail?.response_raw ? (
                <details open>
                  <summary className="cursor-pointer text-xs font-semibold text-[var(--color-teal)]">
                    Response
                  </summary>
                  <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-black/20 p-2 text-[10px] text-[var(--color-muted)]">
                    {detail.response_raw
                      ? detail.response_raw
                      : typeof detail.response_json === 'string'
                        ? detail.response_json
                        : JSON.stringify(detail.response_json, null, 2).slice(0, 12000)}
                  </pre>
                </details>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function Metric({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-3 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">{title}</div>
      <div className="mt-1 text-lg font-semibold text-[var(--color-fg)]">{value}</div>
    </div>
  )
}
