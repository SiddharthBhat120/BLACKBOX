import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'

const LABELS = {
  bad_deploy: 'Bad deploy', db_pool_exhausted: 'Database pool exhausted', slow_dependency: 'Slow outside provider',
  config_change: 'Wrong config value', disk_full: 'Disk fills up',
}

export default function Home() {
  const [health, setHealth] = useState(null)
  const [list, setList] = useState([])
  const [scenario, setScenario] = useState('bad_deploy')
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 90) + 10)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [title, setTitle] = useState('')
  const file = useRef(null)

  const refresh = () => api.incidents().then(setList).catch((e) => setError(e.message))
  useEffect(() => { api.health().then(setHealth).catch(() => {}); refresh() }, [])

  async function run() {
    setBusy('run'); setError('')
    try {
      const inc = await api.runDemo(scenario, seed)
      window.location.hash = `#/incident/${inc.id}`
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  async function importData(f) {
    if (!f) return
    setBusy('import'); setError('')
    try {
      const inc = await api.create(title || f.name.replace(/\.zip$/i, ''))
      const res = await api.importBundle(inc.id, f)
      const accepted = res.reduce((n, r) => n + r.accepted, 0)
      if (!accepted) throw new Error('No events were found in that zip. See the README for the expected file names.')
      window.location.hash = `#/incident/${inc.id}`
    } catch (e) { setError(e.message) } finally { setBusy('') }
  }

  async function remove(id) {
    if (!window.confirm('Delete this recording and all its events?')) return
    await api.remove(id); refresh()
  }

  return (
    <main className="home">
      <section className="hero">
        <h1>Replay what broke, minute by minute.</h1>
        <p className="lede">
          BlackBox reads logs, alerts, deploys and team chat from an outage, squeezes tens of thousands of log lines
          into the moments that matter, ranks the changes that might have caused it, and drafts a postmortem where every
          sentence links to its evidence.
        </p>
      </section>

      <section className="console" aria-label="Start a recording">
        <div className="console-main">
          <h2>Break the demo shop on purpose</h2>
          <p className="muted">A small shop (gateway, orders, payments) runs for 70 simulated minutes. One fault is injected,
            next to two harmless decoy deploys. BlackBox then has to work out what happened.</p>
          <div className="scenarios" role="radiogroup" aria-label="Scenario">
            {health && Object.entries(health.scenarios).map(([key, desc]) => (
              <button key={key} role="radio" aria-checked={scenario === key}
                className={`scenario ${scenario === key ? 'is-on' : ''}`} onClick={() => setScenario(key)}>
                <span className="scenario-name">{LABELS[key] || key}</span>
                <span className="scenario-desc">{desc}</span>
              </button>
            ))}
          </div>
          <div className="run-row">
            <label className="seed">Run number
              <input type="number" min="1" max="9999" value={seed} onChange={(e) => setSeed(Number(e.target.value) || 1)} />
            </label>
            <button className="btn btn-primary" onClick={run} disabled={!!busy}>
              {busy === 'run' ? 'Injecting and recording…' : 'Inject the fault and record'}
            </button>
          </div>
        </div>
        <div className="console-side">
          <h2>Or bring your own data</h2>
          <p className="muted">A .zip with any of: <code>*.log</code> or <code>*.jsonl</code> logs, <code>alerts*.json</code> (Alertmanager),
            <code>deploys*.json</code>, and a <code>chat/</code> Slack export folder.</p>
          <input className="text" placeholder="Incident name (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
          <button className="btn" onClick={() => file.current?.click()} disabled={!!busy}>
            {busy === 'import' ? 'Importing…' : 'Choose a .zip'}
          </button>
          <input ref={file} type="file" accept=".zip" hidden onChange={(e) => importData(e.target.files[0])} />
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </section>

      <section className="recordings">
        <h2>Recordings</h2>
        {list.length === 0 && <p className="muted">No incidents yet. Inject a fault above to make the first recording.</p>}
        <ul>
          {list.map((i) => (
            <li key={i.id}>
              <a href={`#/incident/${i.id}`} className="rec">
                <span className={`dot dot-${i.status}`} aria-hidden="true" />
                <span className="rec-title">{i.title}</span>
                <span className="rec-meta">{i.event_count.toLocaleString()} events</span>
                <span className="rec-meta">{new Date(i.created_at).toLocaleString()}</span>
              </a>
              <button className="icon-btn" onClick={() => remove(i.id)} aria-label={`Delete ${i.title}`}>Delete</button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
