import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'

function parseAgents(sp: URLSearchParams): string[] {
  const multi = sp.getAll('agents').filter(Boolean)
  if (multi.length) return multi
  const one = sp.get('agent')
  return one ? [one] : []
}

function parseTasks(sp: URLSearchParams): string[] {
  const multi = sp.getAll('tasks').filter(Boolean)
  const one = sp.get('task')
  const seen = new Set<string>()
  const out: string[] = []
  for (const t of [...multi, ...(one ? [one] : [])]) {
    const s = t.trim()
    if (s && !seen.has(s)) {
      seen.add(s)
      out.push(s)
    }
  }
  return out
}

export type WorkspaceSelection = {
  agents: string[]
  agent: string | null
  /** All pinned request IDs (multi-select). */
  tasks: string[]
  /** First pinned task (convenience). */
  task: string | null
  setAgents: (keys: string[]) => void
  toggleAgent: (key: string) => void
  setAgent: (key: string | null) => void
  setTasks: (requestIds: string[]) => void
  toggleTask: (requestId: string) => void
  setTask: (requestId: string | null) => void
  clearAll: () => void
}

const Ctx = createContext<WorkspaceSelection | null>(null)

export function WorkspaceSelectionProvider({ children }: { children: ReactNode }) {
  const [sp, setSp] = useSearchParams()
  const searchSignature = sp.toString()

  const agents = useMemo(() => parseAgents(new URLSearchParams(searchSignature)), [searchSignature])
  const agent = agents[0] ?? null
  const tasks = useMemo(() => parseTasks(new URLSearchParams(searchSignature)), [searchSignature])
  const task = tasks[0] ?? null

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
          n.delete('tasks')
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
          n.delete('tasks')
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
          n.delete('tasks')
          if (key) n.append('agents', key.trim())
          return n
        },
        { replace: true },
      )
    },
    [setSp],
  )

  const setTasks = useCallback(
    (requestIds: string[]) => {
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev)
          n.delete('task')
          n.delete('tasks')
          const clean = requestIds.map((r) => r.trim()).filter(Boolean)
          for (const r of clean) n.append('tasks', r)
          return n
        },
        { replace: true },
      )
    },
    [setSp],
  )

  const toggleTask = useCallback(
    (requestId: string) => {
      const r = requestId.trim()
      if (!r) return
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev)
          n.delete('task')
          const cur = parseTasks(n)
          const next = cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]
          n.delete('tasks')
          for (const x of next) n.append('tasks', x)
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
          n.delete('task')
          n.delete('tasks')
          if (requestId) n.set('task', requestId.trim())
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
    () => ({
      agents,
      agent,
      tasks,
      task,
      setAgents,
      toggleAgent,
      setAgent,
      setTasks,
      toggleTask,
      setTask,
      clearAll,
    }),
    [agents, agent, tasks, task, setAgents, toggleAgent, setAgent, setTasks, toggleTask, setTask, clearAll],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useWorkspaceSelection(): WorkspaceSelection {
  const v = useContext(Ctx)
  if (!v) throw new Error('WorkspaceSelectionProvider required')
  return v
}
