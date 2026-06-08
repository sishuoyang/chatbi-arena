import { useEffect, useState } from 'react'
import FlowDiagram from './diagram/FlowDiagram.jsx'
import Leaderboard from './leaderboard/Leaderboard.jsx'
import Countdown from './countdown/Countdown.jsx'

const TABS = [
  ['countdown', '⏱ Countdown'],
  ['architecture', 'Architecture'],
  ['leaderboard', 'Leaderboard'],
]

export default function App() {
  const [tab, setTab] = useState('countdown')
  // mount the diagram the first time its tab is shown, so React Flow's fitView
  // measures a real (non-zero) container; keep it mounted after to preserve drags.
  const [archSeen, setArchSeen] = useState(false)
  useEffect(() => { if (tab === 'architecture') setArchSeen(true) }, [tab])
  return (
    <div className="app-shell">
      <header className="app-bar">
        <div className="brand">ChatBI&nbsp;<b>Arena</b></div>
        <nav className="tabs">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'tab active' : 'tab'}
              onClick={() => setTab(id)}>{label}</button>
          ))}
        </nav>
        <div className="app-sub">NL→SQL agent benchmark on ClickHouse</div>
      </header>
      <main className="app-body">
        {/* diagram + countdown stay mounted (preserve drag positions / keep the timer running) */}
        <div style={{ display: tab === 'architecture' ? 'block' : 'none', height: '100%' }}>
          {archSeen && <FlowDiagram />}
        </div>
        <div style={{ display: tab === 'countdown' ? 'block' : 'none', height: '100%' }}>
          <Countdown />
        </div>
        {tab === 'leaderboard' && <Leaderboard />}
      </main>
    </div>
  )
}
