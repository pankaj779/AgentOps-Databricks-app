import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { ModelCompareResults } from '@/components/ModelCompareResults'
import { RequestLineageGraph } from '@/components/RequestLineageGraph'
import { Card } from '@/components/ui/Card'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import type {
  BenchmarkPromptResponse,
  ComparisonGroupResponse,
  ReplayRunResponse,
  ReplayTargetsResponse,
  TraceDetailResponse,
} from '@/lib/api'
import {
  fetchComparisonGroup,
  fetchReplayTargets,
  fetchTraceDetail,
  postBenchmarkPrompt,
  postReplayRun,
} from '@/lib/api'
import { NAV_PATHS } from '@/lib/navigation'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'

function extractMessagesFromRequest(req: unknown): { role: string; content: string }[] {
  if (typeof req === 'string') {
    try {
      return extractMessagesFromRequest(JSON.parse(req))
    } catch {
      return [{ role: 'user', content: '' }]
    }
  }
  if (!req || typeof req !== 'object') return [{ role: 'user', content: '' }]
  const r = req as Record<string, unknown>
  if (Array.isArray(r.messages)) {
    const out: { role: string; content: string }[] = []
    for (const m of r.messages) {
      if (m && typeof m === 'object') {
        const o = m as Record<string, unknown>
        const role = String(o.role ?? 'user')
        const content =
          typeof o.content === 'string' ? o.content : JSON.stringify(o.content ?? '')
        out.push({ role, content })
      }
    }
    if (out.length) return out
  }
  return [{ role: 'user', content: '' }]
}

/** Pretty-print parsed JSON bodies; stringify raw strings safely. */
function formatPrettyJsonBody(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') {
    const s = value.trim()
    if ((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))) {
      try {
        return JSON.stringify(JSON.parse(s), null, 2)
      } catch {
        /* fallthrough */
      }
    }
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function isWrappedRawResponse(v: unknown): v is { _note?: string; _raw?: string } {
  if (typeof v !== 'object' || v === null) return false
  return typeof (v as Record<string, unknown>)._raw === 'string'
}

