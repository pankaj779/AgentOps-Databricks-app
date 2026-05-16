import { Moon, Sun } from 'lucide-react'
import type { ThemeMode } from '@/lib/theme'

type ThemeToggleProps = {
  mode: ThemeMode
  onToggle: () => void
}

export function ThemeToggle({ mode, onToggle }: ThemeToggleProps) {
  const isLight = mode === 'light'
  return (
    <button
      type="button"
      onClick={onToggle}
      className="inline-flex items-center gap-2 rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-fg)] transition hover:bg-[var(--color-surface-elevated)]"
      aria-label={isLight ? 'Switch to dark theme' : 'Switch to light theme'}
      title={isLight ? 'Dark mode' : 'Light mode'}
    >
      {isLight ? <Moon className="h-4 w-4" aria-hidden /> : <Sun className="h-4 w-4" aria-hidden />}
      <span>{isLight ? 'Dark' : 'Light'}</span>
    </button>
  )
}
