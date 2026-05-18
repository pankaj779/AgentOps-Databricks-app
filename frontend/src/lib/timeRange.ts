/** Rolling windows passed to API as `hours=` (backend has no calendar picker yet). */
export const TIME_RANGE_OPTIONS = [
  { label: 'Last 24 hours', hours: 24 },
  { label: 'Last 7 days', hours: 168 },
  { label: 'Last 30 days', hours: 720 },
  { label: 'Last 90 days', hours: 2160 },
] as const

export type TimeRangeHours = (typeof TIME_RANGE_OPTIONS)[number]['hours']

export function timeRangeLabel(hours: number): string {
  const hit = TIME_RANGE_OPTIONS.find((o) => o.hours === hours)
  if (hit) return hit.label.toLowerCase()
  if (hours % 24 === 0) return `last ${hours / 24} days`
  return `last ${hours} hours`
}
