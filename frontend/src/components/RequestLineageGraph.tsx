import { useEffect, useState, type ReactNode } from 'react'
import type { LineageGraph } from '@/lib/api'
import { callerDisplay } from '@/lib/callerDisplay'

const kindStyles: Record<
  string,
  { ring: string; glow: string; chip: string }
> = {
  caller: {
    ring: 'border-[#2dd4bf]/50',
    glow: 'shadow-[0_0_28px_rgba(45,212,191,0.22)]',
    chip: 'bg-[#2dd4bf]/15 text-[#5eead4]',
  },
  gateway: {
    ring: 'border-cyan-500/35',
    glow: 'shadow-[0_0_22px_rgba(6,182,212,0.18)]',
    chip: 'bg-cyan-500/15 text-cyan-200',
  },
  route: {
    ring: 'border-sky-500/35',
    glow: 'shadow-[0_0_20px_rgba(14,165,233,0.15)]',
    chip: 'bg-sky-500/15 text-sky-200',
  },
  compute: {
    ring: 'border-[var(--color-accent)]/45',
    glow: 'shadow-[0_0_26px_rgba(255,54,33,0.2)]',
    chip: 'bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
  },
  storage: {
    ring: 'border-teal-400/40',
    glow: 'shadow-[0_0_20px_rgba(45,212,191,0.15)]',
    chip: 'bg-teal-500/15 text-teal-200',
  },
  billing: {
    ring: 'border-amber-400/40',
    glow: 'shadow-[0_0_22px_rgba(251,191,36,0.18)]',
    chip: 'bg-amber-500/15 text-amber-200',
  },
  response: {
    ring: 'border-emerald-400/40',
    glow: 'shadow-[0_0_24px_rgba(52,211,153,0.2)]',
    chip: 'bg-emerald-500/15 text-emerald-200',
  },
}

const defaultKind = {
  ring: 'border-[var(--color-border)]',
  glow: 'shadow-[0_4px_20px_rgba(0,0,0,0.35)]',
  chip: 'bg-[var(--color-surface-elevated)] text-[var(--color-muted)]',
}

function FlowConnector({ active }: { active: boolean }) {
  return (
    <div
      className="relative mx-0.5 flex h-14 w-10 shrink-0 items-center justify-center sm:w-14"
      aria-hidden
    >
      <svg
        className={`h-8 w-full overflow-visible ${active ? 'text-[var(--color-teal)]' : 'text-[var(--color-muted)]'}`}
        viewBox="0 0 56 32"
      >
        <defs>
          <linearGradient id="ln-grad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.35" />
            <stop offset="50%" stopColor="currentColor" stopOpacity="1" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0.35" />
          </linearGradient>
        </defs>
        <path
          d="M 2 16 C 14 6, 28 6, 40 16 S 52 26, 54 16"
          fill="none"
          stroke="url(#ln-grad)"
          strokeWidth="2"
          strokeLinecap="round"
          className={active ? 'lineage-edge-path' : ''}
        />
        <path
          d="M 48 16 L 42 12 M 48 16 L 42 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
      </svg>
    </div>
  )
}

export function RequestLineageGraph({ graph }: { graph: LineageGraph }) {
  const nodes = graph?.nodes ?? []
  const [visibleCount, setVisibleCount] = useState(0)

  useEffect(() => {
    setVisibleCount(0)
    if (!nodes.length) return
    let i = 0
    const t = window.setInterval(() => {
      i += 1
      setVisibleCount(i)
      if (i >= nodes.length) window.clearInterval(t)
    }, 220)
    return () => window.clearInterval(t)
  }, [graph, nodes.length])

  if (!nodes.length) return null

  return (
    <div className="relative overflow-hidden rounded-2xl border border-[var(--color-border)] bg-gradient-to-br from-[var(--color-surface)] via-[var(--color-surface)] to-[var(--color-surface-elevated)]/90 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            'radial-gradient(circle at 20% 30%, var(--color-teal) 0%, transparent 45%), radial-gradient(circle at 80% 70%, var(--color-accent) 0%, transparent 40%)',
        }}
      />
      <div className="relative">
        <div className="flex items-center justify-between gap-2">
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[var(--color-teal)]">
            Request journey
          </div>
          <span className="text-[10px] text-[var(--color-muted)]">Animated flow · hover for detail</span>
        </div>
        <div className="mt-4 overflow-x-auto pb-1">
          <div className="flex min-w-max flex-nowrap items-center px-0.5">
            {nodes.map((n, i) => {
              const st = kindStyles[n.kind] ?? defaultKind
              const shown = i < visibleCount
              const rawDetail = n.detail || '—'
              let body: ReactNode
              if (n.kind === 'caller' && rawDetail !== '—') {
                const lines = rawDetail
                  .split('\n')
                  .map((s) => s.trim())
                  .filter(Boolean)
                const first = lines[0] ?? ''
                const cd0 = callerDisplay(first || null)
                const sub = lines.slice(1)
                body = (
                  <>
                    <span title={cd0.full ?? first}>{cd0.primary}</span>
                    {sub.length ? (
                      <span className="mt-1 block max-h-12 overflow-y-auto text-[9px] leading-snug text-[var(--color-muted)] whitespace-pre-wrap">
                        {sub.join('\n')}
                      </span>
                    ) : null}
                  </>
                )
              } else {
                const cd = n.kind === 'caller' ? callerDisplay(rawDetail === '—' ? null : rawDetail) : null
                let primary = cd?.primary ?? rawDetail
                if (primary.length > 160) {
                  primary = `${primary.slice(0, 157)}…`
                }
                body = primary
              }
              return (
                <div
                  key={n.id}
                  className="flex items-center"
                  style={{
                    opacity: shown ? 1 : 0,
                    transform: shown ? 'translateY(0)' : 'translateY(8px)',
                    transition: 'opacity 0.35s ease, transform 0.35s ease',
                  }}
                >
                  <div className="group relative">
                    <div
                      className={`w-[10.5rem] shrink-0 cursor-default rounded-2xl border-2 bg-[var(--color-surface)]/90 px-3 py-2.5 transition duration-300 sm:w-44 ${st.ring} ${st.glow} group-hover:scale-[1.03] group-hover:brightness-105`}
                    >
                      <div
                        className={`inline-block rounded-md px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${st.chip}`}
                      >
                        {n.title}
                      </div>
                      <div className="mt-1.5 max-h-[5.5rem] overflow-y-auto whitespace-pre-wrap break-words text-[11px] leading-snug text-[var(--color-fg)]">
                        {body}
                      </div>
                    </div>
                    <div
                      className="invisible absolute bottom-full left-1/2 z-30 mb-2 w-[min(22rem,calc(100vw-3rem))] -translate-x-1/2 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/98 px-3 py-2.5 text-[11px] shadow-2xl backdrop-blur-sm group-hover:visible"
                      role="tooltip"
                    >
                      <div className="font-semibold text-[var(--color-teal)]">{n.title}</div>
                      <div className="mt-1.5 max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-[var(--color-fg)]">
                        {n.detail || '—'}
                      </div>
                      <div className="mt-2 border-t border-[var(--color-border)] pt-1.5 text-[10px] text-[var(--color-muted)]">
                        Step {i + 1} of {nodes.length}
                      </div>
                    </div>
                  </div>
                  {i < nodes.length - 1 ? <FlowConnector active={shown} /> : null}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
