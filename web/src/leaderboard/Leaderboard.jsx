import { useEffect, useMemo, useState } from 'react'
import { api, API_BASE } from '../api.js'

const OB_COLORS = {
  correct: '#3fb950', wrong_result: '#f85149', sql_exec_error: '#d29922',
  sql_policy_rejected: '#a371f7', empty_but_expected: '#58a6ff', timeout: '#8b96a8',
}
const pct = (v) => (v * 100).toFixed(1) + '%'
const money = (v) => (v == null ? '—' : '$' + Number(v).toFixed(5))

export default function Leaderboard() {
  const [runs, setRuns] = useState([])
  const [run, setRun] = useState(null)
  const [board, setBoard] = useState([])
  const [tiers, setTiers] = useState([])
  const [outcomes, setOutcomes] = useState([])
  const [sort, setSort] = useState({ key: 'accuracy', dir: -1 })
  const [error, setError] = useState(null)

  useEffect(() => {
    api('/api/runs').then((r) => { setRuns(r); if (r.length) setRun(r[0]) })
      .catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    if (!run) return
    const q = `?run_id=${encodeURIComponent(run)}`
    Promise.all([api('/api/leaderboard' + q), api('/api/tiers' + q), api('/api/outcomes' + q)])
      .then(([b, t, o]) => { setBoard(b); setTiers(t); setOutcomes(o); setError(null) })
      .catch((e) => setError(String(e)))
  }, [run])

  const sortedBoard = useMemo(() => {
    const rows = [...board]
    rows.sort((a, b) => (a[sort.key] > b[sort.key] ? 1 : -1) * sort.dir)
    return rows
  }, [board, sort])

  const best = Math.max(...board.map((r) => Number(r.accuracy)), 0)
  const winner = board.find((r) => Number(r.accuracy) === best) || {}
  const totalGraded = board.reduce((s, r) => s + Number(r.n_questions), 0)

  const tierKeys = [...new Set(tiers.map((t) => t.tier))].sort()
  const tierConfigs = [...new Set(tiers.map((t) => t.config_id))]
  const tierMap = Object.fromEntries(tiers.map((t) => [t.config_id + '|' + t.tier, t.accuracy]))

  const byCfg = {}
  outcomes.forEach((r) => { (byCfg[r.config_id] = byCfg[r.config_id] || {})[r.outcome] = Number(r.n) })
  const outcomeKinds = [...new Set(outcomes.map((o) => o.outcome))]

  const toggleSort = (key) =>
    setSort((s) => ({ key, dir: s.key === key ? -s.dir : -1 }))

  if (error) {
    return (
      <div className="lb-error">
        <b>Can't reach the dashboard API at {API_BASE}.</b>
        <div>{error}</div>
        <div className="lb-hint">
          Start it: <code>source .env &amp;&amp; uvicorn dashboard.app:app --port 8000</code>
          {' '}(or set <code>VITE_API_BASE</code>).
        </div>
      </div>
    )
  }

  return (
    <div className="lb-wrap">
      <div className="lb-controls">
        <label className="lb-dim">Run</label>
        <select value={run || ''} onChange={(e) => setRun(e.target.value)}>
          {runs.map((r) => <option key={r}>{r}</option>)}
        </select>
        {run && run.startsWith('mock') && (
          <span className="lb-mock">synthetic data — pending Bedrock access</span>
        )}
      </div>

      <div className="lb-cards">
        <div className="lb-card win"><div className="k">Winner</div><div className="v">{winner.config_id || '—'}</div></div>
        <div className="lb-card"><div className="k">Best accuracy</div><div className="v mono">{winner.accuracy != null ? pct(winner.accuracy) : '—'}</div></div>
        <div className="lb-card"><div className="k">Cost / correct (winner)</div><div className="v mono">{money(winner.cost_per_correct_answer)}</div></div>
        <div className="lb-card"><div className="k">Configs × answers</div><div className="v mono">{board.length} <small>× {totalGraded} graded</small></div></div>
      </div>

      <h2>Leaderboard — sorted by accuracy, then cost per correct</h2>
      <table className="lb-table">
        <thead>
          <tr>
            {[['config_id', 'Config'], ['n_questions', '# Q'], ['accuracy', 'Accuracy'],
              ['n_correct', 'Correct'], ['total_cost_usd', 'Total $'],
              ['avg_latency_ms', 'Avg latency'], ['cost_per_correct_answer', '$ / correct']]
              .map(([k, label]) => (
                <th key={k} onClick={() => toggleSort(k)}
                  className={k === 'config_id' ? 'left' : ''}>{label}</th>
              ))}
          </tr>
        </thead>
        <tbody>
          {sortedBoard.map((r) => (
            <tr key={r.config_id} className={Number(r.accuracy) === best ? 'win' : ''}>
              <td className="left">{r.config_id}{Number(r.accuracy) === best && <span className="pill">WIN</span>}</td>
              <td className="mono">{r.n_questions}</td>
              <td className="mono">{pct(r.accuracy)}</td>
              <td className="mono">{r.n_correct}</td>
              <td className="mono">${Number(r.total_cost_usd).toFixed(5)}</td>
              <td className="mono">{Math.round(r.avg_latency_ms)} ms</td>
              <td className="mono">{money(r.cost_per_correct_answer)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Accuracy by difficulty tier</h2>
      <table className="lb-table heat">
        <thead>
          <tr><th className="left">Config</th>{tierKeys.map((t) => <th key={t}>Tier {t}</th>)}</tr>
        </thead>
        <tbody>
          {tierConfigs.map((c) => (
            <tr key={c}>
              <td className="left">{c}</td>
              {tierKeys.map((t) => {
                const a = tierMap[c + '|' + t]
                const bg = a == null ? 'transparent' : `rgba(63,185,80,${0.15 + 0.6 * a})`
                return <td key={t} className="mono" style={{ background: bg }}>{a == null ? '—' : pct(a)}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Outcome breakdown</h2>
      {Object.keys(byCfg).map((c) => {
        const tot = Object.values(byCfg[c]).reduce((a, b) => a + b, 0)
        return (
          <div key={c} className="lb-ob-row">
            <span className="lb-ob-label">{c}</span>
            <span className="lb-ob-bar">
              {Object.entries(byCfg[c]).map(([k, n]) => (
                <span key={k} style={{ width: `${100 * n / tot}%`, background: OB_COLORS[k] || '#888' }} />
              ))}
            </span>
          </div>
        )
      })}
      <div className="lb-legend">
        {outcomeKinds.map((k) => (
          <span key={k}><i style={{ background: OB_COLORS[k] || '#888' }} />{k}</span>
        ))}
      </div>
    </div>
  )
}
