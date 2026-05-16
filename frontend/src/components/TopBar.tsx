import { Badge } from '@/components/ui/Badge'
import { ThemeToggle } from '@/components/ThemeToggle'
import type { DatabricksHealth } from '@/lib/api'
import type { ThemeMode } from '@/lib/theme'

type TopBarProps = {
  title: string
  subtitle: string
  apiOk: boolean | null
  databricks: DatabricksHealth | null
  themeMode: ThemeMode
  onThemeToggle: () => void
}

function shortHost(host: string | null) {
  if (!host) return '—'
  return host.length > 36 ? `${host.slice(0, 18)}…${host.slice(-12)}` : host
}

export function TopBar({
  title,
  subtitle,
  apiOk,
  databricks,
  themeMode,
  onThemeToggle,
}: TopBarProps) {
  const db = databricks
  const dbTone =
    db?.sql_reachable === true ? 'teal' : db?.configured ? 'warn' : 'neutral'

  return (
    <header className="border-b border-[var(--color-border)] bg-[var(--color-bg)]/90 px-6 py-4 backdrop-blur-md">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-tight text-[var(--color-fg)]">{title}</h1>
          <p className="mt-0.5 text-sm text-[var(--color-muted)]">{subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ThemeToggle mode={themeMode} onToggle={onThemeToggle} />
          <Badge tone="neutral">Accelerator</Badge>
          <div className="flex items-center gap-2 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs text-[var(--color-muted)]">
            <span
              className={`h-2 w-2 rounded-full ${
                apiOk === null ? 'bg-amber-400' : apiOk ? 'bg-emerald-400' : 'bg-red-500'
              }`}
              aria-hidden
            />
            <span className="font-medium text-[var(--color-fg)]">
              {apiOk === null ? 'API' : apiOk ? 'API online' : 'API offline'}
            </span>
          </div>
          {db ? (
            <div className="flex max-w-[min(100vw-3rem,28rem)] flex-col gap-1 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-left text-[11px] text-[var(--color-muted)]">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={dbTone}>
                  {db.sql_reachable
                    ? 'Databricks SQL'
                    : db.configured
                      ? 'Databricks error'
                      : 'Not configured'}
                </Badge>
                {db.warehouse_id ? (
                  <span className="font-mono text-[10px] text-[var(--color-fg)]/80">
                    wh · {db.warehouse_id.slice(0, 8)}…
                  </span>
                ) : null}
              </div>
              <div className="truncate" title={db.host ?? undefined}>
                {shortHost(db.host)}
              </div>
              {db.last_error ? (
                <div className="text-[10px] leading-snug text-[var(--color-warn-fg)]">
                  {db.last_error}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </header>
  )
}
