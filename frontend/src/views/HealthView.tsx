import { useEffect, useState } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'
import type { AgentSummary, CostSummaryResponse, HealthSloResponse, HealthTimeseriesResponse } from '@/lib/api'
import { fetchAgents, fetchCostSummary, fetchHealthSlo, fetchHealthTimeseries } from '@/lib/api'

function shortBucket(iso: string) {
  try {
    const d = new Date(iso)
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2, '0')}:00`
  } catch {
    return iso.slice(5, 16)
  }
}

const axisProps = {
  stroke: 'var(--color-muted)',
  tick: { fill: 'var(--color-muted)', fontSize: 10 },
}

export function HealthView() {
  const { agents: scopeAgents } = useWorkspaceSelection()
  const [agentList, setAgentList] = useState<AgentSummary[]>([])
  const [ts, setTs] = useState<HealthTimeseriesResponse | null>(null)
  const [slo, setSlo] = useState<HealthSloResponse | null>(null)
  const [cost, setCost] = useState<CostSummaryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [a, t, s, c] = await Promise.all([
          fetchAgents(),
          fetchHealthTimeseries(168, { agents: scopeAgents.length ? scopeAgents : null }),
          fetchHealthSlo(2000, 1, { agents: scopeAgents.length ? scopeAgents : null }),
          fetchCostSummary(168, { agents: scopeAgents.length ? scopeAgents : null }),
        ])
        if (!cancelled) {
          setAgentList(a)
          setTs(t)
          setSlo(s)
          setCost(c)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [scopeAgents.join('\0')])

  const tsErr = ts?.error
  const sloErr = slo?.error
  const chartData =
    ts?.buckets.map((b) => ({
      label: shortBucket(b.bucket),
      requests: b.requests,
      errors: b.errors,
    })) ?? []

  return (
    <div className="space-y-6 p-6">
      {error ? (
        <p className="text-sm text-[var(--color-danger)]">{error}</p>
      ) : null}
      {(tsErr || sloErr) && !error ? (
        <p className="text-sm text-[var(--color-warn-fg)]">
          {tsErr || sloErr}
        </p>
      ) : null}

      {cost?.billing && !cost.billing.error && cost.billing.total_list_usd != null ? (
        <Card title="Model serving spend (list price)">
          <div className="text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
            {cost.billing.currency_code} {cost.billing.total_list_usd.toFixed(4)}
          </div>
          <div className="mt-1 text-xs text-[var(--color-muted)]">
            {cost.billing.total_dbu != null ? `${cost.billing.total_dbu.toFixed(6)} DBU` : ''} · same window as Cost tab
          </div>
        </Card>
      ) : null}

      {cost?.ai_gateway && !cost.ai_gateway.error ? (
        <Card title="AI Gateway tokens (7d)">
          <div className="flex flex-wrap gap-6 text-sm">
            <div>
              <div className="text-[11px] font-semibold uppercase text-[var(--color-muted)]">Total tokens</div>
              <div className="text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
                {(cost.ai_gateway.total_tokens ?? 0).toLocaleString()}
              </div>
            </div>
            <div>
              <div className="text-[11px] font-semibold uppercase text-[var(--color-muted)]">Requests</div>
              <div className="text-2xl font-semibold tabular-nums text-[var(--color-muted)]">
                {(cost.ai_gateway.total_requests ?? 0).toLocaleString()}
              </div>
            </div>
          </div>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="SLO monitor">
          {slo ? (
            <ul className="space-y-3 text-sm text-[var(--color-muted)]">
              <li className="flex items-center justify-between gap-3">
                <span>Global p95 latency</span>
                <span className="tabular-nums font-medium text-[var(--color-fg)]">
                  {slo.global_p95_ms != null ? `${Math.round(slo.global_p95_ms).toLocaleString()} ms` : '—'}
                </span>
              </li>
              <li className="flex items-center justify-between gap-3">
                <span>Global error rate</span>
                <span className="tabular-nums font-medium text-[var(--color-fg)]">
                  {slo.global_error_rate_pct != null ? `${slo.global_error_rate_pct.toFixed(2)}%` : '—'}
                </span>
              </li>
              <li className="flex items-center justify-between gap-3">
                <span>Segments breaching p95</span>
                <Badge tone={slo.agents_breaching_p95 > 0 ? 'warn' : 'teal'}>
                  {slo.agents_breaching_p95.toString()}
                </Badge>
              </li>
              <li className="flex items-center justify-between gap-3">
                <span>Segments over error budget</span>
                <Badge tone={slo.agents_over_error_budget > 0 ? 'warn' : 'teal'}>
                  {slo.agents_over_error_budget.toString()}
                </Badge>
              </li>
            </ul>
          ) : (
            <p className="text-sm text-[var(--color-muted)]">Loading…</p>
          )}
        </Card>

        <Card title="Request volume" className="lg:col-span-2">
          {chartData.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">
              No time buckets yet — generate traffic and refresh.
            </p>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.6} />
                  <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={24} />
                  <YAxis yAxisId="l" {...axisProps} allowDecimals={false} width={36} />
                  <YAxis yAxisId="r" orientation="right" {...axisProps} allowDecimals={false} width={36} />
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
                  <Area
                    yAxisId="l"
                    type="monotone"
                    dataKey="requests"
                    name="Requests"
                    stroke="var(--color-teal)"
                    fill="var(--color-teal-soft)"
                    strokeWidth={2}
                  />
                  <Line
                    yAxisId="r"
                    type="monotone"
                    dataKey="errors"
                    name="Errors"
                    stroke="var(--color-accent)"
                    strokeWidth={2}
                    dot={{ r: 2 }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      <Card title="Agent drill-down">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              <tr>
                <th className="pb-3 pr-4">Agent</th>
                <th className="pb-3 pr-4">Status</th>
                <th className="pb-3 pr-4">RPM</th>
                <th className="pb-3 pr-4">p95</th>
                <th className="pb-3">Errors</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {agentList.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-[var(--color-muted)]">
                    No agent rows — check Overview wiring.
                  </td>
                </tr>
              ) : (
                agentList.map((a) => (
                  <tr key={a.id}>
                    <td className="py-3 pr-4 font-medium text-[var(--color-fg)]">{a.name}</td>
                    <td className="py-3 pr-4">
                      <Badge tone={a.status === 'healthy' ? 'teal' : 'warn'}>{a.status}</Badge>
                    </td>
                    <td className="py-3 pr-4 tabular-nums text-[var(--color-muted)]">
                      {a.rpm < 0.01 ? a.rpm.toFixed(4) : a.rpm.toFixed(2)}
                    </td>
                    <td className="py-3 pr-4 tabular-nums text-[var(--color-muted)]">
                      {a.p95_latency_ms.toLocaleString()} ms
                    </td>
                    <td className="py-3 tabular-nums text-[var(--color-muted)]">
                      {a.error_rate_pct.toFixed(2)}%
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
