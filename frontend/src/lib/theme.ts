const KEY = 'agentops-theme'

export type ThemeMode = 'dark' | 'light'

export function getStoredTheme(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark') return v
  } catch {
    /* ignore */
  }
  return 'dark'
}

export function setStoredTheme(mode: ThemeMode) {
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* ignore */
  }
}

export function applyThemeToDocument(mode: ThemeMode) {
  document.documentElement.setAttribute('data-theme', mode)
}
