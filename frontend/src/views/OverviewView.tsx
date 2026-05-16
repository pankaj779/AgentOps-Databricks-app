import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'
import type { AgentCatalogEntry, AgentSummary, OverviewResponse } from '@/lib/api'
import {
  fetchAgents,
  fetchAgentsCatalog,
  fetchInferenceDiagnostics,
  fetchOverview,
} from '@/lib/api'
import { NAV_PATHS } from '@/lib/navigation'

function formatNumber(n: number) {
  return new Intl.NumberFormat().format(n)
}

function modeLabel(mode: string, requestsSource: string) {
  if (requestsSource === 'synthetic') return 'Synthetic demo'
  switch (mode) {
    case 'live':
      return 'Live'
    case 'live_partial':
      return 'Live (partial)'
    case 'disconnected':
      return 'Disconnected'
    default:
      return 'Local'
  }
}

export function OverviewView() {
  const { agents: scopedAgents, setAgent } = useWorkspaceSelection()
  const location = useLocation()
  const navigate = useNavigate()
  const catalogRef = useRef<HTMLDialogElement | null>(null)
  const [overview, setOverview] = useState<OverviewResponse | null>(null)
  const [dashboardAgents, setDashboardAgents] = useState<AgentSummary[]>([])
  const [catalog, setCatalog] = useState<AgentCatalogEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [diagLoading, setDiagLoading] = useState(false)
  const [diag, setDiag] = useState<Record<string, unknown> | null>(null)
  const [catalogSearch, setCatalogSearch] = useState('')

  const topAgents = useMemo(() => {
    return [...dashboardAgents].sort((a, b) => b.rpm - a.rpm).slice(0, 5)
  }, [dashboardAgents])

  const openCatalogForAgent = (a: AgentSummary) => {
    const q = (a.agent_key ?? a.name).trim()
    setCatalogSearch(q)
    catalogRef.current?.showModal()
  }

  const filteredCatalog = useMemo(() => {
    const q = catalogSearch.trim().toLowerCase()
    if (!q) return catalog
    return catalog.filter(
      (e) =>
        e.key.toLowerCase().includes(q) ||
        e.label.toLowerCase().includes(q) ||
        (e.fqn?.toLowerCase().includes(q) ?? false) ||
        (e.gateway_model?.toLowerCase().includes(q) ?? false),
    )
  }, [catalog, catalogSearch])

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [o, a, c] = await Promise.all([
        fetchOverview(),
        fetchAgents(),
        fetchAgentsCatalog().catch(() => ({ agents: [] })),
      ])
      setOverview(o)
      setDashboardAgents(a)
      setCatalog(c.agents ?? [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadData()
  }, [loadData])

  const runDiagnostics = async () => {
    setDiagLoading(true)
    try {
      setDiag(await fetchInferenceDiagnostics())
    } catch (e) {
      setDiag({ error: e instanceof Error ? e.message : 'failed' })
    } finally {
      setDiagLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="p-6">
        <div className="h-6 max-w-sm animate-pulse rounded bg-[var(--color-surface-elevated)]" />
        <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div
              key={i}
              className="h-28 animate-pulse rounded-xl bg-[var(--color-surface)]/80"
            />
          ))}
        </div>
      </div>
    )
  }

  if (error || !overview) {
    return (
      <div className="p-6">
        <Card title="Could not load data" subtitle="Is the FastAPI server running (port 8080)?">
          <p className="text-sm text-[var(--color-danger)]">{error ?? 'Unknown error'}</p>
          <p className="mt-3 text-sm text-[var(--color-muted)]">
            Start the backend from the <code className="rounded bg-black/30 px-1">backend/</code>{' '}
            folder, then refresh.
          </p>
        </Card>
      </div>
    )
  }

  const kpis: { label: string; value: string; onClick?: () => void; hint?: string }[] = [
    {
      label: 'Agents monitored',
      value: formatNumber(overview.agents_monitored),
      hint: 'Open Agent hub — search, multi-select, requests, full trace page',
      onClick: () => navigate({ pathname: NAV_PATHS.agents, search: location.search }),
    },
    {
      label: 'Requests (24h)',
      value: formatNumber(overview.requests_24h),
    },
    ...(overview.count_7d != null
      ? [
          {
            label: 'Requests (7d)',
            value: formatNumber(overview.count_7d),
          },
        ]
      : []),
    ...(overview.gateway_tokens_24h != null
      ? [
          {
            label: 'Gateway tokens (24h)',
            value: formatNumber(overview.gateway_tokens_24h),
          },
        ]
      : []),
    ...(overview.gateway_tokens_7d != null
      ? [
          {
            label: 'Gateway tokens (7d)',
            value: formatNumber(overview.gateway_tokens_7d),
          },
        ]
      : []),
    {
      label: 'Error rate',
      value: `${overview.error_rate_pct.toFixed(2)}%`,
    },
    {
      label: 'Quality (avg)',
      value:
        overview.quality_score_avg != null
          ? `${(overview.quality_score_avg * 100).toFixed(0)}%`
          : '—',
      hint:
        'Composite from latency, 24h errors, and reasoning-shaped responses — a health proxy, not human preference.',
    },
  ]

  const modeTone =
    overview.requests_24h_source === 'synthetic'
      ? 'accent'
      : overview.data_mode === 'live'
        ? 'teal'
        : overview.data_mode === 'disconnected'
          ? 'warn'
          : overview.data_mode === 'live_partial'
            ? 'accent'
            : 'neutral'

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-muted)]">
          <span className="font-medium text-[var(--color-fg)]">Environment</span>
          <code className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-xs">
            {overview.environment}
          </code>
          <Badge tone={modeTone}>
            {modeLabel(overview.data_mode, overview.requests_24h_source)}
          </Badge>
        </div>
        <button
          type="button"
          onClick={() => void loadData()}
          className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-fg)] hover:bg-[var(--color-surface-elevated)]"
        >
          Refresh data
        </button>
      </div>

      {overview.inference_setup_hint ? (
        <Card title="Inference data">
          <p className="text-sm text-[var(--color-muted)]">{overview.inference_setup_hint}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={diagLoading}
              onClick={() => void runDiagnostics()}
              className="rounded-lg bg-[var(--color-accent)] px-3 py-2 text-xs font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              {diagLoading ? 'Running…' : 'Diagnostics'}
            </button>
          </div>
          {diag ? (
            <pre className="mt-3 max-h-48 overflow-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-[10px] text-[var(--color-muted)]">
              {JSON.stringify(diag, null, 2)}
            </pre>
          ) : null}
        </Card>
      ) : null}

      <div
        className={`grid gap-4 ${kpis.length > 6 ? 'sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4' : kpis.length >= 5 ? 'sm:grid-cols-2 xl:grid-cols-5' : 'sm:grid-cols-2 xl:grid-cols-4'}`}
      >
        {kpis.map((k) => (
          <Card key={k.label} title={k.label}>
            {k.onClick ? (
              <button
                type="button"
                onClick={k.onClick}
                title={k.hint}
                className="w-full text-left transition hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/50 rounded-lg"
              >
                <div className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">
                  {k.value}
                </div>
                {k.hint ? (
                  <p className="mt-2 text-[11px] text-[var(--color-muted)]">{k.hint}</p>
                ) : null}
              </button>
            ) : (
              <>
                <div className="text-3xl font-semibold tracking-tight text-[var(--color-fg)]">{k.value}</div>
                {k.hint ? (
                  <p className="mt-2 text-[11px] text-[var(--color-muted)]">{k.hint}</p>
                ) : null}
              </>
            )}
          </Card>
        ))}
      </div>

      <dialog
        ref={catalogRef}
        className="max-h-[85vh] w-[min(100%,520px)] overflow-hidden rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)] p-0 text-[var(--color-fg)] shadow-xl backdrop:bg-black/50"
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
          <div className="text-sm font-semibold">Agents & models</div>
          <button
            type="button"
            className="rounded-lg px-2 py-1 text-xs text-[var(--color-muted)] hover:bg-[var(--color-surface)]"
            onClick={() => catalogRef.current?.close()}
          >
            Close
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto p-3 text-sm">
          <input
            type="search"
            value={catalogSearch}
            onChange={(e) => setCatalogSearch(e.target.value)}
            placeholder="Search by name, FQN, or gateway key…"
            className="mb-3 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-fg)] placeholder:text-[var(--color-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]/40"
          />
          {filteredCatalog.length === 0 ? (
            <p className="text-[var(--color-muted)]">No matches — try another query.</p>
          ) : (
            <ul className="space-y-2">
              {filteredCatalog.map((e) => (
                <li key={e.key}>
                  <button
                    type="button"
                    onClick={() => {
                      setAgent(e.key)
                      catalogRef.current?.close()
                    }}
                    className="flex w-full flex-col items-start gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-left hover:border-[var(--color-accent)]/40"
                  >
                    <span className="font-medium text-[var(--color-fg)]">{e.label}</span>
                    <span className="font-mono text-[10px] text-[var(--color-muted)]">{e.key}</span>
                    <Badge tone="neutral" className="text-[10px]">
                      {e.kind === 'inference_table' ? 'Inference table' : 'AI Gateway'}
                    </Badge>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-[11px] text-[var(--color-muted)]">
            Selecting an agent scopes Cost, Health, and Traces via the URL — use the banner to jump tabs or clear.{' '}
            <Link to={NAV_PATHS.agents} className="text-[var(--color-teal)] hover:underline">
              Full agent hub (multi-select)
            </Link>
          </p>
        </div>
      </dialog>

      <Card
        title="Top agents by traffic"
        subtitle="Highest RPM in the last 24h (approx. from /agents). Open the full directory to search all models."
      >
        <div className="mb-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              setCatalogSearch('')
              catalogRef.current?.showModal()
            }}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-fg)] hover:bg-[var(--color-surface-elevated)]"
          >
            Browse & search all agents…
          </button>
        </div>
        {topAgents.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No agent rollups yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <th className="pb-2 pr-3">Agent</th>
                  <th className="pb-2 pr-3">Source</th>
                  <th className="pb-2 pr-3">RPM</th>
                  <th className="pb-2 pr-3">p95</th>
                  <th className="pb-2">Err %</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {topAgents.map((a) => (
                  <tr
                    key={a.id}
                    className="cursor-pointer hover:bg-[var(--color-surface-elevated)]/50"
                    title="Opens the full agent directory (search) — pick an agent to scope Cost & traces"
                    onClick={() => openCatalogForAgent(a)}
                  >
                    <td className="py-2 pr-3">
                      <div className="max-w-xs truncate font-medium">{a.name}</div>
                      {a.agent_key ? (
                        <div className="font-mono text-[10px] text-[var(--color-muted)]">{a.agent_key}</div>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3">
                      <Badge tone="neutral">{a.source ?? '—'}</Badge>
                    </td>
                    <td className="py-2 pr-3 tabular-nums">{a.rpm.toFixed(1)}</td>
                    <td className="py-2 pr-3 tabular-nums">{a.p95_latency_ms.toLocaleString()} ms</td>
                    <td className="py-2 tabular-nums">{a.error_rate_pct.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Scoped drill-down" subtitle="When agents are selected, use the Agent hub or Traces to browse requests — Cost aggregates multiple models when you check several in the directory.">
        {scopedAgents.length ? (
          <p className="text-sm text-[var(--color-fg)]">
            Active scope ({scopedAgents.length}):{' '}
            <span className="font-mono text-xs text-[var(--color-accent)]">{scopedAgents.join(', ')}</span>
            <span className="mx-2 text-[var(--color-muted)]">·</span>
            <Link to={{ pathname: NAV_PATHS.agents, search: location.search }} className="text-[var(--color-teal)] hover:underline">
              Agent hub
            </Link>
            <span className="mx-2 text-[var(--color-muted)]">·</span>
            <Link to={{ pathname: NAV_PATHS.quality, search: location.search }} className="text-[var(--color-accent)] hover:underline">
              Traces
            </Link>
            <span className="mx-2 text-[var(--color-muted)]">·</span>
            <Link to={{ pathname: NAV_PATHS.cost, search: location.search }} className="text-[var(--color-accent)] hover:underline">
              Cost
            </Link>
          </p>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">
            Select agents from the KPI card,{' '}
            <Link to={NAV_PATHS.agents} className="text-[var(--color-teal)] hover:underline">
              open the Agent hub
            </Link>
            , or the Top agents table to scope Cost, Health, and Traces.
          </p>
        )}
      </Card>
    </div>
  )
}
