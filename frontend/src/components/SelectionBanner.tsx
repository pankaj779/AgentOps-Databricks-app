import { Link, useLocation } from 'react-router-dom'
import { X } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { NAV_PATHS } from '@/lib/navigation'
import { useWorkspaceSelection } from '@/context/WorkspaceSelectionContext'

function summarizeAgent(key: string): string {
  if (key.startsWith('inf:')) return key.slice(4)
  if (key.startsWith('gw:')) return key.slice(3)
  return key
}

export function SelectionBanner() {
  const { agents, task, clearAll, setTask } = useWorkspaceSelection()
  const loc = useLocation()
  const qs = loc.search || ''

  if (!agents.length && !task) return null

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-border)] bg-[var(--color-surface)]/90 px-6 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[var(--color-muted)]">Focus</span>
        {agents.length ? (
          <span className="flex flex-wrap items-center gap-1">
            {agents.slice(0, 4).map((a) => (
              <Badge key={a} tone="teal" className="max-w-[200px] truncate font-mono text-[11px]">
                {summarizeAgent(a)}
              </Badge>
            ))}
            {agents.length > 4 ? (
              <Badge tone="neutral" className="text-[11px]">
                +{agents.length - 4} more
              </Badge>
            ) : null}
          </span>
        ) : null}
        {task ? (
          <Badge tone="neutral" className="font-mono text-[11px]">
            task {task.slice(0, 20)}
            {task.length > 20 ? '…' : ''}
          </Badge>
        ) : null}
        <span className="text-[11px] text-[var(--color-muted)]">
          {agents.length > 1
            ? 'Cost & traces use the combined scope (OR). Open a trace for per-request tokens.'
            : 'Charts and traces below respect this scope. Open a trace to pin a task.'}
        </span>
        <Link
          to={{ pathname: NAV_PATHS.agents, search: qs }}
          className="rounded-md px-2 py-1 text-[11px] font-medium text-[var(--color-teal)] hover:underline"
        >
          Agent hub
        </Link>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Link
          to={{ pathname: NAV_PATHS.cost, search: qs }}
          className="rounded-md px-2 py-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
        >
          Cost
        </Link>
        <Link
          to={{ pathname: NAV_PATHS.health, search: qs }}
          className="rounded-md px-2 py-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
        >
          Health
        </Link>
        <Link
          to={{ pathname: NAV_PATHS.quality, search: qs }}
          className="rounded-md px-2 py-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
        >
          Traces
        </Link>
        <Link
          to={{ pathname: NAV_PATHS.governance, search: qs }}
          className="rounded-md px-2 py-1 text-xs font-medium text-[var(--color-accent)] hover:underline"
        >
          Lineage
        </Link>
        {task ? (
          <button
            type="button"
            onClick={() => setTask(null)}
            className="rounded-md border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-fg)] hover:bg-[var(--color-surface-elevated)]"
          >
            Clear task
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => clearAll()}
          className="inline-flex items-center gap-1 rounded-md border border-[var(--color-border)] px-2 py-1 text-xs text-[var(--color-fg)] hover:bg-[var(--color-surface-elevated)]"
        >
          <X className="h-3 w-3" aria-hidden />
          Clear all
        </button>
      </div>
    </div>
  )
}
