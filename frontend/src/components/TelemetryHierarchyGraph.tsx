import { useCallback, useMemo, useState } from 'react'
import type { TelemetryHierarchyGraph as TelemetryGraph, TelemetryHierarchyNode } from '@/lib/api'

const DATA_KIND_ORDER = [
  'uc_upstream',
  'gateway',
  'route',
  'schema',
  'table',
  'uc_downstream',
] as const

const COST_KIND_ORDER = ['billing_source', 'usage_table', 'schema', 'table'] as const

const dataKindColor: Record<string, string> = {
  gateway: '#22d3ee',
  route: '#38bdf8',
  schema: '#ff3621',
  table: '#2dd4bf',
  uc_upstream: '#fbbf24',
  uc_downstream: '#a78bfa',
}

const costKindColor: Record<string, string> = {
  billing_source: '#fbbf24',
  usage_table: '#38bdf8',
  schema: '#ff3621',
  table: '#2dd4bf',
}

type PlacedNode = TelemetryHierarchyNode & { x: number; y: number; w: number; h: number }

function layoutNodes(
  nodes: TelemetryHierarchyNode[],
  kindOrder: readonly string[],
): { placed: PlacedNode[]; width: number; height: number } {
  const byKind = (k: string) => nodes.filter((n) => n.kind === k)
  const layerGap = 72
  const nodeW = 172
  const nodeH = 58
  const gapX = 14
  const padX = 32
  let y = 28
  const placed: PlacedNode[] = []
  let maxRowW = 0

  for (const kind of kindOrder) {
    const layer = byKind(kind)
    if (!layer.length) continue
    const rowW = layer.length * nodeW + Math.max(0, layer.length - 1) * gapX
    maxRowW = Math.max(maxRowW, rowW)
    let x = padX + Math.max(0, (maxRowW - rowW) / 2)
    for (const n of layer) {
      placed.push({ ...n, x, y, w: nodeW, h: nodeH })
      x += nodeW + gapX
    }
    y += nodeH + layerGap
  }

  const width = Math.max(640, maxRowW + padX * 2)
  const height = Math.max(280, y + 24)
  return { placed, width, height }
}

function edgeKey(e: { from: string; to: string }) {
  return `${e.from}\0${e.to}`
}

function collectEdges(nodes: Set<string>, edges: TelemetryGraph['edges']) {
  const edgeKeys = new Set<string>()
  for (const e of edges) {
    if (nodes.has(e.from) && nodes.has(e.to)) {
      edgeKeys.add(edgeKey(e))
    }
  }
  return edgeKeys
}

function walkUpstream(
  startId: string,
  edges: TelemetryGraph['edges'],
  nodes: Set<string>,
  skipSiblingRoutes?: string,
) {
  const queue = [startId]
  while (queue.length) {
    const cur = queue.shift()!
    for (const e of edges) {
      if (e.to !== cur) continue
      const next = e.from
      if (skipSiblingRoutes && next.startsWith('route:') && next !== skipSiblingRoutes) continue
      if (!nodes.has(next)) {
        nodes.add(next)
        queue.push(next)
      }
    }
  }
}

function walkDownstream(startId: string, edges: TelemetryGraph['edges'], nodes: Set<string>) {
  const queue = [startId]
  while (queue.length) {
    const cur = queue.shift()!
    for (const e of edges) {
      if (e.from !== cur) continue
      const next = e.to
      if (!nodes.has(next)) {
        nodes.add(next)
        queue.push(next)
      }
    }
  }
}

function lineagePathForNode(
  nodeId: string,
  kind: string | undefined,
  edges: TelemetryGraph['edges'],
  allIds: Set<string>,
): { nodes: Set<string>; edgeKeys: Set<string> } {
  if (kind === 'gateway' || kind === 'billing_source') {
    const nodes = new Set(allIds)
    return { nodes, edgeKeys: collectEdges(nodes, edges) }
  }

  if (kind === 'table') {
    const routesForTable = new Set<string>()
    for (const e of edges) {
      if (e.to === nodeId && e.from.startsWith('route:')) routesForTable.add(e.from)
    }
    const nodes = new Set<string>([nodeId])
    const queue = [nodeId]
    while (queue.length) {
      const cur = queue.shift()!
      for (const e of edges) {
        if (e.to !== cur) continue
        const next = e.from
        if (next.startsWith('table:') && next !== nodeId) continue
        if (next.startsWith('route:')) {
          if (routesForTable.size > 0 && !routesForTable.has(next)) continue
          if (routesForTable.size === 0) continue
        }
        if (!nodes.has(next)) {
          nodes.add(next)
          queue.push(next)
        }
      }
    }
    return { nodes, edgeKeys: collectEdges(nodes, edges) }
  }

  if (kind === 'schema' || kind === 'usage_table') {
    const nodes = new Set<string>([nodeId])
    walkUpstream(nodeId, edges, nodes)
    for (const e of edges) {
      if (e.from === nodeId && e.to.startsWith('table:')) nodes.add(e.to)
    }
    return { nodes, edgeKeys: collectEdges(nodes, edges) }
  }

  if (kind === 'route') {
    const nodes = new Set<string>([nodeId])
    walkUpstream(nodeId, edges, nodes, nodeId)
    for (const e of edges) {
      if (e.from === nodeId && e.to.startsWith('table:')) nodes.add(e.to)
    }
    return { nodes, edgeKeys: collectEdges(nodes, edges) }
  }

  const nodes = new Set<string>([nodeId])
  walkUpstream(nodeId, edges, nodes)
  walkDownstream(nodeId, edges, nodes)
  return { nodes, edgeKeys: collectEdges(nodes, edges) }
}

