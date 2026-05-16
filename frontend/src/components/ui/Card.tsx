import type { ReactNode } from 'react'

type CardProps = {
  title?: string
  subtitle?: string
  children: ReactNode
  className?: string
}

export function Card({ title, subtitle, children, className = '' }: CardProps) {
  return (
    <section
      className={`rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-[0_1px_0_rgba(255,255,255,0.04)_inset] ${className}`}
    >
      {(title || subtitle) && (
        <header className="border-b border-[var(--color-border)] px-5 py-4">
          {title && (
            <h2 className="text-sm font-semibold tracking-tight text-[var(--color-fg)]">
              {title}
            </h2>
          )}
          {subtitle && (
            <p className="mt-1 text-xs text-[var(--color-muted)]">{subtitle}</p>
          )}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}
