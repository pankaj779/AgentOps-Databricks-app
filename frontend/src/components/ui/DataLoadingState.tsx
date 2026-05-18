import { useEffect, type ReactNode } from 'react'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'

/** Dims content and shows spinner overlay; sets wait cursor on body while loading. */
export function DataLoadingState({
  loading,
  label = 'Loading…',
  children,
  minHeight = '14rem',
}: {
  loading: boolean
  label?: string
  children: ReactNode
  minHeight?: string
}) {
  useEffect(() => {
    if (!loading) {
      document.body.style.cursor = ''
      return
    }
    document.body.style.cursor = 'wait'
    return () => {
      document.body.style.cursor = ''
    }
  }, [loading])

  return (
    <div className="relative" style={{ minHeight: loading ? minHeight : undefined }}>
      <div className={loading ? 'pointer-events-none select-none opacity-35' : undefined}>{children}</div>
      {loading ? (
        <div
          className="absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-[var(--color-bg)]/50 backdrop-blur-[2px]"
          aria-busy="true"
        >
          <LoadingSpinner label={label} />
        </div>
      ) : null}
    </div>
  )
}
