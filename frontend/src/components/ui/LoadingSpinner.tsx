/** Centered spinner for async data fetches. */
export function LoadingSpinner({
  label = 'Loading…',
  size = 'md',
}: {
  label?: string
  size?: 'sm' | 'md' | 'lg'
}) {
  const dim = size === 'sm' ? 'h-5 w-5 border' : size === 'lg' ? 'h-10 w-10 border-[3px]' : 'h-8 w-8 border-2'
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-6" role="status" aria-live="polite">
      <div
        className={`${dim} animate-spin rounded-full border-[var(--color-border)] border-t-[var(--color-teal)]`}
        aria-hidden
      />
      {label ? <p className="text-sm text-[var(--color-muted)]">{label}</p> : null}
    </div>
  )
}
