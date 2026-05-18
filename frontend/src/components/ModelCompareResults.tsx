import { useMemo, useState } from 'react'
import type { CompareCostEstimate, CompareObjectiveSummary, CompareResultRow } from '@/lib/api'

function formatUsd(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (v < 0.001) return `$${v.toFixed(6)}`
  if (v < 0.01) return `$${v.toFixed(5)}`
  return `$${v.toFixed(4)}`
}

function badgesForRow(
  row: CompareResultRow,
  summary: CompareObjectiveSummary | null | undefined,
): string[] {
  const b: string[] = []
  if (!summary) return b
  if (summary.fastest_target_id === row.target_id) b.push('Fastest')
  if (summary.fewest_tokens_target_id === row.target_id) b.push('Fewest tokens')
  if (summary.cheapest_target_id === row.target_id) b.push('Lowest est. cost')
  return b
}

type Props = {
  results: CompareResultRow[]
  question?: string | null
  objectiveSummary?: CompareObjectiveSummary | null
  costEstimate?: CompareCostEstimate | null
  sortDefault?: 'tokens' | 'cost' | 'latency'
}

export function ModelCompareResults({
  results,
  question,
  objectiveSummary,
  costEstimate,
  sortDefault = 'tokens',
}: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [userBestId, setUserBestId] = useState<string | null>(null)
  const [userWorstId, setUserWorstId] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState(sortDefault)

  const sorted = useMemo(() => {
    const copy = [...results]
    copy.sort((a, b) => {
      if (sortBy === 'latency') {
        return (a.latency_ms ?? 1e9) - (b.latency_ms ?? 1e9)
      }
      if (sortBy === 'cost') {
        return (a.est_list_usd ?? 1e9) - (b.est_list_usd ?? 1e9)
      }
      const ta = Number(a.usage?.total_tokens ?? 1e9)
      const tb = Number(b.usage?.total_tokens ?? 1e9)
      return ta - tb
    })
    return copy
  }, [results, sortBy])

  if (!results.length) return null

  const expanded = sorted.find((r) => r.target_id === expandedId) ?? null

  return (
    <div className="mt-4 space-y-3">
      {question ? (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-elevated)]/50 px-3 py-2 text-xs">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--color-muted)]">
            Question
          </div>
          <p className="mt-1 whitespace-pre-wrap text-[var(--color-fg)]">{question}</p>
        </div>
      ) : null}

      {objectiveSummary?.note ? (
        <p className="text-[10px] text-[var(--color-muted)]">{objectiveSummary.note}</p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 text-[10px]">
        <span className="text-[var(--color-muted)]">Sort by</span>
        {(['tokens', 'latency', 'cost'] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setSortBy(k)}
            className={`rounded-md border px-2 py-0.5 ${
              sortBy === k
                ? 'border-[var(--color-teal)] text-[var(--color-teal)]'
                : 'border-[var(--color-border)] text-[var(--color-muted)]'
            }`}
          >
            {k}
          </button>
        ))}
      </div>

      <div className="overflow-x-auto rounded-lg border border-[var(--color-border)]">
        <table className="w-full text-left text-xs">
          <thead className="bg-[var(--color-surface-elevated)] text-[10px] font-semibold uppercase text-[var(--color-muted)]">
            <tr>
              <th className="px-2 py-2">Target</th>
              <th className="px-2 py-2">HTTP</th>
              <th className="px-2 py-2">ms</th>
              <th className="px-2 py-2">Tokens</th>
              <th className="px-2 py-2">Est. list $</th>
              <th className="px-2 py-2">Your rating</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[var(--color-border)]">
            {sorted.map((r) => {
              const auto = badgesForRow(r, objectiveSummary)
              const isBest = userBestId === r.target_id
              const isWorst = userWorstId === r.target_id
              const isOpen = expandedId === r.target_id
              return (
                <tr
                  key={r.target_id}
                  className={`cursor-pointer transition-colors hover:bg-[var(--color-surface-elevated)]/60 ${
                    isOpen ? 'bg-[var(--color-teal)]/10' : ''
                  }`}
                  onClick={() => setExpandedId((cur) => (cur === r.target_id ? null : r.target_id))}
                >
                  <td className="px-2 py-2">
                    <div className="font-medium text-[var(--color-fg)]">{r.label}</div>
                    {auto.length ? (
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {auto.map((tag) => (
                          <span
                            key={tag}
                            className="rounded bg-[var(--color-teal)]/15 px-1 py-0.5 text-[9px] text-[var(--color-teal)]"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    ) : null}
                    {r.error ? (
                      <div className="mt-1 text-[10px] text-[var(--color-danger)]">{r.error}</div>
                    ) : null}
                  </td>
                  <td className="px-2 py-2 tabular-nums">{r.status_code ?? '—'}</td>
                  <td className="px-2 py-2 tabular-nums">{Math.round(r.latency_ms)}</td>
                  <td className="px-2 py-2 font-mono tabular-nums">
                    {r.usage?.total_tokens != null ? String(r.usage.total_tokens) : '—'}
                  </td>
                  <td className="px-2 py-2 font-mono tabular-nums" title={r.cost_source ?? undefined}>
                    {formatUsd(r.est_list_usd)}
                  </td>
                  <td className="px-2 py-2" onClick={(e) => e.stopPropagation()}>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        title="Mark best answer"
                        className={`rounded border px-1.5 py-0.5 text-[10px] ${
                          isBest
                            ? 'border-emerald-500 bg-emerald-500/20 text-emerald-400'
                            : 'border-[var(--color-border)]'
                        }`}
                        onClick={() =>
                          setUserBestId((cur) => (cur === r.target_id ? null : r.target_id))
                        }
                      >
                        Best
                      </button>
                      <button
                        type="button"
                        title="Mark worst answer"
                        className={`rounded border px-1.5 py-0.5 text-[10px] ${
                          isWorst
                            ? 'border-amber-500 bg-amber-500/20 text-amber-400'
                            : 'border-[var(--color-border)]'
                        }`}
                        onClick={() =>
                          setUserWorstId((cur) => (cur === r.target_id ? null : r.target_id))
                        }
                      >
                        Worst
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {expanded ? (
        <div className="rounded-lg border border-[var(--color-teal)]/40 bg-[var(--color-surface)] px-3 py-3 text-xs">
          <div className="font-semibold text-[var(--color-fg)]">{expanded.label} — answer</div>
          {expanded.answer ? (
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-sans text-[var(--color-fg)]">
              {expanded.answer}
            </pre>
          ) : (
            <p className="mt-2 text-[var(--color-muted)]">No parsed answer (see error or raw preview).</p>
          )}
          {expanded.response_preview && !expanded.answer ? (
            <p className="mt-2 font-mono text-[10px] text-[var(--color-muted)]">{expanded.response_preview}</p>
          ) : null}
        </div>
      ) : (
        <p className="text-[10px] text-[var(--color-muted)]">Click a row to view the model answer.</p>
      )}

      {costEstimate?.note ? (
        <p className="text-[10px] text-[var(--color-muted)]">{costEstimate.note}</p>
      ) : null}
    </div>
  )
}
