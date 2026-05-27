import { useCallback, useEffect, useState } from 'react'
import {
  fetchAlerts,
  fetchSettings,
  fetchStatusSummary,
  patchSettings,
  runHealthCheck,
  type ChangeAlert,
  type HealthCheckResponse,
  type StatusSummary,
} from '@/lib/api'

type Props = {
  onRefresh?: () => void
  refreshToken?: number
}

export function WorkspaceStatusBar({ onRefresh, refreshToken = 0 }: Props) {
  const [status, setStatus] = useState<StatusSummary | null>(null)
  const [alerts, setAlerts] = useState<ChangeAlert[]>([])
  const [excludeTests, setExcludeTests] = useState(true)
  const [healthOpen, setHealthOpen] = useState(false)
  const [health, setHealth] = useState<HealthCheckResponse | null>(null)
  const [healthBusy, setHealthBusy] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date())

  const load = useCallback(async () => {
    setLastRefresh(new Date())
    const [sRes, aRes, cfgRes] = await Promise.allSettled([
      fetchStatusSummary(),
      fetchAlerts(168),
      fetchSettings(),
    ])
    if (sRes.status === 'fulfilled') {
      setStatus(sRes.value)
    } else {
      setStatus(null)
    }
    if (aRes.status === 'fulfilled') {
      setAlerts(aRes.value.alerts ?? [])
    } else {
      setAlerts([])
    }
    if (cfgRes.status === 'fulfilled') {
      setExcludeTests(cfgRes.value.exclude_test_requests)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, refreshToken])

  const toggleTests = async () => {
    const next = !excludeTests
    setExcludeTests(next)
    await patchSettings({ exclude_test_requests: next })
    onRefresh?.()
    void load()
  }

  const runCheck = async () => {
    setHealthBusy(true)
    setHealthOpen(true)
    try {
      setHealth(await runHealthCheck())
    } catch (e) {
      setHealth({
        checks: [],
        passed: 0,
        total: 0,
        all_ok: false,
        error: e instanceof Error ? e.message : 'check failed',
      })
    } finally {
      setHealthBusy(false)
    }
  }

  const ago = Math.max(0, Math.round((Date.now() - lastRefresh.getTime()) / 1000))

  return (
    <div className="border-b border-[var(--color-border)] bg-[var(--color-surface)]/90 px-4 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-medium ${
              status?.sql_ok
                ? 'bg-[var(--color-teal)]/15 text-[var(--color-teal)]'
                : 'bg-[var(--color-warn-bg)] text-[var(--color-warn-fg)]'
            }`}
          >
            {status?.sql_ok ? 'SQL OK' : 'SQL issue'}
          </span>
          <span className="text-[var(--color-muted)]">
            {status?.agent_count ?? '—'} agents · {status?.payload_table_count ?? '—'} payload tables
          </span>
          {status?.sql_error && !status?.sql_ok ? (
            <span className="max-w-xs truncate text-[var(--color-warn-fg)]" title={status.sql_error}>
              ({status.sql_error})
            </span>
          ) : null}
          <span className="text-[var(--color-muted)]">· refreshed {ago}s ago</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex cursor-pointer items-center gap-1.5 text-[var(--color-muted)]">
            <input
              type="checkbox"
              checked={excludeTests}
              onChange={() => void toggleTests()}
              className="rounded border-[var(--color-border)]"
            />
            Hide replay/benchmark tests
          </label>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-md border border-[var(--color-border)] px-2 py-0.5 hover:bg-[var(--color-surface-elevated)]"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={() => void runCheck()}
            className="rounded-md bg-[var(--color-accent)] px-2 py-0.5 font-semibold text-white hover:opacity-90"
          >
            Health check
          </button>
        </div>
      </div>
      {status && alerts.length === 0 ? (
        <p className="mt-1.5 text-[10px] text-[var(--color-muted)]">
          What changed? — no large week-over-week shifts detected in gateway tokens, errors, or volume.
        </p>
      ) : null}
      {alerts.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-2">
          {alerts.slice(0, 4).map((a, i) => (
            <li
              key={`${a.title}-${i}`}
              className={`rounded-md px-2 py-1 text-[10px] ${
                a.severity === 'warn'
                  ? 'bg-[var(--color-warn-bg)] text-[var(--color-warn-fg)]'
                  : 'bg-[var(--color-teal)]/10 text-[var(--color-teal)]'
              }`}
              title={a.detail}
            >
              {a.title}
            </li>
          ))}
        </ul>
      ) : null}
      {healthOpen ? (
        <div className="mt-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/80 p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-[var(--color-fg)]">Workspace health check</span>
            <button
              type="button"
              className="text-[10px] text-[var(--color-muted)] hover:text-[var(--color-fg)]"
              onClick={() => setHealthOpen(false)}
            >
              Close
            </button>
          </div>
          {healthBusy ? (
            <p className="text-xs text-[var(--color-muted)]">Running checks…</p>
          ) : health?.error ? (
            <p className="text-xs text-[var(--color-warn-fg)]">{health.error}</p>
          ) : (
            <ul className="space-y-1 text-[11px]">
              {(health?.checks ?? []).map((c) => (
                <li key={c.name} className="flex gap-2">
                  <span className={c.ok ? 'text-[var(--color-teal)]' : 'text-[var(--color-warn-fg)]'}>
                    {c.ok ? '✓' : '✗'}
                  </span>
                  <span className="text-[var(--color-fg)]">{c.name}</span>
                  <span className="text-[var(--color-muted)]">— {c.detail}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  )
}
