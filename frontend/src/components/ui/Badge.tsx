import type { ReactNode } from 'react'

const tones = {
  neutral:
    'border-[var(--color-border)] bg-[var(--color-surface-elevated)] text-[var(--color-muted)]',
  accent:
    'border-[var(--color-accent)]/35 bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
  teal: 'border-[var(--color-teal)]/35 bg-[var(--color-teal-soft)] text-[var(--color-teal)]',
  warn: 'border-[var(--color-warn-border)] bg-[var(--color-warn-bg)] text-[var(--color-warn-fg)]',
} as const

export type BadgeTone = keyof typeof tones

type BadgeProps = {
  children: ReactNode
  tone?: BadgeTone
  className?: string
}

export function Badge({ children, tone = 'neutral', className = '' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${tones[tone]} ${className}`}
    >
      {children}
    </span>
  )
}
