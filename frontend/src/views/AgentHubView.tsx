import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'
import type { AgentCatalogEntry, TraceRow, TracesListResponse } from '@/lib/api'
import { fetchAgentsCatalog, fetchTraces } from '@/lib/api'
import { NAV_PATHS } from '@/lib/navigation'

type TabId = 'directory' | 'requests'

export function AgentHubView() {
  const location = useLocation()
  const navigate = useNavigate()
  const [sp, setSp] = useSearchParams()
  const { agents, setAgents, toggleAgent } = useWorkspaceSelection()
  const [catalog, setCatalog] = useState<AgentCatalogEntry[]>([])
  const [search, setSearch] = useState('')
  const [traces, setTraces] = useState<TracesListResponse | null>(null)
  const [tab, setTab] = useState<TabId>('directory')

  useEffect(() => {
    void fetchAgentsCatalog()
      .then((c) => setCatalog(c.agents ?? []))
      .catch(() => setCatalog([]))
  }, [])

  useEffect(() => {
    const t = sp.get('tab')
    if (t === 'requests') setTab('requests')
    else setTab('directory')
  }, [sp])

  const setTabInUrl = (next: TabId) => {
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev)
        if (next === 'requests') n.set('tab', 'requests')
        else n.delete('tab')
        return n
      },
      { replace: true },
    )
    setTab(next)
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const tr = await fetchTraces(80, { agents: agents.length ? agents : null })
        if (!cancelled) setTraces(tr)
      } catch {
        if (!cancelled)
          setTraces({ traces: [], error: 'Failed to load traces' })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [agents])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return catalog
    return catalog.filter(
      (e) =>
        e.key.toLowerCase().includes(q) ||
        e.label.toLowerCase().includes(q) ||
        (e.fqn?.toLowerCase().includes(q) ?? false) ||
        (e.gateway_model?.toLowerCase().includes(q) ?? false),
    )
  }, [catalog, search])

  const openTraceDrawer = (row: TraceRow) => {
    if (!row.request_id) return
    const n = new URLSearchParams(location.search)
    n.set('task', row.request_id)
    navigate({ pathname: NAV_PATHS.quality, search: n.toString() })
  }

  const costHrefForTask = (rid: string | null) => {
    if (!rid) return { pathname: NAV_PATHS.cost, search: location.search || '' }
    const n = new URLSearchParams(location.search)
    n.set('task', rid)
    return { pathname: NAV_PATHS.cost, search: n.toString() }
  }

  const qs = location.search || ''

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border)] pb-4">
        <button
          type="button"
          onClick={() => setTabInUrl('directory')}
          className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
            tab === 'directory'
              ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
              : 'text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]'
          }`}
        >
          1 · Directory
        </button>
        <button
          type="button"
          onClick={() => setTabInUrl('requests')}
          className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
            tab === 'requests'
              ? 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
              : 'text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]'
          }`}
        >
          2 · Requests
          {agents.length ? (
            <span className="ml-1 text-[11px] opacity-80">({agents.length} scoped)</span>
          ) : null}
        </button>
        <span className="text-[11px] text-[var(--color-muted)]">
          Select models or tables below (multi-select for combined cost). Then open requests and a trace for tokens.
        </span>
      </div>

      {tab === 'directory' ? (
        <Card
          title="Agents & models"
          subtitle="Check multiple gateway models or inference tables to combine scope on Cost. Single-click row sets only that agent."
        >
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, FQN, gateway key…"
            className="mb-4 w-full max-w-md rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm"
          />
          {agents.length > 0 ? (
            <p className="mb-3 text-xs text-[var(--color-teal)]">
              Scoped:{' '}
              {agents.map((a) => (
                <span key={a} className="mr-2 font-mono text-[11px]">
                  {a}
                </span>
              ))}
              <Link
                to={{ pathname: NAV_PATHS.cost, search: qs }}
                className="ml-2 underline"
              >
                View combined cost
              </Link>
            </p>
          ) : null}
          <ul className="max-h-[min(60vh,560px)] space-y-2 overflow-y-auto">
            {filtered.length === 0 ? (
              <li className="text-sm text-[var(--color-muted)]">No matches.</li>
            ) : (
              filtered.map((e) => {
                const checked = agents.includes(e.key)
                return (
                  <li
                    key={e.key}
                    className="flex flex-wrap items-start gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]/80 p-3"
                  >
                    <label className="flex cursor-pointer items-center gap-2 pt-0.5">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleAgent(e.key)}
                        className="rounded border-[var(--color-border)]"
                        aria-label={`Include ${e.label} in scope`}
                      />
                    </label>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-[var(--color-fg)]">{e.label}</div>
                      <div className="font-mono text-[10px] text-[var(--color-muted)]">{e.key}</div>
                      <Badge tone="neutral" className="mt-1 text-[10px]">
                        {e.kind === 'inference_table' ? 'Inference table' : 'AI Gateway'}
                      </Badge>
                    </div>
                    <div className="flex shrink-0 flex-col gap-1">
                      <button
                        type="button"
                        className="rounded-md bg-[var(--color-accent)] px-2 py-1 text-[11px] font-semibold text-white hover:opacity-90"
                        onClick={() => {
                          setAgents([e.key])
                          setTabInUrl('requests')
                        }}
                      >
                        Requests →
                      </button>
                      <button
                        type="button"
                        className="rounded-md border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-fg)] hover:bg-[var(--color-surface-elevated)]"
                        onClick={() => setAgents([e.key])}
                      >
                        Scope only
                      </button>
                    </div>
                  </li>
                )
              })
            )}
          </ul>
        </Card>
      ) : (
        <Card
          title="Request list"
          subtitle="Scoped agents above (OR). Tokens come from the log row when columns exist; open Cost ▾ to pin a task for gateway-scoped totals."
        >
          {!agents.length ? (
            <p className="text-sm text-[var(--color-warn-fg)]">
              Scope at least one agent from the Directory tab, or go to Overview and pick agents — then return here.
            </p>
          ) : traces?.error ? (
            <p className="text-sm text-[var(--color-warn-fg)]">{traces.error}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-xs">
                <thead className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  <tr>
                    <th className="pb-2 pr-3">Time</th>
                    <th className="pb-2 pr-3">Request</th>
                    <th className="pb-2 pr-3">Tokens (log)</th>
                    <th className="pb-2 pr-3">Status</th>
                    <th className="pb-2 pr-3">ms</th>
                    <th className="pb-2">Detail</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-border)]">
                  {(traces?.traces ?? []).length === 0 ? (
                    <tr>
                      <td colSpan={6} className="py-8 text-center text-[var(--color-muted)]">
                        No traces for this combined scope in the last window.
                      </td>
                    </tr>
                  ) : (
                    (traces?.traces ?? []).map((t) => (
                      <tr key={t.request_id ?? t.event_time} className="hover:bg-[var(--color-surface-elevated)]/50">
                        <td className="py-2 pr-3 whitespace-nowrap text-[var(--color-muted)]">
                          {t.event_time.slice(5, 16).replace('T', ' ')}
                        </td>
                        <td className="max-w-[200px] truncate py-2 pr-3 font-mono text-[10px]">
                          {t.request_id ?? '—'}
                        </td>
                        <td className="py-2 pr-3 font-mono text-[10px] tabular-nums">
                          {t.total_tokens != null ? (
                            <span title="From inference table columns">{t.total_tokens}</span>
                          ) : (
                            <span className="text-[var(--color-muted)]">—</span>
                          )}
                          {t.input_tokens != null || t.output_tokens != null ? (
                            <span className="mt-0.5 block text-[9px] text-[var(--color-muted)]">
                              in {t.input_tokens ?? '—'} · out {t.output_tokens ?? '—'}
                            </span>
                          ) : null}
                        </td>
                        <td className="py-2 pr-3">
                          <Badge tone={t.status_code != null && t.status_code >= 400 ? 'warn' : 'teal'}>
                            {t.status_code ?? '—'}
                          </Badge>
                        </td>
                        <td className="py-2 pr-3 tabular-nums">
                          {t.latency_ms != null ? Math.round(t.latency_ms) : '—'}
                        </td>
                        <td className="py-2 pr-3">
                          <div className="flex flex-wrap gap-2">
                            <Link
                              to={costHrefForTask(t.request_id)}
                              className="text-[var(--color-accent)] hover:underline"
                              title="Cost tab with this request pinned"
                            >
                              Cost ▾
                            </Link>
                            <Link
                              to={{
                                pathname: `/trace/${encodeURIComponent(t.request_id ?? '')}`,
                                search: qs,
                              }}
                              className="text-[var(--color-accent)] hover:underline"
                            >
                              Full page
                            </Link>
                            <button
                              type="button"
                              className="text-[11px] text-[var(--color-teal)] hover:underline"
                              onClick={() => openTraceDrawer(t)}
                            >
                              Drawer (Quality)
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
