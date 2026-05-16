import { useEffect, useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card } from '@/components/ui/Card'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'
import type { CostSummaryResponse } from '@/lib/api'
import { fetchCostSummary } from '@/lib/api'

const axisProps = {
  stroke: 'var(--color-muted)',
  tick: { fill: 'var(--color-muted)', fontSize: 10 },
}

function shortBucket(iso: string) {
  try {
    const d = new Date(iso)
    return `${d.getUTCMonth() + 1}/${d.getUTCDate()} ${String(d.getUTCHours()).padStart(2, '0')}h`
  } catch {
    return iso.slice(5, 13)
  }
}

export function CostView() {
  const { agents, clearAll, task } = useWorkspaceSelection()
  const [data, setData] = useState<CostSummaryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const c = await fetchCostSummary(168, {
          agents: agents.length ? agents : null,
          task: task?.trim() || null,
        })
        if (!cancelled) {
          setData(c)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [agents, task])

  const hourlyChart = useMemo(
    () =>
      (data?.hourly ?? []).map((h) => ({
        label: shortBucket(h.bucket),
        est_tokens: Math.round(h.est_tokens),
      })),
    [data],
  )

  const barData = useMemo(
    () =>
      (data?.by_destination ?? []).map((r) => ({
        name:
          r.destination.length > 20 ? `${r.destination.slice(0, 10)}…${r.destination.slice(-6)}` : r.destination,
        full: r.destination,
        est_tokens: Math.round(r.est_tokens),
        requests: r.requests,
      })),
    [data],
  )

  const total = data?.total_est_tokens
  const bill = data?.billing
  const gw = data?.ai_gateway

  const costLooksEmpty = useMemo(() => {
    if (!data) return false
    const gwHas =
      gw &&
      !gw.error &&
      ((gw.total_tokens != null && gw.total_tokens > 0) ||
        (gw.total_requests != null && gw.total_requests > 0) ||
        (gw.by_model?.length ?? 0) > 0)
    const billHas =
      bill && !bill.error && bill.total_list_usd != null && bill.total_list_usd > 0
    const rollups = (data.by_destination ?? []).length > 0
    const hourly = (data.hourly ?? []).length > 0
    return !gwHas && !billHas && !rollups && !hourly
  }, [data, gw, bill])

  return (
    <div className="space-y-6 p-6">
      {error ? <p className="text-sm text-[var(--color-danger)]">{error}</p> : null}
      {data?.error ? <p className="text-sm text-[var(--color-warn-fg)]">{data.error}</p> : null}

      <Card title="Cost & tokens">
        {task?.trim() ? (
          <p className="mb-3 text-xs text-[var(--color-teal)]">
            Pinned request <span className="font-mono">{task.trim()}</span> — gateway tokens and payload charts match this
            request when IDs align. List price / DBU below are still workspace-wide.
          </p>
        ) : null}
        {agents.length ? (
          <p className="mb-3 text-xs text-[var(--color-accent)]">
            Filtered view — {agents.length === 1 ? (
              <>metrics for <span className="font-mono">{agents[0]}</span></>
            ) : (
              <>
                combined metrics for <span className="font-mono">{agents.length}</span> scoped agents (OR).
              </>
            )}{' '}
            Clear from the banner above for combined workspace totals.
          </p>
        ) : null}
        {data && costLooksEmpty ? (
          <div className="mb-4 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/60 px-4 py-3 text-xs text-[var(--color-muted)]">
            <p className="font-medium text-[var(--color-fg)]">No cost or token rollups in this window</p>
            <ul className="mt-2 list-inside list-disc space-y-1.5">
              <li>
                These figures come from <strong className="text-[var(--color-fg)]">AI Gateway system usage</strong> and{' '}
                <strong className="text-[var(--color-fg)]">inference payload tables</strong>. If your calls never hit the
                gateway or are not logged to inference tables, charts stay empty even when Overview shows traffic.
              </li>
              {agents.length ? (
                <li>
                  Scope is limited to{' '}
                  {agents.length === 1 ? (
                    <span className="font-mono text-[var(--color-fg)]">{agents[0]}</span>
                  ) : (
                    <span className="font-mono text-[var(--color-fg)]">
                      {agents.length} agents (combined)
                    </span>
                  )}
                  . If these are gateway models, confirm catalog keys match usage rows.{' '}
                  <button
                    type="button"
                    className="text-[var(--color-accent)] underline hover:no-underline"
                    onClick={() => clearAll()}
                  >
                    Clear scope
                  </button>{' '}
                  to see workspace-wide totals.
                </li>
              ) : (
                <li>
                  Use <strong className="text-[var(--color-fg)]">Agents</strong> in the sidebar or Overview to scope
                  models or inference tables.
                </li>
              )}
            </ul>
          </div>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              List price (est.)
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-muted)]">Workspace · not narrowed by agent</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
              {bill && !bill.error && bill.total_list_usd != null
                ? `${bill.currency_code} ${bill.total_list_usd.toFixed(4)}`
                : '—'}
            </div>
          </div>
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Model serving DBU
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-muted)]">Workspace · not narrowed by agent</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
              {bill && !bill.error && bill.total_dbu != null ? bill.total_dbu.toFixed(6) : '—'}
            </div>
          </div>
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Gateway tokens
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-teal)]">Scoped to focus (and task if pinned)</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
              {gw && !gw.error && gw.total_tokens != null ? gw.total_tokens.toLocaleString() : '—'}
            </div>
          </div>
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Gateway requests
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-teal)]">Scoped to focus (and task if pinned)</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
              {gw && !gw.error && gw.total_requests != null ? gw.total_requests.toLocaleString() : '—'}
            </div>
          </div>
        </div>
        {bill?.pricing_partial ? (
          <p className="mt-2 text-xs text-[var(--color-warn-fg)]">Some rows missing a list price.</p>
        ) : null}
        {bill?.error ? <p className="mt-2 text-sm text-[var(--color-warn-fg)]">{bill.error}</p> : null}
        {gw?.error ? <p className="mt-2 text-sm text-[var(--color-warn-fg)]">{gw.error}</p> : null}
        {bill && !bill.error && bill.note ? (
          <p className="mt-3 text-[11px] text-[var(--color-muted)]">{bill.note}</p>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Hourly payload size (proxy)">
          {hourlyChart.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">No hourly data.</p>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={hourlyChart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" opacity={0.6} />
                  <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={20} />
                  <YAxis {...axisProps} width={44} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--color-surface-elevated)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 8,
                      color: 'var(--color-fg)',
                      fontSize: 12,
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="est_tokens"
                    name="Est. tokens / h"
                    stroke="var(--color-accent)"
                    fill="var(--color-accent-soft)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="By destination (proxy)">
          {barData.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">No rollups.</p>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={barData} layout="vertical" margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" horizontal={false} opacity={0.6} />
                  <XAxis type="number" {...axisProps} />
                  <YAxis type="category" dataKey="name" {...axisProps} width={100} tick={{ fontSize: 9 }} />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--color-surface-elevated)',
                      border: '1px solid var(--color-border)',
                      borderRadius: 8,
                      color: 'var(--color-fg)',
                      fontSize: 12,
                    }}
                    formatter={(value: number, name: string) => [value.toLocaleString(), name]}
                    labelFormatter={(_, payload) =>
                      payload?.[0]?.payload?.full ? String(payload[0].payload.full) : ''
                    }
                  />
                  <Bar dataKey="est_tokens" name="Est. tokens" fill="var(--color-teal)" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      {bill && !bill.error && (bill.by_endpoint ?? []).length > 0 ? (
        <Card title="List price by serving endpoint">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <th className="pb-3 pr-4">Endpoint</th>
                  <th className="pb-3 pr-4">SKU</th>
                  <th className="pb-3 pr-4">DBU</th>
                  <th className="pb-3 pr-4">$/DBU</th>
                  <th className="pb-3">List USD</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {(bill.by_endpoint ?? []).map((r) => (
                  <tr key={`${r.sku_name}-${r.endpoint_name}`}>
                    <td className="py-2 pr-4 font-mono text-xs text-[var(--color-fg)]">{r.endpoint_name}</td>
                    <td className="max-w-[220px] truncate py-2 pr-4 font-mono text-[10px] text-[var(--color-muted)]">
                      {r.sku_name}
                    </td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--color-muted)]">{r.dbu.toFixed(6)}</td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--color-muted)]">
                      {r.usd_per_dbu != null ? r.usd_per_dbu.toFixed(4) : '—'}
                    </td>
                    <td className="py-2 tabular-nums text-[var(--color-fg)]">
                      {r.list_usd != null ? r.list_usd.toFixed(6) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      {gw && !gw.error && (gw.by_model ?? []).length > 0 ? (
        <Card title="Gateway tokens by model">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-left text-sm">
              <thead className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <th className="pb-3 pr-4">Model</th>
                  <th className="pb-3 pr-4">Requests</th>
                  <th className="pb-3 pr-4">In</th>
                  <th className="pb-3 pr-4">Out</th>
                  <th className="pb-3">Total</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {(gw.by_model ?? []).map((r) => (
                  <tr key={r.model}>
                    <td className="py-2 pr-4 font-mono text-xs text-[var(--color-fg)]">{r.model}</td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--color-muted)]">{r.requests}</td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--color-muted)]">
                      {r.input_tokens.toLocaleString()}
                    </td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--color-muted)]">
                      {r.output_tokens.toLocaleString()}
                    </td>
                    <td className="py-2 tabular-nums font-medium text-[var(--color-fg)]">
                      {r.total_tokens.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <Card title="Inference table (proxy)">
        <p className="mb-3 text-xs text-[var(--color-muted)]">
          {(total != null ? Math.round(total) : 0).toLocaleString()} est. tokens (char/4) in window.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[480px] text-left text-sm">
            <thead className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              <tr>
                <th className="pb-3 pr-4">Destination</th>
                <th className="pb-3 pr-4">Requests</th>
                <th className="pb-3">Est. tokens</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {(data?.by_destination ?? []).length === 0 ? (
                <tr>
                  <td colSpan={3} className="py-6 text-center text-[var(--color-muted)]">
                    No rows.
                  </td>
                </tr>
              ) : (
                (data?.by_destination ?? []).map((r) => (
                  <tr key={r.destination}>
                    <td className="py-2 pr-4 font-mono text-xs text-[var(--color-fg)]">{r.destination}</td>
                    <td className="py-2 pr-4 tabular-nums text-[var(--color-muted)]">{r.requests}</td>
                    <td className="py-2 tabular-nums text-[var(--color-muted)]">
                      {Math.round(r.est_tokens).toLocaleString()}
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