export function TraceDetailPage() {
  const { requestId: ridParam } = useParams<{ requestId: string }>()
  const location = useLocation()
  const qs = location.search || ''
  const requestId = ridParam ? decodeURIComponent(ridParam) : ''
  const { tasks, toggleTask } = useWorkspaceSelection()
  const pinnedForCost = requestId ? tasks.includes(requestId) : false

  const [detail, setDetail] = useState<TraceDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [comp, setComp] = useState<ComparisonGroupResponse | null>(null)
  const [compLoading, setCompLoading] = useState(false)
  const [replayTargets, setReplayTargets] = useState<ReplayTargetsResponse | null>(null)
  const [replayBusy, setReplayBusy] = useState(false)
  const [replayResult, setReplayResult] = useState<ReplayRunResponse | null>(null)
  const [benchBusy, setBenchBusy] = useState(false)
  const [benchResult, setBenchResult] = useState<BenchmarkPromptResponse | null>(null)
  const [benchMessage, setBenchMessage] = useState('')
  const [trackReplayInDashboard, setTrackReplayInDashboard] = useState(false)
  const [trackBenchmarkInDashboard, setTrackBenchmarkInDashboard] = useState(false)

  useEffect(() => {
    if (!requestId) return
    let cancelled = false
    setLoading(true)
    setDetail(null)
    void fetchTraceDetail(requestId)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch((e) => {
        if (!cancelled)
          setDetail({
            error: e instanceof Error ? e.message : 'failed',
            request_id: requestId,
          })
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [requestId])

  useEffect(() => {
    if (!detail?.request_json) return
    const msgs = extractMessagesFromRequest(detail.request_json)
    const lastUser = [...msgs].reverse().find((m) => m.role === 'user')
    setBenchMessage(lastUser?.content ?? msgs[0]?.content ?? '')
  }, [detail?.request_json])

  useEffect(() => {
    if (!requestId || !detail?.comparison_group_id) {
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
  }, [requestId, detail?.comparison_group_id])

  useEffect(() => {
    void fetchReplayTargets()
      .then(setReplayTargets)
      .catch(() => setReplayTargets({ targets: [] }))
  }, [])

  const resolvedCols = detail?.payload_columns_resolved

  const requestBodyText = useMemo(() => {
    const raw = detail?.request_json ?? detail?.record?.request
    if (raw == null) return ''
    if (typeof raw === 'string' && !raw.trim()) return ''
    return formatPrettyJsonBody(raw)
  }, [detail?.request_json, detail?.record?.request])

  const responseBodyText = useMemo(() => {
    const rj = detail?.response_json
    if (rj != null && isWrappedRawResponse(rj)) {
      const bits = [rj._note, rj._raw].filter(Boolean)
      return bits.join('\n\n')
    }
    if (rj != null) {
      return formatPrettyJsonBody(rj)
    }
    const rec = detail?.record?.response
    if (rec == null || (typeof rec === 'string' && !rec.trim())) return ''
    return formatPrettyJsonBody(rec)
  }, [detail?.response_json, detail?.record?.response])

  const trackLabel = (checked: boolean, onChange: (v: boolean) => void) => (
    <label className="mb-3 flex cursor-pointer items-start gap-2 text-[11px] text-[var(--color-muted)]">
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>
        <span className="font-medium text-[var(--color-fg)]">Track in dashboard</span> — include these test calls in
        Agents &amp; request lists (when off, AgentOps hides them; Databricks may still log and bill).
      </span>
    </label>
  )

  return (
    <div className="min-h-svh bg-[var(--color-bg)] text-[var(--color-fg)]">
      <header className="sticky top-0 z-20 border-b border-[var(--color-border)] bg-[var(--color-surface)]/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3">
          <Link
            to={{ pathname: NAV_PATHS.agents, search: qs }}
            className="inline-flex items-center gap-1 text-sm text-[var(--color-accent)] hover:underline"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
            Agents & requests
          </Link>
          <span className="text-[var(--color-muted)]">/</span>
          <span className="font-mono text-xs text-[var(--color-muted)] truncate max-w-[280px]">
            {requestId || '—'}
          </span>
          {requestId ? (
            <>
              <button
                type="button"
                onClick={() => toggleTask(requestId)}
                className={`rounded-md border px-2 py-1 text-xs ${
                  pinnedForCost
                    ? 'border-[var(--color-teal)] bg-[var(--color-teal)]/10 text-[var(--color-teal)]'
                    : 'border-[var(--color-border)] text-[var(--color-fg)] hover:bg-[var(--color-surface-elevated)]'
                }`}
              >
                {pinnedForCost ? 'Pinned for cost' : 'Add to cost selection'}
              </button>
              <Link
                to={{
                  pathname: NAV_PATHS.cost,
                  search: (() => {
                    const n = new URLSearchParams(qs)
                    n.delete('task')
                    n.delete('tasks')
                    for (const t of tasks.includes(requestId) ? tasks : [...tasks, requestId]) {
                      n.append('tasks', t)
                    }
                    return n.toString()
                  })(),
                }}
                className="text-xs text-[var(--color-accent)] hover:underline"
              >
                Open cost
              </Link>
            </>
          ) : null}
        </div>
      </header>
      <main className="mx-auto max-w-5xl space-y-6 p-4 pb-16">
        {loading ? <LoadingSpinner label="Loading trace…" /> : null}
        {detail?.error ? <p className="text-[var(--color-danger)]">{detail.error}</p> : null}

        {!loading && detail?.comparison_group_id ? (
          <Card title="Cross-model comparison (this task group)" subtitle="Production rows sharing comparison_group_id">
            <div className="font-mono text-[11px] text-[var(--color-muted)]">{detail.comparison_group_id}</div>
            {compLoading ? <LoadingSpinner label="Loading comparison…" size="sm" /> : null}
            {comp?.error ? <p className="text-sm text-[var(--color-warn-fg)]">{comp.error}</p> : null}
            {!compLoading && comp?.rows && comp.rows.length > 0 ? (
              <div className="mt-3 max-h-64 overflow-auto rounded-lg border border-[var(--color-border)]">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase text-[var(--color-muted)]">
                    <tr>
                      <th className="px-2 py-2">Model</th>
                      <th className="px-2 py-2">Total tok</th>
                      <th className="px-2 py-2">Est $</th>
                      <th className="px-2 py-2">Latency</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--color-border)]">
                    {comp.rows.map((r, i) => (
                      <tr key={r.request_id ?? i}>
                        <td className="px-2 py-2 font-mono">{r.model_or_destination ?? r.destination_id ?? '—'}</td>
                        <td className="px-2 py-2 tabular-nums">{r.total_tokens ?? '—'}</td>
                        <td className="px-2 py-2 tabular-nums">
                          {r.est_list_usd_prorated != null ? `$${r.est_list_usd_prorated.toFixed(4)}` : '—'}
                        </td>
                        <td className="px-2 py-2 tabular-nums">
                          {typeof r.latency_ms === 'number' ? `${Math.round(r.latency_ms)}` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Card>
        ) : null}

        {!loading &&
        detail?.ai_gateway_usage &&
        detail.ai_gateway_usage.total_tokens != null ? (
          <Card title="Tokens — this request" subtitle="From AI Gateway usage row when available">
            <div className="font-mono text-sm text-[var(--color-teal)]">
              in {String(detail.ai_gateway_usage.input_tokens ?? '—')} · out{' '}
              {String(detail.ai_gateway_usage.output_tokens ?? '—')} · total{' '}
              {String(detail.ai_gateway_usage.total_tokens ?? '—')}
            </div>
          </Card>
        ) : !loading && detail?.ai_gateway_usage_error ? (
          <Card title="Gateway usage">
            <p className="text-sm text-[var(--color-warn-fg)]">{detail.ai_gateway_usage_error}</p>
          </Card>
        ) : null}

        {!loading && detail?.request_id ? (
          <Card
            title="Replay this request"
            subtitle="Re-sends the same stored JSON body to every route in backend/replay_targets.json (agentops_test, gemma-3-model_payload, llama-4-model_payload)."
          >
            {(replayTargets?.targets?.length ?? 0) === 0 ? (
              <p className="mb-3 text-sm text-[var(--color-warn-fg)]">
                Loading replay targets… If this stays empty, fix{' '}
                <code className="text-[10px]">backend/replay_targets.json</code> (must be a JSON array) and
                restart the API.
              </p>
            ) : (
              <>
            {trackLabel(trackReplayInDashboard, setTrackReplayInDashboard)}
            <button
              type="button"
              disabled={replayBusy}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm disabled:opacity-50"
              onClick={() => {
                if (!detail.request_id) return
                setReplayBusy(true)
                setReplayResult(null)
                void postReplayRun(detail.request_id, { trackInDashboard: trackReplayInDashboard })
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
              <p className="mt-2 text-sm text-[var(--color-warn-fg)]">{replayResult.error}</p>
            ) : null}
            <ModelCompareResults
              results={replayResult?.results ?? []}
              question={replayResult?.question}
              objectiveSummary={replayResult?.objective_summary}
              costEstimate={replayResult?.cost_estimate}
            />
              </>
            )}
          </Card>
        ) : null}

        <Card
          title="Custom prompt benchmark"
          subtitle="Type a new question and send it to all replay targets (different from “Replay this request”, which reuses the stored trace JSON)."
        >
          {(replayTargets?.targets?.length ?? 0) === 0 ? (
            <div className="mb-3 rounded-lg border border-[var(--color-warn-border)] bg-[var(--color-warn-bg)] px-3 py-2 text-[11px] text-[var(--color-warn-fg)]">
              Replay targets not loaded. Add <code className="text-[10px]">backend/replay_targets.json</code> and restart
              the API.
            </div>
          ) : null}
          <p className="mb-2 text-[10px] text-[var(--color-muted)]">
            {(replayTargets?.targets?.length ?? 0)} target(s) configured.
          </p>
          <details className="mb-3 hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/40 px-3 py-2 text-[11px] text-[var(--color-muted)]">
            <summary className="cursor-pointer font-medium text-[var(--color-teal)]">
              Env reference
            </summary>
            <ol className="mt-2 list-inside list-decimal space-y-1.5">
              <li>
                <code className="text-[10px] text-[var(--color-fg)]">AGENTOPS_REPLAY_TARGETS_FILE=replay_targets.json</code>
              </li>
              <li>
                <code className="text-[10px] text-[var(--color-fg)]">AGENTOPS_BENCHMARK_ENABLED=true</code>
              </li>
              <li>Restart FastAPI after edits.</li>
            </ol>
            <p className="mt-2 text-[10px]">
              Targets receive the same JSON body as replay (model + messages + max_tokens). Use only trusted URLs.
            </p>
          </details>
          {trackLabel(trackBenchmarkInDashboard, setTrackBenchmarkInDashboard)}
          <textarea
            value={benchMessage}
            onChange={(e) => setBenchMessage(e.target.value)}
            rows={4}
            className="mb-2 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-2 text-sm"
            placeholder="User message…"
          />
          <button
            type="button"
            disabled={benchBusy || !benchMessage.trim()}
            className="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            onClick={() => {
              setBenchBusy(true)
              setBenchResult(null)
              void postBenchmarkPrompt({
                messages: [{ role: 'user', content: benchMessage.trim() }],
                max_tokens: 256,
                temperature: 0,
                track_in_dashboard: trackBenchmarkInDashboard,
              })
                .then(setBenchResult)
                .catch((e) =>
                  setBenchResult({
                    error: e instanceof Error ? e.message : 'failed',
                    results: [],
                  }),
                )
                .finally(() => setBenchBusy(false))
            }}
          >
            {benchBusy ? 'Calling models…' : 'Test on all configured targets'}
          </button>
          {benchResult?.error ? (
            <p className="mt-2 text-sm text-[var(--color-warn-fg)]">
              {benchResult.error}
              {benchResult.hint ? ` — ${benchResult.hint}` : ''}
            </p>
          ) : null}
          <ModelCompareResults
            results={benchResult?.results ?? []}
            question={benchResult?.question ?? benchMessage}
            objectiveSummary={benchResult?.objective_summary}
            costEstimate={benchResult?.cost_estimate}
          />
        </Card>

        {!loading && detail?.cost_attribution ? (
          <Card title="Cost attribution (this request)">
            <p className="text-xs text-[var(--color-muted)]">
              Why this request costs what it costs — gateway metering + billing.usage match when available.
              {detail.cost_attribution.metering_source === 'completion_response_json'
                ? ' Tokens here come from completion.usage embedded in your inference payload (Agent / Apps traffic often differs from system.ai_gateway.usage request_id).'
                : detail.cost_attribution.metering_source === 'ai_gateway_heuristic_time_destination'
                  ? ' Tokens matched system.ai_gateway.usage by gateway event time + destination_id.'
                  : null}
            </p>
            <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-[10px] uppercase text-[var(--color-muted)]">Gateway tokens</dt>
                <dd className="tabular-nums font-medium text-[var(--color-teal)]">
                  {detail.cost_attribution.gateway_tokens?.toLocaleString() ?? '—'}
                </dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase text-[var(--color-muted)]">List price (est.)</dt>
                <dd className="tabular-nums font-medium">
                  {detail.cost_attribution.list_usd != null
                    ? `USD ${detail.cost_attribution.list_usd.toFixed(6)}`
                    : '—'}
                </dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase text-[var(--color-muted)]">DBU</dt>
                <dd className="tabular-nums">{detail.cost_attribution.dbu ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase text-[var(--color-muted)]">Attribution</dt>
                <dd className="font-mono text-[10px]">{detail.cost_attribution.attribution ?? '—'}</dd>
              </div>
            </dl>
            {(detail.cost_attribution.by_endpoint ?? []).length > 0 ? (
              <ul className="mt-3 space-y-1 text-[11px] font-mono text-[var(--color-muted)]">
                {detail.cost_attribution.by_endpoint!.map((ep, i) => (
                  <li key={i}>
                    {ep.endpoint_name ?? 'endpoint'} · DBU {ep.dbu ?? '—'} · ${ep.list_usd ?? '—'}
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>
        ) : null}

        {!loading && detail?.lineage_graph?.nodes?.length ? (
          <Card title="Request journey" subtitle="Animated path for this request_id">
            <RequestLineageGraph graph={detail.lineage_graph} />
          </Card>
        ) : null}

        {!loading && detail?.internal_lineage?.length ? (
          <Card title="Internal flow">
            <ol className="space-y-2 border-l-2 border-[var(--color-teal)] pl-3 text-sm">
              {detail.internal_lineage.map((step) => (
                <li key={step.step}>
                  <div className="font-medium">{step.step}</div>
                  <div className="text-xs text-[var(--color-muted)]">{step.detail}</div>
                </li>
              ))}
            </ol>
          </Card>
        ) : null}

        {requestBodyText ? (
          <Card>
            {(resolvedCols?.request_body ?? resolvedCols?.response_body) ? (
              <p className="-mt-2 mb-2 text-[10px] text-[var(--color-muted)]">
                Payload columns mapped:{' '}
                <span className="font-mono">
                  request={resolvedCols?.request_body ?? '—'}, response={resolvedCols?.response_body ?? '—'}
                </span>
              </p>
            ) : null}
            <details open>
              <summary className="cursor-pointer text-sm font-semibold text-[var(--color-teal)]">
                Request JSON
              </summary>
              <pre className="mt-2 max-h-[min(60vh,32rem)] overflow-auto rounded-lg bg-black/20 p-2 text-[10px]">
                {requestBodyText}
              </pre>
            </details>
          </Card>
        ) : null}

        {responseBodyText ? (
          <Card>
            <details open>
              <summary className="cursor-pointer text-sm font-semibold text-[var(--color-teal)]">
                Response JSON
              </summary>
              <pre className="mt-2 max-h-[min(70vh,40rem)] whitespace-pre-wrap break-all overflow-auto rounded-lg bg-black/20 p-2 text-[10px]">
                {responseBodyText}
              </pre>
            </details>
          </Card>
        ) : !loading && detail && !detail.error ? (
          <Card subtitle="Inference payload columns">
            <p className="text-sm text-[var(--color-muted)]">
              No response body returned for this row. If Gateway shows text above, UC may omit the assistant payload for
              this model, or use a column name AgentOps doesn’t map yet (
              <span className="font-mono text-[var(--color-fg)]">{resolvedCols?.response_body ?? '—'}</span>).
            </p>
          </Card>
        ) : null}
      </main>
    </div>
  )
}
