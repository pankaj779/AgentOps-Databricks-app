import { useEffect, useState } from 'react'
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

        {!loading && (replayTargets?.targets?.length ?? 0) > 0 && detail?.request_id ? (
          <Card
            title="Replay this request"
            subtitle="Re-sends the exact JSON body stored for this trace to every model in replay_targets.json (same prompt, tools, and params as the original call)."
          >
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
          </Card>
        ) : null}

        <Card
          title="Custom prompt benchmark"
          subtitle="Type any new question below and send it to all replay targets (not the stored trace body — use “Replay this request” above for that)."
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

        {!loading && detail?.lineage_graph?.nodes?.length ? (
          <Card title="Request journey">
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

        {detail?.request_json != null ? (
          <Card>
            <details>
              <summary className="cursor-pointer text-sm font-semibold text-[var(--color-teal)]">
                Request JSON
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-black/20 p-2 text-[10px]">
                {JSON.stringify(detail.request_json, null, 2)}
              </pre>
            </details>
          </Card>
        ) : null}

        {detail?.response_json != null || detail?.response_raw ? (
          <Card>
            <details open>
              <summary className="cursor-pointer text-sm font-semibold text-[var(--color-teal)]">
                Response
              </summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-black/20 p-2 text-[10px]">
                {detail.response_raw
                  ? detail.response_raw
                  : typeof detail.response_json === 'string'
                    ? detail.response_json
                    : JSON.stringify(detail.response_json, null, 2)}
              </pre>
            </details>
          </Card>
        ) : detail?.record &&
          detail.record.response != null &&
          String(detail.record.response).length > 0 ? (
          <Card>
            <details>
              <summary className="cursor-pointer text-sm font-semibold text-[var(--color-teal)]">
                Response (raw payload)
              </summary>
              <p className="mt-2 text-[11px] text-[var(--color-muted)]">
                Stored response did not parse as JSON — showing raw text from the log row.
              </p>
              <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-black/20 p-2 text-[10px]">
                {typeof detail.record.response === 'string'
                  ? detail.record.response.slice(0, 24000)
                  : JSON.stringify(detail.record.response, null, 2).slice(0, 24000)}
              </pre>
            </details>
          </Card>
        ) : null}
      </main>
    </div>
  )
}
