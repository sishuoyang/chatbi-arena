import { useEffect, useMemo, useRef, useState } from 'react'
import './countdown.css'
import arenaImg from '../assets/arena.png'

const FIGHTERS = [
  ['Claude', '#46d160'], ['Qwen', '#22d3ee'], ['DeepSeek', '#4f8cff'],
  ['gpt-oss', '#ff9f1c'], ['Kimi', '#b06cff'],
]
const PRESETS = [5, 10, 15]
const rnd = (a, b) => a + Math.random() * (b - a)

export default function Countdown() {
  const [duration, setDuration] = useState(600) // seconds
  const [remaining, setRemaining] = useState(600)
  const [running, setRunning] = useState(false)
  const tick = useRef(null)

  useEffect(() => {
    if (!running) return
    tick.current = setInterval(() => setRemaining((r) => (r <= 1 ? 0 : r - 1)), 1000)
    return () => clearInterval(tick.current)
  }, [running])
  useEffect(() => { if (remaining === 0) setRunning(false) }, [remaining])

  const setPreset = (m) => { setDuration(m * 60); setRemaining(m * 60); setRunning(false) }
  const reset = () => { setRemaining(duration); setRunning(false) }
  const mm = String(Math.floor(remaining / 60)).padStart(2, '0')
  const ss = String(remaining % 60).padStart(2, '0')
  const live = remaining === 0

  // decorative particles (generated once)
  const embers = useMemo(() => Array.from({ length: 26 }, () => ({
    left: rnd(0, 100), dur: rnd(6, 13), delay: rnd(0, 9), drift: rnd(-40, 60), size: rnd(2, 5),
  })), [])
  const sparks = useMemo(() => Array.from({ length: 16 }, () => {
    const ang = rnd(0, Math.PI * 2), dist = rnd(40, 150)
    return { dx: Math.cos(ang) * dist, dy: Math.sin(ang) * dist, dur: rnd(0.9, 1.8), delay: rnd(0, 1.7) }
  }), [])
  const glints = useMemo(() => Array.from({ length: 10 }, () => ({
    left: rnd(15, 85), top: rnd(45, 80), dur: rnd(1.4, 3), delay: rnd(0, 2.5),
  })), [])
  const confetti = useMemo(() => Array.from({ length: 40 }, () => ({
    left: rnd(0, 100), dur: rnd(2.6, 5), delay: rnd(0, 3),
    color: FIGHTERS[Math.floor(rnd(0, FIGHTERS.length))][1],
  })), [])

  return (
    <div className={`cd-stage${live ? ' live' : ''}`}>
      <div className="cd-embers">
        {embers.map((e, i) => (
          <span key={i} className="cd-ember" style={{
            left: `${e.left}%`, width: e.size, height: e.size,
            animationDuration: `${e.dur}s`, animationDelay: `${e.delay}s`, '--drift': `${e.drift}px`,
          }} />
        ))}
      </div>
      {live && <div className="cd-live-flash" />}
      {live && confetti.map((c, i) => (
        <span key={i} className="cd-confetti" style={{
          left: `${c.left}%`, background: c.color, animationDuration: `${c.dur}s`, animationDelay: `${c.delay}s`,
        }} />
      ))}

      <div className="cd-inner">
        <div className="cd-kicker">ChatBI <b>Arena</b> <span className="dot">◆</span> Live NL→SQL Benchmark</div>

        {live
          ? <div className="cd-live-banner">⚔ The Arena is LIVE ⚔</div>
          : <h1 className="cd-headline">Guess who will win the&nbsp;arena?</h1>}

        <div className="cd-arena-wrap">
          <div className="cd-frame">
            <img src={arenaImg} alt="ChatBI Arena — models battle for NL→SQL supremacy" />
            <div className="cd-vfx">
              <div className="sheen" />
              <div className="beam b1" /><div className="beam b2" />
              <div className="beam b3" /><div className="beam b4" />
              <div className="clash" />
              {sparks.map((s, i) => (
                <span key={i} className="cd-spark" style={{
                  '--dx': `${s.dx}px`, '--dy': `${s.dy}px`,
                  '--dur': `${s.dur}s`, '--delay': `${s.delay}s`,
                }} />
              ))}
              {glints.map((g, i) => (
                <span key={i} className="cd-glint" style={{
                  left: `${g.left}%`, top: `${g.top}%`, '--dur': `${g.dur}s`, '--delay': `${g.delay}s`,
                }} />
              ))}
            </div>
          </div>
        </div>

        <div className="cd-count">
          <div className="lbl">{live ? 'It’s on — to the leaderboard!' : 'Demo starts in'}</div>
          <div className="cd-digits">{mm}<span className="col">:</span>{ss}</div>
        </div>

        <div className="cd-fighters">
          {FIGHTERS.map(([name, c]) => (
            <span key={name} className="cd-fighter" style={{ '--c': c }}>{name}</span>
          ))}
        </div>
        <div className="cd-bets">Place your bets — who takes the crown? 👑</div>

        <div className="cd-controls">
          {PRESETS.map((m) => (
            <button key={m} onClick={() => setPreset(m)}>{m}m</button>
          ))}
          <span className="sep" />
          <button className="go" onClick={() => (remaining === 0 ? reset() : setRunning((r) => !r))}>
            {running ? 'Pause' : remaining === 0 ? 'Reset' : 'Start'}
          </button>
          <button onClick={reset}>Reset</button>
        </div>
      </div>
    </div>
  )
}
