import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Sidebar, type NavId } from '@/components/Sidebar'
import { SelectionBanner } from '@/components/SelectionBanner'
import { TopBar } from '@/components/TopBar'
import { NAV_PATHS, pathToNav } from '@/lib/navigation'
import { CostView } from '@/views/CostView'
import { GovernanceView } from '@/views/GovernanceView'
import { HealthView } from '@/views/HealthView'
import { OverviewView } from '@/views/OverviewView'
import { QualityView } from '@/views/QualityView'
import { AgentHubView } from '@/views/AgentHubView'
import type { DatabricksHealth } from '@/lib/api'
import { fetchHealth } from '@/lib/api'
import { applyThemeToDocument, getStoredTheme, setStoredTheme, type ThemeMode } from '@/lib/theme'

const pageMeta: Record<NavId, { title: string; subtitle: string }> = {
  overview: {
    title: 'Mission control',
    subtitle: 'Unified snapshot across health, cost, quality, and governance.',
  },
  agents: {
    title: 'Agents',
    subtitle: 'Browse models, multi-select combined cost, then drill into requests and token detail.',
  },
  health: {
    title: 'Agent health',
    subtitle: 'Latency, reliability, and saturation — powered by Inference Tables.',
  },
  cost: {
    title: 'Cost & tokens',
    subtitle: 'Understand spend drivers before the invoice surprises you.',
  },
  quality: {
    title: 'Quality & safety',
    subtitle: 'Evaluations and traces that explain model behavior in production.',
  },
  governance: {
    title: 'Governance & audit',
    subtitle: 'Unity Catalog lineage and access patterns tied to agent workloads.',
  },
}

export function AppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const nav = pathToNav(location.pathname)
  const [apiOk, setApiOk] = useState<boolean | null>(null)
  const [db, setDb] = useState<DatabricksHealth | null>(null)
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => getStoredTheme())

  useEffect(() => {
    applyThemeToDocument(themeMode)
  }, [themeMode])

  const toggleTheme = () => {
    setThemeMode((prev) => {
      const next = prev === 'dark' ? 'light' : 'dark'
      setStoredTheme(next)
      applyThemeToDocument(next)
      return next
    })
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const h = await fetchHealth()
        if (!cancelled) {
          setApiOk(true)
          setDb(h.databricks)
        }
      } catch {
        if (!cancelled) {
          setApiOk(false)
          setDb(null)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const meta = pageMeta[nav]

  const onNavigate = (id: NavId) => {
    navigate({ pathname: NAV_PATHS[id], search: location.search })
  }

  return (
    <div className="flex min-h-svh">
      <Sidebar active={nav} onNavigate={onNavigate} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar
          title={meta.title}
          subtitle={meta.subtitle}
          apiOk={apiOk}
          databricks={db}
          themeMode={themeMode}
          onThemeToggle={toggleTheme}
        />
        <SelectionBanner />
        <main className="app-main flex-1 overflow-y-auto">
          {nav === 'overview' ? <OverviewView /> : null}
          {nav === 'agents' ? <AgentHubView /> : null}
          {nav === 'health' ? <HealthView /> : null}
          {nav === 'cost' ? <CostView /> : null}
          {nav === 'quality' ? <QualityView /> : null}
          {nav === 'governance' ? <GovernanceView /> : null}
        </main>
      </div>
    </div>
  )
}
