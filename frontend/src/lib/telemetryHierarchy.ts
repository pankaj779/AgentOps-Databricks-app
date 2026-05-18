import type { GovernanceLineageResponse, TelemetryHierarchyGraph } from '@/lib/api'

/** Build lineage graph only from API payload (no fixed table names or URLs). */
export function buildTelemetryHierarchy(lin: GovernanceLineageResponse): TelemetryHierarchyGraph {
  const nodes: TelemetryHierarchyGraph['nodes'] = []
  const edges: TelemetryHierarchyGraph['edges'] = []

  const fqns =
    lin.inference_table_fqns?.length
      ? [...lin.inference_table_fqns]
      : lin.inference_table
        ? [lin.inference_table]
        : []

  const rf = lin.runtime_flow
  const routes = rf?.routes ?? []
  const hubSet = new Set(fqns.map((f) => f.toLowerCase()))

  const gwId = 'layer:gateway'
  const gwUrl = routes.map((r) => r.url).find((u) => u && String(u).trim()) ?? null
  const gwLabel =
    routes.find((r) => r.api_type)?.api_type?.toString() ||
    routes.find((r) => r.destination_id)?.destination_id?.toString() ||
    'AI Gateway'
  nodes.push({
    id: gwId,
    kind: 'gateway',
    label: String(gwLabel),
    detail: gwUrl,
    meta: rf?.total_requests != null ? `${rf.total_requests} reqs (${rf.window_days ?? 7}d)` : null,
  })

  const usageId = 'layer:system-usage'
  const hasUsageInLineage = (lin.workspace_lineage_recent ?? []).some((e) =>
    (e.source ?? '').toLowerCase().includes('ai_gateway'),
  )
  if (hasUsageInLineage || routes.length > 0 || fqns.length > 0) {
    nodes.push({
      id: usageId,
      kind: 'route',
      label: 'system.ai_gateway.usage',
      detail: 'Databricks system table (token metering)',
      meta: null,
    })
    edges.push({ from: gwId, to: usageId })
  }

  const routeIds: string[] = []
  for (const r of routes) {
    const dest = String(r.destination_id ?? r.api_type ?? '').trim() || 'route'
    const rid = `route:${dest}`
    if (routeIds.includes(rid)) continue
    routeIds.push(rid)
    nodes.push({
      id: rid,
      kind: 'route',
      label: dest.length > 32 ? `${dest.slice(0, 30)}…` : dest,
      detail: r.url ?? gwUrl ?? undefined,
      meta: r.requests != null ? `${r.requests} reqs` : null,
    })
    if (nodes.some((n) => n.id === usageId)) {
      edges.push({ from: usageId, to: rid })
    } else {
      edges.push({ from: gwId, to: rid })
    }
  }

  const schemaLabel =
    fqns[0]?.split('.').slice(0, -1).join('.') ||
    lin.inference_table_display?.split('.').slice(0, -1).join('.') ||
    lin.inference_table_primary?.split('.').slice(0, -1).join('.') ||
    'inference schema'

  if (fqns.length > 0) {
    const hubId = 'layer:schema'
    nodes.push({
      id: hubId,
      kind: 'schema',
      label: schemaLabel,
      detail: `${fqns.length} discovered payload table(s)`,
      meta: null,
    })
    const usageOrGw = nodes.some((n) => n.id === usageId) ? usageId : gwId
    edges.push({ from: usageOrGw, to: hubId })

    const tableSlug = (fqn: string) =>
      (fqn.split('.').pop() ?? fqn).toLowerCase().replace(/_payload$/, '')

    const routeSlug = (rid: string) => rid.split(':', 2)[1]?.toLowerCase() ?? ''

    for (const fqn of fqns) {
      const tid = `table:${fqn}`
      nodes.push({
        id: tid,
        kind: 'table',
        label: fqn.split('.').pop() ?? fqn,
        detail: fqn,
        meta: null,
      })
      edges.push({ from: hubId, to: tid })

      const slug = tableSlug(fqn)
      for (const rid of routeIds) {
        const rlabel = routeSlug(rid)
        if (
          slug &&
          rlabel &&
          (slug === rlabel ||
            slug.includes(rlabel) ||
            rlabel.includes(slug) ||
            slug.replace(/-/g, '_') === rlabel.replace(/-/g, '_'))
        ) {
          edges.push({ from: rid, to: tid })
        }
      }
    }
  }

  const seenUc = new Set<string>()
  for (const e of lin.edges ?? []) {
    const src = (e.source ?? '').trim()
    const tgt = (e.target ?? '').trim()
    if (!src || !tgt) continue
    const sl = src.toLowerCase()
    const tl = tgt.toLowerCase()
    if (tl && hubSet.has(tl) && src && !hubSet.has(sl)) {
      const uid = `uc-up:${sl}`
      if (!seenUc.has(uid)) {
        seenUc.add(uid)
        nodes.push({
          id: uid,
          kind: 'uc_upstream',
          label: src.split('.').pop() ?? src,
          detail: src,
          meta: e.entity_type ?? null,
        })
      }
      edges.push({ from: uid, to: `table:${tgt}` })
    }
    if (sl && hubSet.has(sl) && tgt && !hubSet.has(tl)) {
      const did = `uc-down:${tl}`
      if (!seenUc.has(did)) {
        seenUc.add(did)
        nodes.push({
          id: did,
          kind: 'uc_downstream',
          label: tgt.split('.').pop() ?? tgt,
          detail: tgt,
          meta: e.entity_type ?? null,
        })
      }
      edges.push({ from: `table:${src}`, to: did })
    }
  }

  return { nodes, edges }
}

/** Prefer server graph when populated; otherwise build from lineage API fields. */
export function resolveTelemetryHierarchy(lin: GovernanceLineageResponse): TelemetryHierarchyGraph {
  const built = buildTelemetryHierarchy(lin)
  const fromServer = lin.telemetry_hierarchy
  if ((fromServer?.nodes?.length ?? 0) >= built.nodes.length) {
    return fromServer!
  }
  return built
}
