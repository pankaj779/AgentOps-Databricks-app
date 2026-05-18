import { useEffect, useMemo, useState } from 'react'
import { Card } from '@/components/ui/Card'
import { DataLoadingState } from '@/components/ui/DataLoadingState'
import { Badge } from '@/components/ui/Badge'
import { TelemetryHierarchyGraph } from '@/components/TelemetryHierarchyGraph'
import type { GovernanceAuditResponse, GovernanceLineageResponse } from '@/lib/api'
import { fetchGovernanceAudit, fetchGovernanceLineage } from '@/lib/api'
import { resolveTelemetryHierarchy } from '@/lib/telemetryHierarchy'

export function GovernanceView() {
  const [lin, setLin] = useState<GovernanceLineageResponse | null>(null)
  const [audit, setAudit] = useState<GovernanceAuditResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

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
  }, [])

  const rf = lin?.runtime_flow
  const hasUc = lin?.has_uc_lineage ?? false

  const hierarchy = useMemo(() => (lin ? resolveTelemetryHierarchy(lin) : null), [lin])

  const payloadTables = useMemo(() => {
    const fromGraph = (hierarchy?.nodes ?? []).filter((n) => n.kind === 'table')
    if (fromGraph.length) return fromGraph
    return (lin?.inference_table_fqns ?? []).map((fqn) => ({
      id: fqn,
      kind: 'table',
      label: fqn.split('.').pop() ?? fqn,
      detail: fqn,
      meta: null,
    }))
  }, [lin, hierarchy])

  return (
    <div className="space-y-6 p-6">
      {err ? <p className="text-sm text-[var(--color-danger)]">{err}</p> : null}

      <DataLoadingState loading={loading} label="Loading governance data…">
      <Card title="Lineage">
        {lin ? (
          <div className="flex flex-col gap-6">
            <TelemetryHierarchyGraph graph={hierarchy} />
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
                {(audit?.events ?? []).map((ev) => (
                  <tr key={ev.request_id ?? ev.event_time}>
                    <td className="px-2 py-2 whitespace-nowrap text-[var(--color-muted)]">
                      {ev.event_time.slice(5, 19).replace('T', ' ')}
                    </td>
                    <td className="max-w-[160px] truncate px-2 py-2 text-[var(--color-fg)]">
                      {ev.requester ?? '—'}
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
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      </DataLoadingState>
    </div>
  )
}
