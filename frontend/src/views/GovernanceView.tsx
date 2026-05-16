import { useEffect, useMemo, useState } from 'react'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import type { GovernanceAuditResponse, GovernanceLineageResponse } from '@/lib/api'
import { fetchGovernanceAudit, fetchGovernanceLineage } from '@/lib/api'

export function GovernanceView() {
  const [lin, setLin] = useState<GovernanceLineageResponse | null>(null)
  const [audit, setAudit] = useState<GovernanceAuditResponse | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
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
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const { upstream, downstream } = useMemo(() => {
    const hub = new Set(
      (lin?.inference_table_fqns?.length ? lin.inference_table_fqns : lin?.inference_table ? [lin.inference_table] : []).map(
        (x) => x.toLowerCase(),
      ),
    )
    const up: { name: string; via: string | null }[] = []
    const down: { name: string; via: string | null }[] = []
    for (const e of lin?.edges ?? []) {
      const s = (e.source ?? '').toLowerCase()
      const t = (e.target ?? '').toLowerCase()
      if (t && hub.has(t) && e.source) {
        up.push({ name: e.source, via: e.entity_type })
      }
      if (s && hub.has(s) && e.target) {
        down.push({ name: e.target, via: e.entity_type })
      }
    }
    return { upstream: up, downstream: down }
  }, [lin])

  const rf = lin?.runtime_flow

  return (
    <div className="space-y-6 p-6">
      {err ? <p className="text-sm text-[var(--color-danger)]">{err}</p> : null}

      <Card title="Runtime lineage (from inference logs)">
        {!lin || !rf ? (
          <p className="text-sm text-[var(--color-muted)]">Loading…</p>
        ) : rf.error ? (
          <p className="text-sm text-[var(--color-warn-fg)]">{rf.error}</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-3 text-sm">
              <Badge tone="teal">{rf.total_requests ?? 0} requests ({rf.window_days}d)</Badge>
              {rf.distinct_callers != null ? (
                <Badge tone="neutral">{rf.distinct_callers} distinct callers</Badge>
              ) : null}
            </div>
            <div className="mt-4 grid gap-4 lg:grid-cols-2">
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  Gateway routes
                </div>
                <ul className="mt-2 space-y-2 text-xs">
                  {(rf.routes ?? []).map((r, i) => (
                    <li
                      key={`${r.url}-${i}`}
                      className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]/80 p-2"
                    >
                      <div className="font-mono text-[var(--color-fg)]">{r.api_type ?? '—'}</div>
                      <div className="mt-1 break-all text-[var(--color-muted)]">{r.url ?? '—'}</div>
                      <div className="mt-1 text-[10px] text-[var(--color-muted)]">
                        destination_id:{' '}
                        <span className="font-mono text-[var(--color-fg)]">{r.destination_id ?? '—'}</span> ·{' '}
                        {r.requests} reqs
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                  Models in response JSON
                </div>
                <ul className="mt-2 space-y-2 text-xs">
                  {(rf.models ?? []).length === 0 ? (
                    <li className="text-[var(--color-muted)]">No model field parsed (needs response JSON).</li>
                  ) : (
                    (rf.models ?? []).map((m, i) => (
                      <li
                        key={`${m.model}-${i}`}
                        className="flex items-center justify-between gap-2 rounded-lg bg-[var(--color-teal-soft)] px-2 py-1.5"
                      >
                        <span className="font-mono text-[var(--color-fg)]">{m.model ?? '(null)'}</span>
                        <Badge tone="neutral">{m.requests}</Badge>
                      </li>
                    ))
                  )}
                </ul>
              </div>
            </div>
            {rf.note ? <p className="mt-3 text-xs text-[var(--color-muted)]">{rf.note}</p> : null}
          </>
        )}
      </Card>

      <Card title="Workspace UC lineage">
        {lin?.workspace_lineage_recent_error ? (
          <p className="text-sm text-[var(--color-warn-fg)]">{lin.workspace_lineage_recent_error}</p>
        ) : null}
        {!lin ? (
          <p className="text-sm text-[var(--color-muted)]">Loading…</p>
        ) : (lin.workspace_lineage_recent ?? []).length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No rows.</p>
        ) : (
          <div className="max-h-80 overflow-auto rounded-lg border border-[var(--color-border)]">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead className="sticky top-0 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <th className="px-2 py-2">Source</th>
                  <th className="px-2 py-2">Target</th>
                  <th className="px-2 py-2">Entity</th>
                  <th className="px-2 py-2">Actor</th>
                  <th className="px-2 py-2">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)] font-mono text-[var(--color-muted)]">
                {(lin.workspace_lineage_recent ?? []).map((e, i) => (
                  <tr key={`${e.event_time}-${i}`}>
                    <td className="max-w-[200px] truncate px-2 py-2">{e.source ?? '—'}</td>
                    <td className="max-w-[200px] truncate px-2 py-2">{e.target ?? '—'}</td>
                    <td className="px-2 py-2">{e.entity_type ?? '—'}</td>
                    <td className="max-w-[120px] truncate px-2 py-2">{e.created_by ?? '—'}</td>
                    <td className="whitespace-nowrap px-2 py-2">{e.event_time.slice(0, 19)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Unity Catalog table lineage">
        {lin?.error ? (
          <p className="mt-2 text-sm text-[var(--color-warn-fg)]">{lin.error}</p>
        ) : null}
        {lin?.uc_lineage_query_error ? (
          <p className="mt-2 rounded-lg border border-[var(--color-warn-border)] bg-[var(--color-warn-bg)] px-3 py-2 text-sm text-[var(--color-warn-fg)]">
            Could not query <code className="text-xs">system.access.table_lineage</code>: {lin.uc_lineage_query_error}
          </p>
        ) : null}
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 p-4">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Upstream (UC)
            </div>
            <ul className="mt-2 space-y-2 text-xs">
              {upstream.length === 0 ? (
                <li className="text-[var(--color-muted)]">
                  No UC edges — use runtime lineage above, or enable system tables + run workloads that touch this table.
                </li>
              ) : (
                upstream.map((u) => (
                  <li key={u.name} className="rounded-lg bg-[var(--color-teal-soft)] px-2 py-1.5">
                    <div className="font-mono text-[var(--color-fg)]">{u.name}</div>
                    {u.via ? (
                      <div className="text-[10px] text-[var(--color-muted)]">via {u.via}</div>
                    ) : null}
                  </li>
                ))
              )}
            </ul>
          </div>
          <div className="flex flex-col items-center justify-center rounded-2xl border-2 border-dashed border-[var(--color-accent)]/40 bg-[var(--color-accent-soft)]/20 p-4 text-center">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Inference log
            </div>
            <div className="mt-2 font-mono text-xs font-medium text-[var(--color-fg)]">
              {lin?.inference_table_display ?? lin?.inference_table ?? '—'}
            </div>
            <Badge tone="neutral" className="mt-2">
              AgentOps telemetry hub
            </Badge>
          </div>
          <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 p-4">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Downstream (UC)
            </div>
            <ul className="mt-2 space-y-2 text-xs">
              {downstream.length === 0 ? (
                <li className="text-[var(--color-muted)]">No downstream consumers in UC lineage yet.</li>
              ) : (
                downstream.map((d) => (
                  <li key={d.name} className="rounded-lg bg-[var(--color-surface-elevated)] px-2 py-1.5">
                    <div className="font-mono text-[var(--color-fg)]">{d.name}</div>
                    {d.via ? (
                      <div className="text-[10px] text-[var(--color-muted)]">via {d.via}</div>
                    ) : null}
                  </li>
                ))
              )}
            </ul>
          </div>
        </div>

        {(lin?.edges?.length ?? 0) > 0 ? (
          <div className="mt-6 overflow-x-auto">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Raw lineage events
            </div>
            <table className="mt-2 w-full min-w-[720px] text-left text-xs">
              <thead className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <th className="pb-2 pr-2">Source</th>
                  <th className="pb-2 pr-2">Target</th>
                  <th className="pb-2 pr-2">Entity</th>
                  <th className="pb-2 pr-2">Actor</th>
                  <th className="pb-2">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)] font-mono text-[var(--color-muted)]">
                {(lin?.edges ?? []).slice(0, 40).map((e, i) => (
                  <tr key={`${e.event_time}-${i}`}>
                    <td className="py-2 pr-2">{e.source ?? '—'}</td>
                    <td className="py-2 pr-2">{e.target ?? '—'}</td>
                    <td className="py-2 pr-2">{e.entity_type ?? '—'}</td>
                    <td className="py-2 pr-2">{e.created_by ?? '—'}</td>
                    <td className="py-2 whitespace-nowrap">{e.event_time.slice(0, 19)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Accessed assets (UC)">
          {(lin?.nodes?.length ?? 0) === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">
              No nodes yet — runtime lineage above reflects your gateway traffic.
            </p>
          ) : (
            <ul className="space-y-2 text-xs">
              {(lin?.nodes ?? []).map((n) => (
                <li
                  key={n.id}
                  className="flex items-center justify-between gap-2 rounded-lg border border-[var(--color-border)] px-2 py-2"
                >
                  <span className="font-mono text-[var(--color-fg)]">{n.fqn}</span>
                  <Badge tone="neutral">{n.label}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Inference audit feed">
          {audit?.error ? (
            <p className="text-sm text-[var(--color-warn-fg)]">{audit.error}</p>
          ) : (
            <div className="max-h-72 overflow-auto rounded-lg border border-[var(--color-border)]">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
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
      </div>
    </div>
  )
}
