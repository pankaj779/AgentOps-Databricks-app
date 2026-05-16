import type { NavId } from '@/components/Sidebar'

export const NAV_PATHS: Record<NavId, string> = {
  overview: '/',
  agents: '/agents',
  health: '/health',
  cost: '/cost',
  quality: '/quality',
  governance: '/governance',
}

export function pathToNav(pathname: string): NavId {
  const p = pathname.replace(/\/$/, '') || '/'
  if (p === '/' || p === '/overview') return 'overview'
  if (p === '/agents') return 'agents'
  if (p === '/health') return 'health'
  if (p === '/cost') return 'cost'
  if (p === '/quality') return 'quality'
  if (p === '/governance') return 'governance'
  return 'overview'
}
