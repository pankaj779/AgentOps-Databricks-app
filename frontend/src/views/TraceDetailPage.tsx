import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { RequestLineageGraph } from '@/components/RequestLineageGraph'
import { Card } from '@/components/ui/Card'
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

  const sortedBench = useMemo(() => {
    const rows = benchResult?.results ?? []
    return [...rows].sort((a, b) => {
      const ta = Number(a.usage?.total_tokens ?? 1e9)
      const tb = Number(b.usage?.total_tokens ?? 1e9)
      return ta - tb
    })
  }, [benchResult])

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
        </div>
      </header>
      <main className="mx-auto max-w-5xl space-y-6 p-4 pb-16">
        {loading ? <p className="text-sm text-[var(--color-muted)]">Loading trace…</p> : null}
        {detail?.error ? <p className="text-[var(--color-danger)]">{detail.error}</p> : null}

        {!loading && detail?.comparison_group_id ? (
          <Card title="Cross-model comparison (this task group)" subtitle="Production rows sharing comparison_group_id">
            <div className="font-mono text-[11px] text-[var(--color-muted)]">{detail.comparison_group_id}</div>
            {compLoading ? <p className="mt-2 text-xs text-[var(--color-muted)]">Loading…</p> : null}
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
            title="Replay logged JSON"
            subtitle="Same payload to each target in AGENTOPS_REPLAY_TARGETS_JSON"
          >
            <button
              type="button"
              disabled={replayBusy}
              className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm disabled:opacity-50"
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
              <p className="mt-2 text-sm text-[var(--color-warn-fg)]">{replayResult.error}</p>
            ) : null}
            {(replayResult?.results?.length ?? 0) > 0 ? (
              <ul className="mt-3 space-y-2 text-xs">
                {replayResult!.results!.map((rr) => (
                  <li key={rr.target_id} className="rounded-lg border border-[var(--color-border)] px-2 py-2">
                    <strong>{rr.label}</strong> — HTTP {rr.status_code ?? '—'} — {Math.round(rr.latency_ms)} ms
                    {rr.usage?.total_tokens != null ? (
                      <span className="ml-2 font-mono">tokens {String(rr.usage.total_tokens)}</span>
                    ) : null}
                    {rr.error ? <div className="text-[var(--color-danger)]">{rr.error}</div> : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </Card>
        ) : null}

        <Card
          title="Live prompt benchmark"
          subtitle="Same question to every replay target — enable AGENTOPS_BENCHMARK_ENABLED=true on the server"
        >
          {replayTargets?.diagnostics ? (
            <div className="mb-3 rounded-lg border border-[var(--color-warn-border)] bg-[var(--color-warn-bg)] px-3 py-2 text-[11px] text-[var(--color-warn-fg)]">
              <strong className="block text-[var(--color-fg)]">Replay targets not loaded</strong>
              <span className="mt-1 block">{replayTargets.diagnostics.hint}</span>
              {replayTargets.diagnostics.parse_error ? (
                <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap font-mono text-[10px] opacity-90">
                  {replayTargets.diagnostics.parse_error}
                </pre>
              ) : null}
              <p className="mt-2 text-[10px] text-[var(--color-muted)]">
                Source: {replayTargets.diagnostics.configured_from ?? '—'} (raw {replayTargets.diagnostics.raw_length ?? 0}{' '}
                chars). Tip: set <code className="text-[var(--color-fg)]">AGENTOPS_REPLAY_TARGETS_FILE</code> to a json
                file under <code className="text-[var(--color-fg)]">backend/</code> (see{' '}
                <code className="text-[var(--color-fg)]">replay_targets.example.json</code>).
              </p>
            </div>
          ) : null}
          <p className="mb-2 text-xs text-[var(--color-muted)]">
            Uses OpenAI-style chat JSON. Best for localhost demos with real gateway URLs in replay targets.
          </p>
          <details className="mb-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/40 px-3 py-2 text-[11px] text-[var(--color-muted)]">
            <summary className="cursor-pointer font-medium text-[var(--color-teal)]">
              Enable benchmark (backend .env)
            </summary>
            <ol className="mt-2 list-inside list-decimal space-y-1.5">
              <li>
                Add replay targets (OpenAI-compatible URLs):{' '}
                <code className="text-[10px] text-[var(--color-fg)]">AGENTOPS_REPLAY_TARGETS_JSON</code>
              </li>
              <li>
                Turn on the API: <code className="text-[10px] text-[var(--color-fg)]">AGENTOPS_BENCHMARK_ENABLED=true</code>
              </li>
              <li>Restart the FastAPI process and reload this page.</li>
            </ol>
            <p className="mt-2 text-[10px]">
              Targets receive the same JSON body as replay (model + messages + max_tokens). Use only trusted URLs.
            </p>
          </details>
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
          {sortedBench.length > 0 ? (
            <div className="mt-4 overflow-x-auto rounded-lg border border-[var(--color-border)]">
              <table className="w-full text-left text-xs">
                <thead className="bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase text-[var(--color-muted)]">
                  <tr>
                    <th className="px-2 py-2">Target</th>
                    <th className="px-2 py-2">HTTP</th>
                    <th className="px-2 py-2">ms</th>
                    <th className="px-2 py-2">Total tokens</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-border)]">
                  {sortedBench.map((r) => (
                    <tr key={r.target_id}>
                      <td className="px-2 py-2 font-medium">{r.label}</td>
                      <td className="px-2 py-2">{r.status_code ?? '—'}</td>
                      <td className="px-2 py-2 tabular-nums">{Math.round(r.latency_ms)}</td>
                      <td className="px-2 py-2 font-mono tabular-nums">
                        {r.usage?.total_tokens != null ? String(r.usage.total_tokens) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="border-t border-[var(--color-border)] p-2 text-[10px] text-[var(--color-muted)]">
                Sorted by total tokens (lowest first). Verify output quality separately — token count alone is not
                sufficiency.
              </p>
            </div>
          ) : null}
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

        {detail?.response_json != null ? (
          <Card>
            <details>
              <summary className="cursor-pointer text-sm font-semibold text-[var(--color-teal)]">
                Response JSON
              </summary>
              <pre className="mt-2 max-h-96 overflow-auto rounded-lg bg-black/20 p-2 text-[10px]">
                {JSON.stringify(detail.response_json, null, 2)}
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
