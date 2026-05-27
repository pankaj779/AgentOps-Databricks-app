import { useEffect, useMemo, useState } from 'react'
import { Card } from '@/components/ui/Card'
import { DataLoadingState } from '@/components/ui/DataLoadingState'
import { Badge } from '@/components/ui/Badge'
import { TelemetryHierarchyGraph } from '@/components/TelemetryHierarchyGraph'
import { callerDisplay } from '@/lib/callerDisplay'
import type { GovernanceAuditResponse, GovernanceLineageResponse } from '@/lib/api'
import { fetchGovernanceAudit, fetchGovernanceLineage } from '@/lib/api'
import { resolveTelemetryHierarchy } from '@/lib/telemetryHierarchy'

type LineageTab = 'data' | 'cost'

export function GovernanceView({ refreshToken = 0 }: { refreshToken?: number }) {
  const [lin, setLin] = useState<GovernanceLineageResponse | null>(null)
  const [audit, setAudit] = useState<GovernanceAuditResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [lineageTab, setLineageTab] = useState<LineageTab>('data')
  const [selectedCaller, setSelectedCaller] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    ;(async () => {
      try {
        const [l, a] = await Promise.all([fetchGovernanceLineage(100), fetchGovernanceAudit(50)])
        if (!cancelled) {
          setLin(l)
          setAudit(a)
          setErr(null)
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : 'Failed to load')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refreshToken])

  const rf = lin?.runtime_flow
  const hasUc = lin?.has_uc_lineage ?? false

  const hierarchy = useMemo(() => (lin ? resolveTelemetryHierarchy(lin) : null), [lin])
  const costHierarchy = useMemo(() => {
    const g = lin?.cost_tokens_hierarchy
    if (g?.nodes?.length) return g
    return null
  }, [lin])

  const payloadTables = useMemo(() => {
    const fromGraph = (hierarchy?.nodes ?? []).filter((n) => n.kind === 'table')
    if (fromGraph.length) return fromGraph
    return (lin?.inference_table_fqns ?? []).map((fqn) => ({
      id: fqn,
      kind: 'table',
      label: fqn.split('.').pop() ?? fqn,
      detail: fqn,
      meta: null,
      usage: null,
    }))
  }, [lin, hierarchy])

  const callers = rf?.callers ?? []
  const filteredAudit = useMemo(() => {
    const events = audit?.events ?? []
    if (!selectedCaller) return events
    return events.filter((ev) => (ev.requester ?? '').trim() === selectedCaller)
  }, [audit, selectedCaller])

  return (
    <div className="space-y-6 p-6">
      {err ? <p className="text-sm text-[var(--color-danger)]">{err}</p> : null}

      <DataLoadingState loading={loading} label="Loading governance data…">
      {lin?.pii_scan && lin.pii_scan.matches > 0 ? (
        <div className="rounded-lg border border-[var(--color-warn-border)] bg-[var(--color-warn-bg)] px-4 py-3 text-sm text-[var(--color-warn-fg)]">
          <span className="font-semibold">PII policy check: </span>
          {lin.pii_scan.matches} request(s) in the last {lin.pii_scan.window_days ?? 7} days may contain
          email/phone/SSN-like patterns (regex scan, not full DLP).
        </div>
      ) : lin?.pii_scan && !lin.pii_scan.error ? (
        <div className="rounded-lg border border-[var(--color-teal)]/30 bg-[var(--color-teal)]/8 px-4 py-2 text-xs text-[var(--color-teal)]">
          No obvious PII patterns in recent request bodies ({lin.pii_scan.scanned_rows} rows scanned).
        </div>
      ) : null}
      <Card title="Lineage">
        {lin ? (
          <div className="flex flex-col gap-6">
            <div className="flex flex-wrap gap-2 border-b border-[var(--color-border)] pb-3">
              <button
                type="button"
                onClick={() => setLineageTab('data')}
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${
                  lineageTab === 'data'
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]'
                }`}
              >
                Data flow
              </button>
              <button
                type="button"
                onClick={() => setLineageTab('cost')}
                className={`rounded-lg px-3 py-1.5 text-xs font-semibold ${
                  lineageTab === 'cost'
                    ? 'bg-[var(--color-accent)] text-white'
                    : 'border border-[var(--color-border)] text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]'
                }`}
              >
                Cost & tokens
              </button>
            </div>

            {lineageTab === 'data' ? (
              <TelemetryHierarchyGraph graph={hierarchy} variant="data" />
            ) : costHierarchy ? (
              <TelemetryHierarchyGraph graph={costHierarchy} variant="cost_tokens" />
            ) : (
              <p className="text-sm text-[var(--color-muted)]">Cost & tokens lineage not available yet.</p>
            )}

            <div className="grid gap-4 text-xs md:grid-cols-3">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  Payload tables
                </div>
                <ul className="mt-2 max-h-48 space-y-1.5 overflow-y-auto">
                  {payloadTables.map((t) => (
                    <li
                      key={t.id}
                      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-2 py-1.5 font-mono text-[var(--color-fg)]"
                    >
                      {t.label}
                      {t.meta ? (
                        <span className="ml-2 text-[10px] text-[var(--color-muted)]">{t.meta}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
              {rf && !rf.error ? (
                <>
                  <div>
                    <div className="flex flex-wrap gap-2">
                      <Badge tone="teal">{rf.total_requests ?? 0} requests ({rf.window_days}d)</Badge>
                      {rf.distinct_callers != null ? (
                        <Badge tone="neutral">{rf.distinct_callers} callers</Badge>
                      ) : null}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                      Models (from logs)
                    </div>
                    <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                      {(rf.models ?? []).slice(0, 12).map((m, i) => (
                        <li
                          key={`${m.model}-${i}`}
                          className="flex justify-between gap-2 rounded bg-[var(--color-teal-soft)] px-2 py-1"
                        >
                          <span className="truncate font-mono text-[var(--color-fg)]">{m.model ?? '—'}</span>
                          <span className="tabular-nums text-[var(--color-muted)]">{m.requests}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                      Gateway endpoints
                    </div>
                    <ul className="mt-2 max-h-36 space-y-1 overflow-y-auto font-mono text-[10px] text-[var(--color-muted)]">
                      {(rf.routes ?? []).slice(0, 8).map((r, i) => (
                        <li key={`${r.url}-${i}`} className="truncate">
                          {r.destination_id ?? r.api_type ?? 'route'} · {r.requests} reqs
                        </li>
                      ))}
                    </ul>
                  </div>
                </>
              ) : rf?.error ? (
                <p className="text-[var(--color-warn-fg)]">{rf.error}</p>
              ) : null}
            </div>
          </div>
        ) : (
          <p className="text-sm text-[var(--color-muted)]">No lineage data.</p>
        )}
      </Card>

      <Card
        title="Users & callers"
        subtitle="From the requester column in payload / gateway logs. Values are often OAuth or service-principal ids (UUID-shaped), not display names."
      >
        {callers.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            No requester identity in logs yet. When Databricks logs who called the gateway, they will appear here.
          </p>
        ) : (
          <div className="flex flex-col gap-4 lg:flex-row">
            <ul className="flex max-h-56 flex-wrap gap-2 lg:max-w-md lg:flex-col lg:overflow-y-auto">
              <li>
                <button
                  type="button"
                  onClick={() => setSelectedCaller(null)}
                  className={`w-full rounded-lg border px-3 py-2 text-left text-xs ${
                    selectedCaller === null
                      ? 'border-[var(--color-teal)] bg-[var(--color-teal)]/15 text-[var(--color-fg)]'
                      : 'border-[var(--color-border)] text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]'
                  }`}
                >
                  All callers ({callers.reduce((n, c) => n + c.requests, 0)} reqs)
                </button>
              </li>
              {callers.map((c) => {
                const cd = callerDisplay(c.requester)
                return (
                <li key={c.requester}>
                  <button
                    type="button"
                    onClick={() => setSelectedCaller(c.requester)}
                    className={`w-full rounded-lg border px-3 py-2 text-left text-xs ${
                      selectedCaller === c.requester
                        ? 'border-[var(--color-teal)] bg-[var(--color-teal)]/15 font-medium text-[var(--color-fg)]'
                        : 'border-[var(--color-border)] text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]'
                    }`}
                  >
                    <span
                      className="block truncate font-mono"
                      title={cd.full ?? cd.primary}
                    >
                      {cd.primary}
                    </span>
                    <span className="mt-0.5 text-[10px] tabular-nums text-[var(--color-teal)]">
                      {c.requests} request{c.requests === 1 ? '' : 's'}
                      {c.last_seen ? ` · last ${c.last_seen.slice(0, 16).replace('T', ' ')}` : ''}
                    </span>
                  </button>
                </li>
                )
              })}
            </ul>
            <p className="flex-1 text-[11px] text-[var(--color-muted)]">
              Click a caller to filter the audit table below. Any workspace user or service principal that appears in
              inference logs will show up — no hardcoded list.
            </p>
          </div>
        )}
      </Card>

      {hasUc ? (
        <Card title="Unity Catalog lineage">
          {lin?.uc_lineage_query_error ? (
            <p className="text-sm text-[var(--color-warn-fg)]">{lin.uc_lineage_query_error}</p>
          ) : null}
          <div className="max-h-64 overflow-auto rounded-lg border border-[var(--color-border)]">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead className="sticky top-0 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase text-[var(--color-muted)]">
                <tr>
                  <th className="px-2 py-2">Source</th>
                  <th className="px-2 py-2">Target</th>
                  <th className="px-2 py-2">Entity</th>
                  <th className="px-2 py-2">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)] font-mono text-[var(--color-muted)]">
                {(lin?.edges ?? []).slice(0, 30).map((e, i) => (
                  <tr key={`${e.event_time}-${i}`}>
                    <td className="max-w-[220px] truncate px-2 py-2">{e.source ?? '—'}</td>
                    <td className="max-w-[220px] truncate px-2 py-2">{e.target ?? '—'}</td>
                    <td className="px-2 py-2">{e.entity_type ?? '—'}</td>
                    <td className="whitespace-nowrap px-2 py-2">{e.event_time.slice(0, 19)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <Card title="Inference audit">
        {selectedCaller ? (
          <p className="mb-2 text-[11px] text-[var(--color-teal)]">
            Filtered to <span className="font-mono">{selectedCaller}</span>
            <button
              type="button"
              className="ml-2 underline"
              onClick={() => setSelectedCaller(null)}
            >
              Clear
            </button>
          </p>
        ) : null}
        {audit?.error ? (
          <p className="text-sm text-[var(--color-warn-fg)]">{audit.error}</p>
        ) : (
          <div className="max-h-72 overflow-auto rounded-lg border border-[var(--color-border)]">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase text-[var(--color-muted)]">
                <tr>
                  <th className="px-2 py-2">Time</th>
                  <th className="px-2 py-2">Requester</th>
                  <th className="px-2 py-2">Code</th>
                  <th className="px-2 py-2">ms</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {filteredAudit.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-2 py-6 text-center text-[var(--color-muted)]">
                      No events for this filter.
                    </td>
                  </tr>
                ) : (
                  filteredAudit.map((ev) => (
                    <tr key={ev.request_id ?? ev.event_time}>
                      <td className="px-2 py-2 whitespace-nowrap text-[var(--color-muted)]">
                        {ev.event_time.slice(5, 19).replace('T', ' ')}
                      </td>
                      <td className="max-w-[160px] truncate px-2 py-2 font-mono text-[var(--color-fg)]">
                        {(() => {
                          const cd = callerDisplay(ev.requester)
                          return (
                            <span title={cd.full ?? cd.primary} className="cursor-default">
                              {cd.primary}
                            </span>
                          )
                        })()}
                      </td>
                      <td className="px-2 py-2">
                        <Badge tone={ev.status_code != null && ev.status_code >= 400 ? 'warn' : 'teal'}>
                          {ev.status_code ?? '—'}
                        </Badge>
                      </td>
                      <td className="px-2 py-2 tabular-nums text-[var(--color-muted)]">
                        {ev.latency_ms != null ? Math.round(ev.latency_ms) : '—'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      </DataLoadingState>
    </div>
  )
}
