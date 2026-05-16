import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'

function parseAgents(sp: URLSearchParams): string[] {
  const multi = sp.getAll('agents').filter(Boolean)
  if (multi.length) return multi
  const one = sp.get('agent')
  return one ? [one] : []
}

export type WorkspaceSelection = {
  /** All scoped agent keys (inf:… / gw:…) — combined for Cost/Traces when len>1 */
  agents: string[]
  /** First scoped agent (convenience) */
  agent: string | null
  task: string | null
  setAgents: (keys: string[]) => void
  toggleAgent: (key: string) => void
  setAgent: (key: string | null) => void
  setTask: (requestId: string | null) => void
  clearAll: () => void
}

const Ctx = createContext<WorkspaceSelection | null>(null)

export function WorkspaceSelectionProvider({ children }: { children: ReactNode }) {
  const [sp, setSp] = useSearchParams()
  const searchSignature = sp.toString()

  const agents = useMemo(() => parseAgents(new URLSearchParams(searchSignature)), [searchSignature])
  const agent = agents[0] ?? null
  const task = useMemo(() => new URLSearchParams(searchSignature).get('task'), [searchSignature])

  const setAgents = useCallback(
    (keys: string[]) => {
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev)
          n.delete('agent')
          n.delete('agents')
          const clean = keys.map((k) => k.trim()).filter(Boolean)
          for (const k of clean) n.append('agents', k)
          n.delete('task')
          return n
        },
        { replace: true },
      )
    },
    [setSp],
  )

  const toggleAgent = useCallback(
    (key: string) => {
      const k = key.trim()
      if (!k) return
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev)
          const cur = parseAgents(n)
          const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k]
          n.delete('agent')
          n.delete('agents')
          for (const x of next) n.append('agents', x)
          n.delete('task')
          return n
        },
        { replace: true },
      )
    },
    [setSp],
  )

  const setAgent = useCallback(
    (key: string | null) => {
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev)
          n.delete('agent')
          n.delete('agents')
          n.delete('task')
          if (key) n.append('agents', key.trim())
          return n
        },
        { replace: true },
      )
    },
    [setSp],
  )

  const setTask = useCallback(
    (requestId: string | null) => {
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev)
          if (requestId) n.set('task', requestId)
          else n.delete('task')
          return n
        },
        { replace: true },
      )
    },
    [setSp],
  )

  const clearAll = useCallback(() => {
    setSp({}, { replace: true })
  }, [setSp])

  const value = useMemo(
    () => ({ agents, agent, task, setAgents, toggleAgent, setAgent, setTask, clearAll }),
    [agents, agent, task, setAgents, toggleAgent, setAgent, setTask, clearAll],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useWorkspaceSelection(): WorkspaceSelection {
  const v = useContext(Ctx)
  if (!v) throw new Error('WorkspaceSelectionProvider required')
  return v
}
