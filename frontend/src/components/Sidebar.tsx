import {
  Activity,
  FolderGit2,
  Gauge,
  LayoutDashboard,
  Scale,
  ShieldCheck,
  Sparkles,
  GitCompare,
} from 'lucide-react'

export type NavId = 'overview' | 'agents' | 'compare' | 'health' | 'cost' | 'quality' | 'governance'

const items: { id: NavId; label: string; description: string; icon: typeof LayoutDashboard }[] =
  [
    {
      id: 'overview',
      label: 'Overview',
      description: 'Workspace snapshot',
      icon: LayoutDashboard,
    },
    {
      id: 'agents',
      label: 'Agents',
      description: 'Directory & requests',
      icon: FolderGit2,
    },
    {
      id: 'compare',
      label: 'Compare models',
      description: 'Scorecard & live test',
      icon: GitCompare,
    },
    {
      id: 'health',
      label: 'Agent health',
      description: 'Latency, errors, throughput',
      icon: Activity,
    },
    {
      id: 'cost',
      label: 'Cost & tokens',
      description: 'Spend attribution',
      icon: Gauge,
    },
    {
      id: 'quality',
      label: 'Quality & safety',
      description: 'Evals & traces',
      icon: Sparkles,
    },
    {
      id: 'governance',
      label: 'Governance',
      description: 'UC lineage & audit',
      icon: ShieldCheck,
    },
  ]

type SidebarProps = {
  active: NavId
  onNavigate: (id: NavId) => void
}

export function Sidebar({ active, onNavigate }: SidebarProps) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="flex items-center gap-2 border-b border-[var(--color-border)] px-5 py-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
          <Scale className="h-5 w-5" aria-hidden />
        </div>
        <div>
          <div className="text-sm font-semibold tracking-tight text-[var(--color-fg)]">AgentOps</div>
          <div className="text-[11px] text-[var(--color-muted)]">Databricks accelerator</div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 p-3" aria-label="Primary">
        {items.map((item) => {
          const Icon = item.icon
          const isActive = item.id === active
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              className={`flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition ${
                isActive
                  ? 'bg-[var(--color-surface-elevated)] text-[var(--color-fg)] ring-1 ring-[var(--color-accent)]/40'
                  : 'text-[var(--color-muted)] hover:bg-[var(--color-surface-elevated)]/60 hover:text-[var(--color-fg)]'
              }`}
            >
              <Icon
                className={`mt-0.5 h-4 w-4 shrink-0 ${isActive ? 'text-[var(--color-accent)]' : ''}`}
                aria-hidden
              />
              <span>
                <span className="block text-sm font-medium">{item.label}</span>
                <span className="block text-[11px] text-[var(--color-muted)]">
                  {item.description}
                </span>
              </span>
            </button>
          )
        })}
      </nav>

      <div className="border-t border-[var(--color-border)] p-4 text-[11px] leading-relaxed text-[var(--color-muted)]">
        Inference Tables · MLflow · Unity Catalog · Lakebase
      </div>
    </aside>
  )
}
