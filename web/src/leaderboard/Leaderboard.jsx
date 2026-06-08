import { useEffect, useMemo, useRef, useState } from 'react'
import { api, API_BASE } from '../api.js'

const OB_COLORS = {
  correct: '#3fb950', wrong_result: '#f85149', sql_exec_error: '#d29922',
  sql_policy_rejected: '#a371f7', empty_but_expected: '#58a6ff', timeout: '#8b96a8',
}
const pct = (v) => (v * 100).toFixed(1) + '%'
const money = (v) => (v == null ? '—' : '$' + Number(v).toFixed(5))

async function post(path, body) {
  const r = await fetch(API_BASE + path, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`)
  return r.json()
}

export default function Leaderboard() {
  const [runs, setRuns] = useState([])
  const [run, setRun] = useState(null)
  const [board, setBoard] = useState([])
  const [tiers, setTiers] = useState([])
  const [outcomes, setOutcomes] = useState([])
  const [meta, setMeta] = useState({})
  const [sort, setSort] = useState({ key: 'accuracy', dir: -1 })
  const [error, setError] = useState(null)

  const [expanded, setExpanded] = useState(null)
  const [details, setDetails] = useState({})
  const [conv, setConv] = useState(null)             // {config_id, loading, exchanges}
  const [turns, setTurns] = useState({})             // trace_id -> [{role,content}]

  // run-from-UI
  const [opts, setOpts] = useState({ models: [], prompts: [] })
  const [showRun, setShowRun] = useState(false)
  const [selM, setSelM] = useState(new Set())
  const [selP, setSelP] = useState(new Set())
  const [judge, setJudge] = useState(true)
  const [runName, setRunName] = useState('')
  const [runStatus, setRunStatus] = useState(null)   // {running, run_id, lines, returncode}
  const pollRef = useRef(null)

  function loadRuns(select) {
    return api('/api/runs').then((r) => { setRuns(r); if (select) setRun(select); else if (r.length && !run) setRun(r[0]) })
  }

  useEffect(() => {
    loadRuns().catch((e) => setError(String(e)))
    api('/api/meta').then(setMeta).catch(() => {})
    api('/api/grid-options').then((o) => {
      setOpts(o); setSelM(new Set(o.models)); setSelP(new Set(o.prompts))
    }).catch(() => {})
    return () => clearTimeout(pollRef.current)
  }, [])

  useEffect(() => {
    if (!run) return
    setExpanded(null); setConv(null); setDetails({})
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
  const lfBase = meta.langfuse_base
  const sessionUrl = (cfg) => lfBase && `${lfBase}/sessions/${encodeURIComponent(run + '__' + cfg)}`
  const toggleSort = (key) => setSort((s) => ({ key, dir: s.key === key ? -s.dir : -1 }))

  async function toggleExpand(cfg) {
    if (expanded === cfg) { setExpanded(null); return }
    setExpanded(cfg); setConv(null)
    if (!details[cfg]) {
      const rows = await api(`/api/questions?run_id=${encodeURIComponent(run)}&config_id=${encodeURIComponent(cfg)}`)
      setDetails((d) => ({ ...d, [cfg]: rows }))
    }
  }

  async function openConversation(cfg) {
    setConv({ config_id: cfg, loading: true, exchanges: [] })
    try {
      const r = await api(`/api/lf/session?session_id=${encodeURIComponent(run + '__' + cfg)}`)
      setConv({ config_id: cfg, loading: false, exchanges: r.exchanges || [] })
    } catch (e) {
      setConv({ config_id: cfg, loading: false, error: String(e), exchanges: [] })
    }
  }

  async function loadTurns(traceId) {
    if (!traceId || turns[traceId]) return
    try {
      const r = await api(`/api/lf/trace?trace_id=${encodeURIComponent(traceId)}`)
      setTurns((t) => ({ ...t, [traceId]: r.turns || [] }))
    } catch { /* ignore */ }
  }

  // ---- run from UI ----
  function pollStatus() {
    api('/api/run/status').then((s) => {
      setRunStatus(s)
      if (s.running) { pollRef.current = setTimeout(pollStatus, 1500) }
      else if (s.run_id) { loadRuns(s.run_id) }   // done -> select the new run
    }).catch(() => { pollRef.current = setTimeout(pollStatus, 2500) })
  }

  async function startRun() {
    try {
      const r = await post('/api/run', {
        models: [...selM], prompts: [...selP], judge,
        run_id: runName.trim() || undefined,
      })
      if (!r.ok) { setRunStatus({ running: false, lines: ['⚠ ' + (r.error || 'failed to start')] }); return }
      setRunStatus({ running: true, run_id: r.run_id, lines: ['starting…'] })
      clearTimeout(pollRef.current); pollRef.current = setTimeout(pollStatus, 1200)
    } catch (e) {
      setRunStatus({ running: false, lines: ['⚠ ' + String(e)] })
    }
  }

  if (error) {
    return (
      <div className="lb-error">
        <b>Can't reach the dashboard API at {API_BASE}.</b>
        <div>{error}</div>
        <div className="lb-hint">Start it: <code>source .env &amp;&amp; uvicorn dashboard.app:app --port 8000</code></div>
      </div>
    )
  }

  const COLS = [['config_id', 'Config'], ['n_questions', '# Q'], ['accuracy', 'Accuracy'],
    ['n_correct', 'Correct'], ['avg_judge_score', 'LLM judge'], ['total_cost_usd', 'Total $'],
    ['avg_latency_ms', 'Avg latency'], ['cost_per_correct_answer', '$ / correct']]
  const running = runStatus && runStatus.running

  return (
    <div className="lb-wrap">
      <div className="lb-controls">
        <label className="lb-dim">Run</label>
        <select value={run || ''} onChange={(e) => setRun(e.target.value)}>
          {runs.map((r) => <option key={r}>{r}</option>)}
        </select>
        <button className="lb-runbtn" onClick={() => setShowRun((v) => !v)}>▶ Run benchmark</button>
        {meta.datasets_url && (
          <a className="lb-extlink" href={meta.datasets_url} target="_blank" rel="noreferrer">
            Open experiment in LangFuse ↗
          </a>
        )}
      </div>

      {showRun && (
        <div className="lb-runpanel">
          <div className="lb-runcols">
            <div>
              <div className="lb-runhead">Models</div>
              {opts.models.map((m) => (
                <label key={m} className="lb-chk"><input type="checkbox" checked={selM.has(m)}
                  onChange={() => setSelM((s) => toggle(s, m))} />{m}</label>
              ))}
            </div>
            <div>
              <div className="lb-runhead">Prompts</div>
              {opts.prompts.map((p) => (
                <label key={p} className="lb-chk"><input type="checkbox" checked={selP.has(p)}
                  onChange={() => setSelP((s) => toggle(s, p))} />{p}</label>
              ))}
            </div>
            <div className="lb-runside">
              <div className="lb-runhead">Run name</div>
              <input className="lb-runname" placeholder="e.g. sonnet-vs-nova"
                value={runName}
                onChange={(e) => setRunName(e.target.value.replace(/[^A-Za-z0-9._-]/g, '-'))} />
              <label className="lb-chk" style={{ marginTop: '.5rem' }}>
                <input type="checkbox" checked={judge} onChange={() => setJudge((v) => !v)} />LLM judge
              </label>
              <div className="lb-dim" style={{ margin: '.4rem 0' }}>
                {selM.size}×{selP.size} configs × 18 Qs = {selM.size * selP.size * 18} agent calls
              </div>
              <button className="lb-runbtn primary" disabled={running || !selM.size || !selP.size}
                onClick={startRun}>{running ? 'running…' : 'Run'}</button>
            </div>
          </div>
          {runStatus && (
            <div className="lb-runlog">
              <div className="lb-dim">{running ? `running ${runStatus.run_id}…`
                : runStatus.returncode === 0 ? `done — run "${runStatus.run_id}" loaded`
                : runStatus.returncode != null ? `exited (code ${runStatus.returncode})` : ''}</div>
              <pre>{(runStatus.lines || []).slice(-14).join('\n')}</pre>
            </div>
          )}
        </div>
      )}

      <div className="lb-cards">
        <div className="lb-card win"><div className="k">Winner</div><div className="v">{winner.config_id || '—'}</div></div>
        <div className="lb-card"><div className="k">Best accuracy</div><div className="v mono">{winner.accuracy != null ? pct(winner.accuracy) : '—'}</div></div>
        <div className="lb-card"><div className="k">Cost / correct (winner)</div><div className="v mono">{money(winner.cost_per_correct_answer)}</div></div>
        <div className="lb-card"><div className="k">Configs × answers</div><div className="v mono">{board.length} <small>× {totalGraded}</small></div></div>
      </div>

      <h2>Leaderboard — click a row to drill into per-question LangFuse traces</h2>
      <table className="lb-table">
        <thead><tr>
          <th className="left" style={{ width: 18 }}></th>
          {COLS.map(([k, label]) => (
            <th key={k} onClick={() => toggleSort(k)} className={k === 'config_id' ? 'left' : ''}>{label}</th>
          ))}
        </tr></thead>
        <tbody>
          {sortedBoard.map((r) => {
            const isWin = Number(r.accuracy) === best
            const open = expanded === r.config_id
            return (
              <FragmentRow key={r.config_id}>
                <tr className={isWin ? 'win clickrow' : 'clickrow'} onClick={() => toggleExpand(r.config_id)}>
                  <td className="left mono">{open ? '▾' : '▸'}</td>
                  <td className="left">{r.config_id}{isWin && <span className="pill">WIN</span>}</td>
                  <td className="mono">{r.n_questions}</td>
                  <td className="mono">{pct(r.accuracy)}</td>
                  <td className="mono">{r.n_correct}</td>
                  <td className="mono">{r.avg_judge_score != null ? (r.avg_judge_score * 100).toFixed(0) + '%' : '—'}</td>
                  <td className="mono">${Number(r.total_cost_usd).toFixed(5)}</td>
                  <td className="mono">{Math.round(r.avg_latency_ms)} ms</td>
                  <td className="mono">{money(r.cost_per_correct_answer)}</td>
                </tr>
                {open && (
                  <tr className="lb-detailrow">
                    <td colSpan={COLS.length + 1}>
                      <div className="lb-detail">
                        <div className="lb-detail-bar">
                          <span>per-question results — “trace ↗” opens the LangFuse trace</span>
                          <span className="lb-detail-actions">
                            {sessionUrl(r.config_id) && <a href={sessionUrl(r.config_id)} target="_blank" rel="noreferrer">session ↗</a>}
                            <button onClick={() => openConversation(r.config_id)}>View conversation (LangFuse)</button>
                          </span>
                        </div>
                        <table className="lb-qtable">
                          <thead><tr>
                            <th className="left">Q</th><th>tier</th><th></th><th>judge</th>
                            <th>latency</th><th>outcome</th><th className="left">trace</th>
                          </tr></thead>
                          <tbody>
                            {(details[r.config_id] || []).map((q) => (
                              <tr key={q.question_id}>
                                <td className="left mono">{q.question_id}</td>
                                <td className="mono">{q.tier}</td>
                                <td className="mono">{q.correctness ? '✓' : '✗'}</td>
                                <td className="mono">{(q.judge_score * 100).toFixed(0)}%</td>
                                <td className="mono">{q.latency_ms} ms</td>
                                <td><span className="ob-tag" style={{ background: OB_COLORS[q.outcome] || '#888' }}>{q.outcome}</span></td>
                                <td className="left">{q.trace_url ? <a href={q.trace_url} target="_blank" rel="noreferrer">trace ↗</a> : '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>

                        {conv && conv.config_id === r.config_id && (
                          <div className="lb-conv">
                            <div className="lb-conv-head">Conversation history — fetched from the LangFuse API{conv.loading && ' …loading'}</div>
                            {conv.error && <div className="lb-hint">{conv.error}</div>}
                            {conv.exchanges.map((ex) => (
                              <details key={ex.question_id} className="lb-ex"
                                onToggle={(e) => e.target.open && loadTurns(ex.trace_id)}>
                                <summary>
                                  <b>{ex.question_id}</b> — {ex.question}
                                  <span className="ob-tag" style={{ background: OB_COLORS[ex.outcome] || '#888' }}>{ex.outcome}</span>
                                </summary>
                                <div className="lb-turns">
                                  {(turns[ex.trace_id] || []).filter((t) => t.role !== 'system').map((t, i) => (
                                    <div key={i} className={`bubble ${t.role}`}>
                                      <span className="role">{t.role}</span>
                                      <pre>{t.content}</pre>
                                    </div>
                                  ))}
                                  {!turns[ex.trace_id] && <div className="lb-dim">loading turns…</div>}
                                </div>
                              </details>
                            ))}
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </FragmentRow>
            )
          })}
        </tbody>
      </table>

      <h2>Accuracy by difficulty tier</h2>
      <TierHeat tiers={tiers} />
      <h2>Outcome breakdown</h2>
      <Outcomes outcomes={outcomes} />
    </div>
  )
}

function toggle(set, v) { const n = new Set(set); n.has(v) ? n.delete(v) : n.add(v); return n }
function FragmentRow({ children }) { return <>{children}</> }

function TierHeat({ tiers }) {
  const keys = [...new Set(tiers.map((t) => t.tier))].sort()
  const configs = [...new Set(tiers.map((t) => t.config_id))]
  const map = Object.fromEntries(tiers.map((t) => [t.config_id + '|' + t.tier, t.accuracy]))
  return (
    <table className="lb-table heat">
      <thead><tr><th className="left">Config</th>{keys.map((t) => <th key={t}>Tier {t}</th>)}</tr></thead>
      <tbody>
        {configs.map((c) => (
          <tr key={c}>
            <td className="left">{c}</td>
            {keys.map((t) => {
              const a = map[c + '|' + t]
              const bg = a == null ? 'transparent' : `rgba(63,185,80,${0.15 + 0.6 * a})`
              return <td key={t} className="mono" style={{ background: bg }}>{a == null ? '—' : (a * 100).toFixed(0) + '%'}</td>
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Outcomes({ outcomes }) {
  const byCfg = {}
  outcomes.forEach((r) => { (byCfg[r.config_id] = byCfg[r.config_id] || {})[r.outcome] = Number(r.n) })
  const kinds = [...new Set(outcomes.map((o) => o.outcome))]
  return (
    <>
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
        {kinds.map((k) => <span key={k}><i style={{ background: OB_COLORS[k] || '#888' }} />{k}</span>)}
      </div>
    </>
  )
}
