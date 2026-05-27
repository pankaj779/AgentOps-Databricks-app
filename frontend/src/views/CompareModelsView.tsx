import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Card } from '@/components/ui/Card'
import { DataLoadingState } from '@/components/ui/DataLoadingState'
import { ModelCompareResults } from '@/components/ModelCompareResults'
import {
  fetchCompareScorecard,
  fetchSettings,
  postBenchmarkPrompt,
  type CompareScorecardRow,
} from '@/lib/api'

export function CompareModelsView({ refreshToken = 0 }: { refreshToken?: number }) {
  const [rows, setRows] = useState<CompareScorecardRow[]>([])
  const [loading, setLoading] = useState(true)
  const [prompt, setPrompt] = useState('What is Databricks?')
  const [benchBusy, setBenchBusy] = useState(false)
  const [benchResult, setBenchResult] = useState<Awaited<ReturnType<typeof postBenchmarkPrompt>> | null>(
    null,
  )
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void Promise.all([fetchCompareScorecard(168), fetchSettings()])
      .then(([sc, cfg]) => {
        if (!cancelled) {
          setRows(sc.rows ?? [])
          if (cfg.default_compare_prompt) setPrompt(cfg.default_compare_prompt)
        }
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : 'Failed to load')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [refreshToken])

  const runLiveCompare = () => {
    setBenchBusy(true)
    setBenchResult(null)
    void postBenchmarkPrompt({
      messages: [{ role: 'user', content: prompt.trim() }],
      track_in_dashboard: false,
    })
      .then(setBenchResult)
      .catch((e) =>
        setBenchResult({
          error: e instanceof Error ? e.message : 'failed',
          results: [],
          question: prompt,
        }),
      )
      .finally(() => setBenchBusy(false))
  }

  return (
    <div className="space-y-6 p-6">
      {err ? <p className="text-sm text-[var(--color-danger)]">{err}</p> : null}
      <DataLoadingState loading={loading} label="Loading scorecard…">
        <Card
          title="Agent comparison scorecard"
          subtitle="7-day stats from gateway metering (canonical route names). Run a live compare on the same prompt below."
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="text-[10px] font-semibold uppercase text-[var(--color-muted)]">
                <tr>
                  <th className="pb-3 pr-3">Route</th>
                  <th className="pb-3 pr-3">7d requests</th>
                  <th className="pb-3 pr-3">7d tokens</th>
                  <th className="pb-3 pr-3">In / out</th>
                  <th className="pb-3">Replay</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {rows.map((r) => (
                  <tr key={r.route}>
                    <td className="py-2 pr-3">
                      <div className="font-mono text-xs text-[var(--color-fg)]">{r.label}</div>
                      {r.display_labels?.length ? (
                        <div className="text-[10px] text-[var(--color-muted)]">
                          gateway: {r.display_labels.join(', ')}
                        </div>
                      ) : null}
                    </td>
                    <td className="py-2 pr-3 tabular-nums">{r.requests_7d}</td>
                    <td className="py-2 pr-3 tabular-nums font-medium text-[var(--color-teal)]">
                      {r.total_tokens_7d.toLocaleString()}
                    </td>
                    <td className="py-2 pr-3 text-[10px] tabular-nums text-[var(--color-muted)]">
                      {r.input_tokens_7d.toLocaleString()} / {r.output_tokens_7d.toLocaleString()}
                    </td>
                    <td className="py-2">{r.in_replay_targets ? 'Yes' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="Live compare (same prompt)">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            className="mb-3 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm"
          />
          <button
            type="button"
            disabled={benchBusy || !prompt.trim()}
            onClick={runLiveCompare}
            className="rounded-lg bg-[var(--color-accent)] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            {benchBusy ? 'Running on all targets…' : 'Run on all replay targets'}
          </button>
          {benchResult ? (
            <div className="mt-4">
              <ModelCompareResults
                results={benchResult.results ?? []}
                question={benchResult.question}
                objectiveSummary={benchResult.objective_summary}
                costEstimate={benchResult.cost_estimate}
              />
            </div>
          ) : null}
          <p className="mt-3 text-[11px] text-[var(--color-muted)]">
            Pin a request for cost: open any row from{' '}
            <Link to="/agents" className="text-[var(--color-teal)] hover:underline">
              Agents → Requests
            </Link>{' '}
            then use Cost with tasks pinned (multi-select supported).
          </p>
        </Card>
      </DataLoadingState>
    </div>
  )
}
