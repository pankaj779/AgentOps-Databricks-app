/**
 * Inference / AI Gateway often stores `requester` as an OAuth client id or
 * service-principal UUID — not a person's display name. Format for dense UI
 * while preserving the full value in `title` / tooltips.
 */
const UUID_LIKE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function isLikelyPrincipalUuid(s: string): boolean {
  return UUID_LIKE.test(s.trim())
}

export type CallerDisplay = {
  /** Short label for tables and chips */
  primary: string
  /** Full opaque id when `primary` is shortened */
  full?: string
}

export function callerDisplay(requester: string | null | undefined): CallerDisplay {
  if (requester == null || !String(requester).trim()) {
    return { primary: '—' }
  }
  const raw = String(requester).trim()
  if (isLikelyPrincipalUuid(raw)) {
    return { primary: `${raw.slice(0, 8)}…`, full: raw }
  }
  if (raw.length > 40) {
    return { primary: `${raw.slice(0, 38)}…`, full: raw }
  }
  return { primary: raw }
}

/**
 * Prefer an end-user hint parsed from the logged chat request (`user`, metadata.*)
 * when present; still surface the gateway `requester` (OAuth / SP) in the tooltip.
 */
export function callerDisplayForTrace(
  requestActor: string | null | undefined,
  requester: string | null | undefined,
): CallerDisplay {
  const actorRaw = requestActor != null ? String(requestActor).trim() : ''
  const reqRaw = requester != null ? String(requester).trim() : ''
  if (actorRaw) {
    const cd = callerDisplay(actorRaw)
    if (reqRaw && reqRaw !== actorRaw) {
      return {
        primary: cd.primary,
        full: `End-user (from request JSON): ${actorRaw} · Gateway requester: ${reqRaw}`,
      }
    }
    return cd
  }
  return callerDisplay(reqRaw || null)
}