function NodeDetailPanel({ n, pinned }: { n: PlacedNode; pinned: boolean }) {
  const u = n.usage
  return (
    <div className="mt-3 rounded-lg border border-[var(--color-teal)]/40 bg-[var(--color-teal)]/10 px-3 py-2 text-xs">
      <div className="font-semibold text-[var(--color-fg)]">{n.label}</div>
      <div className="mt-0.5 text-[10px] uppercase tracking-wide text-[var(--color-muted)]">
        {(n.kind ?? 'node').replace(/_/g, ' ')}
        {pinned ? ' · pinned' : ' · hover'}
      </div>
      {n.detail ? (
        <div className="mt-1 break-all font-mono text-[10px] text-[var(--color-muted)]">{n.detail}</div>
      ) : null}
      {n.meta ? <div className="mt-1 text-[var(--color-muted)]">{n.meta}</div> : null}
      {u?.role ? <p className="mt-2 text-[var(--color-fg)]">{u.role}</p> : null}
      {u?.requests_7d != null || u?.est_tokens_7d != null ? (
        <p className="mt-1 tabular-nums text-[var(--color-teal)]">
          {u?.requests_7d != null ? `${u.requests_7d} requests (7d)` : ''}
          {u?.requests_7d != null && u?.est_tokens_7d != null ? ' · ' : ''}
          {u?.est_tokens_7d != null ? `~${u.est_tokens_7d.toLocaleString()} est. tokens` : ''}
        </p>
      ) : null}
      {u?.used_by?.length ? (
        <div className="mt-2">
          <div className="text-[10px] font-semibold uppercase text-[var(--color-muted)]">Used in AgentOps</div>
          <ul className="mt-1 list-inside list-disc text-[10px] text-[var(--color-muted)]">
            {u.used_by.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

function NodeShape({
  n,
  fill,
  active,
  isHover,
  onHover,
  onUnhover,
  onPin,
}: {
  n: PlacedNode
  fill: string
  active: boolean
  isHover: boolean
  onHover?: () => void
  onUnhover?: () => void
  onPin?: () => void
}) {
  const interactive = Boolean(onHover || onPin)
  const tip =
    n.usage?.role && n.kind === 'table'
      ? `${n.label}: ${n.usage.role}`
      : n.meta
        ? `${n.label} — ${n.meta}`
        : n.label
  return (
    <g opacity={active ? 1 : 0.18} style={{ pointerEvents: interactive ? 'auto' : 'none' }}>
      <title>{tip}</title>
      <rect
        x={n.x}
        y={n.y}
        width={n.w}
        height={n.h}
        rx={10}
        fill="var(--color-surface-elevated)"
        stroke={isHover ? '#ffffff' : fill}
        strokeWidth={isHover ? 2.5 : 1.5}
        style={{ cursor: interactive ? 'pointer' : 'default' }}
        onPointerEnter={onHover}
        onPointerLeave={onUnhover}
        onClick={onPin}
      />
      <text
        x={n.x + n.w / 2}
        y={n.y + 18}
        textAnchor="middle"
        fill={fill}
        fontSize="9"
        fontWeight="600"
        pointerEvents="none"
      >
        {(n.kind ?? '').replace(/_/g, ' ').toUpperCase()}
      </text>
      <text
        x={n.x + n.w / 2}
        y={n.y + 34}
        textAnchor="middle"
        fill="var(--color-fg)"
        fontSize="11"
        fontWeight="600"
        pointerEvents="none"
      >
        {n.label.length > 24 ? `${n.label.slice(0, 22)}…` : n.label}
      </text>
      {n.meta ? (
        <text
          x={n.x + n.w / 2}
          y={n.y + 48}
          textAnchor="middle"
          fill="var(--color-muted)"
          fontSize="9"
          pointerEvents="none"
        >
          {n.meta.length > 28 ? `${n.meta.slice(0, 26)}…` : n.meta}
        </text>
      ) : null}
    </g>
  )
}

export function TelemetryHierarchyGraph({
  graph,
  variant = 'data',
}: {
  graph: TelemetryGraph | null | undefined
  variant?: 'data' | 'cost_tokens'
}) {
  const [zoom, setZoom] = useState(1)
  const [hoverId, setHoverId] = useState<string | null>(null)
  const [pinnedId, setPinnedId] = useState<string | null>(null)
  const focusId = pinnedId ?? hoverId

  const kindOrder = variant === 'cost_tokens' ? COST_KIND_ORDER : DATA_KIND_ORDER
  const colors = variant === 'cost_tokens' ? costKindColor : dataKindColor

  const { placed, edges, width, height, tableNodes, otherNodes } = useMemo(() => {
    const nodes = graph?.nodes ?? []
    const { placed, width, height } = layoutNodes(nodes, kindOrder)
    return {
      placed,
      edges: graph?.edges ?? [],
      width,
      height,
      tableNodes: placed.filter((n) => n.kind === 'table'),
      otherNodes: placed.filter((n) => n.kind !== 'table'),
    }
  }, [graph, kindOrder])

  const allIds = useMemo(() => new Set(placed.map((n) => n.id)), [placed])

  const highlight = useMemo(() => {
    if (!focusId) return null
    const kind = placed.find((n) => n.id === focusId)?.kind
    return lineagePathForNode(focusId, kind, edges, allIds)
  }, [focusId, edges, placed, allIds])

  const focusedNode = placed.find((n) => n.id === focusId) ?? null

  const zoomIn = useCallback(() => setZoom((z) => Math.min(2.5, +(z + 0.2).toFixed(2))), [])
  const zoomOut = useCallback(() => setZoom((z) => Math.max(0.45, +(z - 0.2).toFixed(2))), [])
  const resetView = useCallback(() => setZoom(1), [])

  if (!placed.length) {
    return (
      <p className="text-sm text-[var(--color-muted)]">
        No lineage graph data yet — check inference tables are configured and the API can reach Databricks SQL.
      </p>
    )
  }

  const pos = new Map(placed.map((n) => [n.id, n]))

  const renderNode = (n: PlacedNode) => {
    const fill = colors[n.kind ?? ''] ?? '#94a3b8'
    const active = !highlight || highlight.nodes.has(n.id)
    const isFocus = focusId === n.id
    return (
      <NodeShape
        key={n.id}
        n={n}
        fill={fill}
        active={active}
        isHover={isFocus}
        onHover={() => setHoverId(n.id)}
        onUnhover={() => setHoverId((cur) => (cur === n.id ? null : cur))}
        onPin={() => setPinnedId((cur) => (cur === n.id ? null : n.id))}
      />
    )
  }

  const hint =
    variant === 'cost_tokens'
      ? 'Billing → gateway metering → payload tables. Hover a table for how AgentOps uses it.'
      : 'AI Gateway → routes → payload tables. Hover a table for usage details.'

  return (
    <div className="relative w-full min-w-0">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[10px] text-[var(--color-muted)]">
          {hint} Scroll to pan · +/- zoom · click to pin path.
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={zoomOut}
            className="rounded-md border border-[var(--color-border)] px-2 py-1 text-xs hover:bg-[var(--color-surface-elevated)]"
            aria-label="Zoom out"
          >
            −
          </button>
          <span className="min-w-[3rem] text-center text-[10px] tabular-nums text-[var(--color-muted)]">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            onClick={zoomIn}
            className="rounded-md border border-[var(--color-border)] px-2 py-1 text-xs hover:bg-[var(--color-surface-elevated)]"
            aria-label="Zoom in"
          >
            +
          </button>
          <button
            type="button"
            onClick={resetView}
            className="rounded-md border border-[var(--color-border)] px-2 py-1 text-[10px] hover:bg-[var(--color-surface-elevated)]"
          >
            Reset
          </button>
        </div>
      </div>

      <div className="max-h-[min(70vh,520px)] overflow-auto rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)]/50 p-2">
        <div
          style={{
            width: width * zoom,
            height: height * zoom,
            minWidth: '100%',
          }}
        >
          <svg
            width={width * zoom}
            height={height * zoom}
            viewBox={`0 0 ${width} ${height}`}
            className="block"
            role="img"
            aria-label={variant === 'cost_tokens' ? 'Cost and tokens lineage' : 'Data flow hierarchy'}
          >
            <defs>
              <marker id="ln-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 Z" fill="#2dd4bf" />
              </marker>
              <marker id="ln-arrow-dim" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 Z" fill="#64748b" opacity="0.35" />
              </marker>
            </defs>
            <g pointerEvents="none">
              {edges.map((e, i) => {
                const a = pos.get(e.from)
                const b = pos.get(e.to)
                if (!a || !b) return null
                const ek = `${e.from}\0${e.to}`
                const active = !highlight || highlight.edgeKeys.has(ek)
                const x1 = a.x + a.w / 2
                const y1 = a.y + a.h
                const x2 = b.x + b.w / 2
                const y2 = b.y
                const mid = (y1 + y2) / 2
                return (
                  <path
                    key={`${e.from}-${e.to}-${i}`}
                    d={`M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`}
                    fill="none"
                    stroke={active ? '#2dd4bf' : '#475569'}
                    strokeWidth={active ? 2.5 : 1}
                    strokeOpacity={active ? 0.95 : 0.15}
                    markerEnd={active ? 'url(#ln-arrow)' : 'url(#ln-arrow-dim)'}
                  />
                )
              })}
            </g>
            {otherNodes.map(renderNode)}
            {tableNodes.map(renderNode)}
          </svg>
        </div>
      </div>

      {focusedNode ? <NodeDetailPanel n={focusedNode} pinned={Boolean(pinnedId)} /> : null}
    </div>
  )
}
