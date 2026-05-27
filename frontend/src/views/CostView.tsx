import { useEffect, useMemo, useRef, useState } from 'react'
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
import { DataLoadingState } from '@/components/ui/DataLoadingState'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'
import type { CostSummaryResponse } from '@/lib/api'
import { fetchCostSummary } from '@/lib/api'
import { TIME_RANGE_OPTIONS, type TimeRangeHours } from '@/lib/timeRange'

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

export function CostView({ refreshToken = 0 }: { refreshToken?: number }) {
  const { agents, clearAll, tasks } = useWorkspaceSelection()
  const [data, setData] = useState<CostSummaryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const sawDataRef = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const [hours, setHours] = useState<TimeRangeHours>(168)

  useEffect(() => {
    let cancelled = false
    const blocking = !sawDataRef.current
    if (blocking) setLoading(true)
    else setRefreshing(true)
    ;(async () => {
      try {
        const c = await fetchCostSummary(hours, {
          agents: agents.length ? agents : null,
          tasks: tasks.length ? tasks : null,
        })
        if (!cancelled) {
          setData(c)
          sawDataRef.current = true
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load')
      } finally {
        if (!cancelled) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [agents, tasks, hours, refreshToken])

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
  const billAttr = bill?.attribution
  const billWs = bill?.workspace_reference
  const billWsUsd = billWs?.total_list_usd != null ? Number(billWs.total_list_usd) : null
  const billWsDbu = billWs?.total_dbu != null ? Number(billWs.total_dbu) : null
  const tokenEst = data?.token_cost_estimate

  const sourceOfTruth = useMemo(() => {
    const billUsd = bill?.total_list_usd != null ? Number(bill.total_list_usd) : null
    const gwTok = gw && !gw.error && gw.total_tokens != null ? Number(gw.total_tokens) : null
    const proxy = total != null ? Math.round(total) : null
    return { billUsd, gwTok, proxy }
  }, [bill, gw, total])

  const listPriceSubtitle =
    billAttr === 'request_pinned_token_prorated'
      ? 'Pinned requests — DBU/list USD prorated from workspace billing by gateway token share (billing has no request_id)'
      : billAttr === 'request_pinned'
      ? 'Pinned requests — billing matched via gateway endpoint labels'
      : billAttr === 'ai_gateway_token_estimate'
        ? 'Databricks-hosted traffic — token estimate (billing.usage had no MODEL_SERVING/AI_GATEWAY DBU rows yet)'
        : billAttr === 'endpoint_filtered'
        ? 'Scoped — gateway labels → billing endpoint (fuzzy LIKE)'
        : billAttr === 'endpoint_unmatched'
          ? 'Scope on, but no billing rows matched — check note below'
          : bill?.usage_source === 'system.billing.usage'
            ? 'Workspace — MODEL_SERVING / AI_GATEWAY DBU × list_prices'
            : 'Workspace — list price estimate'

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
    const billWsHas =
      bill?.workspace_reference &&
      !bill.error &&
      ((bill.workspace_reference.total_list_usd ?? 0) > 0 ||
        (bill.workspace_reference.total_dbu ?? 0) > 0)
    const rollups = (data.by_destination ?? []).length > 0
    const hourly = (data.hourly ?? []).length > 0
    const byReqHas = (data.by_request ?? []).some(
      (r) => (r.gateway_total_tokens ?? 0) > 0 || (r.est_payload_tokens ?? 0) > 0,
    )
    return !gwHas && !billHas && !billWsHas && !rollups && !hourly && !byReqHas
  }, [data, gw, bill])

  return (
    <div className="space-y-6 p-6">
      {error ? <p className="text-sm text-[var(--color-danger)]">{error}</p> : null}
      {data?.error ? <p className="text-sm text-[var(--color-warn-fg)]">{data.error}</p> : null}

      <DataLoadingState loading={loading} label="Loading cost & tokens…">
      <Card title="Cost & tokens">
        {refreshing ? (
          <p className="mb-2 text-[11px] text-[var(--color-muted)]" aria-live="polite">
            Refreshing workspace billing and gateway numbers — previous totals stay visible below.
          </p>
        ) : null}
        <div className="mb-4 rounded-lg border border-[var(--color-teal)]/35 bg-[var(--color-teal)]/8 px-3 py-2 text-xs text-[var(--color-fg)]">
          <span className="font-semibold text-[var(--color-teal)]">Source of truth — </span>
          Billing (list price):{' '}
          <span className="tabular-nums font-medium">
            {sourceOfTruth.billUsd != null ? `USD ${sourceOfTruth.billUsd.toFixed(4)}` : '—'}
          </span>
          {' · '}
          Gateway tokens:{' '}
          <span className="tabular-nums font-medium">
            {sourceOfTruth.gwTok != null ? sourceOfTruth.gwTok.toLocaleString() : '—'}
          </span>
          {' · '}
          Log proxy (estimate only):{' '}
          <span className="tabular-nums text-[var(--color-muted)]">
            {sourceOfTruth.proxy != null ? `~${sourceOfTruth.proxy.toLocaleString()}` : '—'}
          </span>
          {tasks.length > 0 ? (
            <span className="ml-2 text-[var(--color-teal)]">
              · scoped to {tasks.length} pinned request{tasks.length === 1 ? '' : 's'}
            </span>
          ) : agents.length > 0 ? (
            <span className="ml-2 text-[var(--color-teal)]">· {agents.length} agent(s)</span>
          ) : null}
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label className="text-xs text-[var(--color-muted)]" htmlFor="cost-hours">
            Time range
          </label>
          <select
            id="cost-hours"
            value={hours}
            onChange={(e) => setHours(Number(e.target.value) as TimeRangeHours)}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-xs"
          >
            {TIME_RANGE_OPTIONS.map((o) => (
              <option key={o.hours} value={o.hours}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        {tasks.length ? (
          <p className="mb-3 text-xs text-[var(--color-teal)]">
            {tasks.length === 1 ? (
              <>
                Pinned request <span className="font-mono">{tasks[0]}</span> — gateway tokens are for this call (see
                summary cards + table below). List price may be $0 if billing has no match for this endpoint.
              </>
            ) : (
              <>
                <span className="font-semibold">{tasks.length} pinned requests</span> — gateway and payload totals are
                combined (sum). See per-request table below.
              </>
            )}
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
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              List price (est.)
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-muted)]">{listPriceSubtitle}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
              {bill && !bill.error && bill.total_list_usd != null
                ? `${bill.currency_code} ${bill.total_list_usd.toFixed(4)}`
                : '—'}
            </div>
            {bill &&
            !bill.error &&
            bill.total_list_usd != null &&
            bill.total_list_usd === 0 &&
            billWsUsd != null &&
            billWsUsd > 0 ? (
              <p className="mt-2 text-[10px] leading-snug text-[var(--color-teal)]">
                Workspace (no endpoint filter):{' '}
                <span className="font-semibold tabular-nums">
                  {billWs?.currency_code ?? bill.currency_code} {billWsUsd.toFixed(4)}
                </span>{' '}
                — scoped billing names did not match; see note below.
              </p>
            ) : null}
          </div>
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Model serving DBU
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-muted)]">{listPriceSubtitle}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-fg)]">
              {bill && !bill.error && bill.total_dbu != null ? bill.total_dbu.toFixed(6) : '—'}
            </div>
            {bill &&
            !bill.error &&
            bill.total_dbu != null &&
            bill.total_dbu === 0 &&
            billWsDbu != null &&
            billWsDbu > 0 ? (
              <p className="mt-2 text-[10px] leading-snug text-[var(--color-teal)]">
                Workspace DBU:{' '}
                <span className="font-semibold tabular-nums">{billWsDbu.toFixed(6)}</span>
              </p>
            ) : null}
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
          <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/80 px-4 py-3">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Token est. (env rate)
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--color-muted)]">
              Gateway tokens × ${tokenEst?.usd_per_1m_tokens ?? 0.7}/1M — fallback when billing.usage has no DBU rows
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-[var(--color-teal)]">
              {tokenEst?.estimated_usd != null ? `USD ${tokenEst.estimated_usd.toFixed(4)}` : '—'}
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
        {bill?.diagnostic?.top_workspaces?.length &&
        bill.total_list_usd === 0 &&
        !bill.error ? (
          <div className="mt-3 rounded-lg border border-[var(--color-warn-fg)]/30 bg-[var(--color-warn-fg)]/10 px-3 py-2 text-[11px] text-[var(--color-muted)]">
            <p className="font-medium text-[var(--color-warn-fg)]">Billing diagnostic</p>
            <p className="mt-1">
              Configured workspace{' '}
              <span className="font-mono text-[var(--color-fg)]">
                {bill.diagnostic.configured_workspace_id ?? '—'}
              </span>
              . Top TOKEN billing in this window:
            </p>
            <ul className="mt-1 list-inside list-disc font-mono text-[10px]">
              {bill.diagnostic.top_workspaces.slice(0, 5).map((w) => (
                <li key={`${w.workspace_id}-${w.billing_origin_product}`}>
                  ws {w.workspace_id} · {w.billing_origin_product} · qty {w.usage_quantity.toFixed(4)}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Hourly payload size (proxy)"
          subtitle="Inference logs only: sum of proxy tokens per hour (agent scope + pinned task). Not gateway metering."
        >
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

        <Card
          title="By destination (proxy)"
          subtitle="Groups by payload table / destination column in agent logs — same scope as hourly. Differs from Gateway tokens when using char/4 proxy."
        >
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
        <Card
          title="Gateway tokens by model"
          subtitle="Real metering from system.ai_gateway.usage (input + output). Row totals should match the Gateway tokens card above."
        >
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
              <tfoot className="border-t border-[var(--color-border)] text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <td className="pt-3 pr-4 text-[var(--color-fg)]">Total</td>
                  <td className="pt-3 pr-4 tabular-nums">
                    {(gw.by_model ?? []).reduce((n, r) => n + (r.requests ?? 0), 0)}
                  </td>
                  <td className="pt-3 pr-4">—</td>
                  <td className="pt-3 pr-4">—</td>
                  <td className="pt-3 tabular-nums text-[var(--color-teal)]">
                    {(
                      gw.total_tokens ??
                      (gw.by_model ?? []).reduce((n, r) => n + (r.total_tokens ?? 0), 0)
                    ).toLocaleString()}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </Card>
      ) : null}

      {(data?.by_request?.length ?? 0) > 0 ? (
        <Card
          title="Per-request breakdown"
          subtitle="Gateway tokens (metered) and prorated list USD when billing scope matches."
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="text-[11px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
                <tr>
                  <th className="pb-3 pr-3">Request</th>
                  <th className="pb-3 pr-3">Model</th>
                  <th className="pb-3 pr-3">GW tokens</th>
                  <th className="pb-3 pr-3">Payload est.</th>
                  <th className="pb-3">Est. $</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {[...(data?.by_request ?? [])]
                  .sort(
                    (a, b) =>
                      (Number(b.gateway_total_tokens) || 0) - (Number(a.gateway_total_tokens) || 0),
                  )
                  .map((r) => (
                    <tr key={r.request_id}>
                      <td className="max-w-[140px] truncate py-2 pr-3 font-mono text-[10px]" title={r.request_id}>
                        {r.request_id}
                      </td>
                      <td className="max-w-[160px] truncate py-2 pr-3 text-xs text-[var(--color-muted)]">
                        {r.destination_model ?? '—'}
                      </td>
                      <td className="py-2 pr-3 font-mono tabular-nums text-[var(--color-teal)]">
                        {r.gateway_total_tokens != null ? r.gateway_total_tokens.toLocaleString() : '—'}
                      </td>
                      <td className="py-2 pr-3 tabular-nums text-[var(--color-muted)]">
                        {r.est_payload_tokens != null ? Math.round(r.est_payload_tokens).toLocaleString() : '—'}
                      </td>
                      <td className="py-2 tabular-nums text-[var(--color-fg)]">
                        {r.est_list_usd_prorated != null ? `$${r.est_list_usd_prorated.toFixed(4)}` : '—'}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <Card
        title="Log size estimate (proxy)"
        subtitle="Not gateway metering — guesses tokens from JSON body size in UC payload tables (chars ÷ 4). Usually higher than Gateway tokens."
      >
        <p className="mb-3 text-xs text-[var(--color-muted)]">
          {(total != null ? Math.round(total) : 0).toLocaleString()} est. tokens in window — do not add to Gateway
          tokens above.
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
      </DataLoadingState>
    </div>
  )
}
