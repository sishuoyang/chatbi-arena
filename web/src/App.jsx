import { useState } from 'react'
import FlowDiagram from './diagram/FlowDiagram.jsx'
import Leaderboard from './leaderboard/Leaderboard.jsx'

const TABS = [
  ['architecture', 'Architecture'],
  ['leaderboard', 'Leaderboard'],
]

export default function App() {
  const [tab, setTab] = useState('architecture')
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
        {/* keep the diagram mounted to preserve drag positions when switching tabs */}
        <div style={{ display: tab === 'architecture' ? 'block' : 'none', height: '100%' }}>
          <FlowDiagram />
        </div>
        {tab === 'leaderboard' && <Leaderboard />}
      </main>
    </div>
  )
}
