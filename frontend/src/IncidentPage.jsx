import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DataSet, Timeline, moment } from 'vis-timeline/standalone'
import { api, fmtMin, fmtTime } from './api.js'
import ErrorChart from './ErrorChart.jsx'

const LANES = [
  ['moments', 'Key moments'], ['deploy', 'Changes'], ['alert', 'Alerts'], ['log', 'Log patterns'], ['chat', 'Chat'],
]
const MOMENT_LABEL = {
  first_error: 'New error', error_spike: 'Error spike', latency_spike: 'Latency spike', new_pattern: 'New log pattern',
  first_human: 'First human', mitigation: 'Mitigation', recovery: 'Recovered',
}
const SEVS = ['debug', 'info', 'warning', 'error', 'critical']
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))

export default function IncidentPage({ id }) {
  const [inc, setInc] = useState(null)
  const [a, setA] = useState(null)
  const [truth, setTruth] = useState(null)
  const [chat, setChat] = useState([])
  const [notes, setNotes] = useState({})
  const [error, setError] = useState('')
  const [tab, setTab] = useState('timeline')
  const [selected, setSelected] = useState(null) // {kind: 'event'|'pattern', id}
  const [focusId, setFocusId] = useState(null)

  const loadNotes = useCallback(() => api.annotations(id).then(setNotes), [id])
  useEffect(() => {
    api.incident(id).then(setInc).catch((e) => setError(e.message))
    api.analysis(id).then(setA).catch((e) => setError(e.message))
    api.truth(id).then(setTruth).catch(() => {})
    api.events(id, { source: 'chat', limit: 500 }).then(setChat).catch(() => {})
    loadNotes()
  }, [id, loadNotes])

  const jumpTo = (eventId) => { setTab('timeline'); setSelected({ kind: 'event', id: eventId }); setFocusId(eventId + ':' + Date.now()) }

  if (error) return <main className="page"><p className="error">{error} <a href="#">Back to recordings</a></p></main>
  if (!a || !inc) return <main className="page"><p className="muted loading">Reading the recording…</p></main>

  return (
    <main className="page">
      <section className="head">
        <div>
          <p className="kicker">{inc.status === 'open' ? 'Open incident' : 'Resolved incident'}: {a.log_lines.toLocaleString()} log lines, {a.counts.alert} alerts, {a.counts.deploy} changes, {a.counts.chat} chat messages</p>
          <h1>{inc.title}</h1>
          {truth?.description && <p className="muted">{truth.description}</p>}
        </div>
        <Readouts a={a} />
      </section>

      <div className="tabs" role="tablist">
        {[['timeline', 'Timeline'], ['postmortem', 'Postmortem draft'], ['raw', 'Raw events']].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'is-on' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {tab === 'timeline' && (
        <div className="workspace">
          <div className="main-col">
            <TimelineView a={a} chat={chat} notes={notes} onSelect={setSelected} focusId={focusId} />
          </div>
          <aside className="side-col">
            {selected
              ? <Drawer incId={id} a={a} sel={selected} notes={notes} onClose={() => setSelected(null)} onSaved={loadNotes} />
              : <Triggers a={a} onJump={jumpTo} />}
            {truth?.scenario && <AnswerKey a={a} truth={truth} />}
          </aside>
        </div>
      )}
      {tab === 'postmortem' && <PostmortemView id={id} onJump={jumpTo} />}
      {tab === 'raw' && <RawEvents id={id} services={Object.keys(a.metrics)} onOpen={jumpTo} />}
    </main>
  )
}

function Readouts({ a }) {
  const cells = [
    ['Started', fmtTime(a.start), a.first_failing_service ? `in ${a.first_failing_service}` : ''],
    ['Detected', fmtTime(a.detection), a.time_to_detect_min != null ? `after ${fmtMin(a.time_to_detect_min)}` : ''],
    ['Mitigated', fmtTime(a.mitigation), a.time_to_mitigate_min != null ? `after ${fmtMin(a.time_to_mitigate_min)}` : ''],
    ['Recovered', fmtTime(a.recovery), a.time_to_recover_min != null ? `after ${fmtMin(a.time_to_recover_min)}` : ''],
    ['Squeezed', `${a.log_lines.toLocaleString()} → ${a.timeline_items}`, 'log lines → timeline items'],
  ]
  return (
    <dl className="readouts">
      {cells.map(([k, v, sub]) => (
        <div key={k} className="readout"><dt>{k}</dt><dd>{v}</dd><span>{sub}</span></div>
      ))}
    </dl>
  )
}

function TimelineView({ a, chat, notes, onSelect, focusId }) {
  const box = useRef(null)
  const tl = useRef(null)
  const items = useRef(new DataSet())
  const [win, setWin] = useState([new Date(a.window_start).getTime(), new Date(a.window_end).getTime()])
  const [services, setServices] = useState(() => new Set(Object.keys(a.metrics)))
  const [lanes, setLanes] = useState(() => new Set(LANES.map(([k]) => k)))
  const [minSev, setMinSev] = useState('debug')
  const [noise, setNoise] = useState(false)
  const [showHidden, setShowHidden] = useState(false)
  const topTrigger = a.triggers[0]?.event_id

  const all = useMemo(() => {
    const out = []
    const svcOk = (s) => !s || services.has(s)
    for (const m of a.key_moments) {
      const lane = m.kind === 'change' ? 'deploy' : m.kind.startsWith('alert') ? 'alert' : 'moments'
      const label = lane === 'moments' ? `${MOMENT_LABEL[m.kind]}${m.service ? ` in ${m.service}` : ''}` : m.title
      out.push({
        id: `m:${m.id}`, eventIds: m.event_ids, group: lane, start: m.ts, type: lane === 'moments' ? 'point' : 'box', service: m.service, sev: 'info',
        content: esc(label), title: esc(`${m.title}\n${m.reason}`),
        className: `k-${m.kind}${m.event_ids.includes(topTrigger) ? ' is-trigger' : ''}`,
      })
    }
    for (const p of a.patterns) {
      if (!noise && !['new', 'increased'].includes(p.change)) continue
      const end = new Date(p.last).getTime() - new Date(p.first).getTime() < 60000 ? new Date(new Date(p.first).getTime() + 60000).toISOString() : p.last
      out.push({
        id: `p:${p.pattern_id}`, patternId: p.pattern_id, eventIds: [p.first_event_id], group: 'log', start: p.change === 'new' ? p.first : p.first, end,
        type: 'range', service: p.service, sev: p.severity, content: esc(`${p.count.toLocaleString()}× ${p.template}`),
        title: esc(`${p.service}, ${p.change}: ${p.baseline_count} in baseline, ${p.incident_count} after\n${p.template}`),
        className: `pat pat-${p.change} sev-${p.severity}`,
      })
    }
    for (const c of chat) {
      out.push({
        id: `e:${c.id}`, eventIds: [c.id], group: 'chat', start: c.ts, type: 'box', service: '', sev: 'info',
        content: esc((c.raw.is_bot ? '🤖 ' : '') + c.message.slice(0, 48) + (c.message.length > 48 ? '…' : '')),
        title: esc(`${c.raw.role || c.raw.user}: ${c.message}`), className: c.raw.is_bot ? 'chat-bot' : 'chat-human',
      })
    }
    return out
      .filter((it) => lanes.has(it.group) && svcOk(it.service) && (it.group !== 'log' || SEVS.indexOf(it.sev) >= SEVS.indexOf(minSev)))
      .map((it) => {
        const ann = it.eventIds.map((e) => notes[e]).find(Boolean)
        if (ann?.hidden && !showHidden) return null
        return { ...it, className: `${it.className}${ann?.pinned ? ' is-pinned' : ''}${ann?.hidden ? ' is-hidden' : ''}`,
          content: (ann?.pinned ? '📌 ' : '') + it.content + (ann?.note ? ' ✎' : '') }
      })
      .filter(Boolean)
  }, [a, chat, notes, services, lanes, minSev, noise, showHidden, topTrigger])

  useEffect(() => {
    const t = new Timeline(box.current, items.current, LANES.map(([id, content]) => ({ id, content })), {
      stack: true, zoomMin: 60 * 1000, orientation: 'top', margin: { item: 6, axis: 4 },
      start: a.window_start, end: a.window_end, tooltip: { followMouse: true, overflowMethod: 'cap' },
      groupOrder: (x, y) => LANES.findIndex(([k]) => k === x.id) - LANES.findIndex(([k]) => k === y.id),
      maxHeight: 560, showCurrentTime: false, verticalScroll: true,
      moment: (date) => moment(date).utc(), // show UTC, like every other time on the page
    })
    t.on('rangechanged', ({ start, end }) => setWin([start.getTime(), end.getTime()]))
    tl.current = t
    // Open on the incident itself, with some context on each side.
    if (a.start) {
      const s = new Date(a.start).getTime(), e = new Date(a.recovery || a.window_end).getTime()
      const pad = Math.max(4 * 60000, (e - s) * 0.35)
      t.setWindow(s - pad * 1.6, e + pad, { animation: false })
      setWin([s - pad * 1.6, e + pad])
    }
    return () => t.destroy()
  }, [a])

  useEffect(() => {
    const t = tl.current
    if (!t) return
    t.off('select')
    t.on('select', ({ items: sel }) => {
      const it = all.find((x) => x.id === sel[0])
      if (!it) return
      onSelect(it.patternId ? { kind: 'pattern', id: it.patternId } : { kind: 'event', id: it.eventIds[0] })
    })
  }, [all, onSelect])

  useEffect(() => { items.current.clear(); items.current.add(all) }, [all])

  useEffect(() => {
    if (!focusId || !tl.current) return
    const evId = focusId.split(':')[0]
    const it = all.find((x) => x.eventIds.includes(evId))
    if (it) { tl.current.setSelection([it.id]); tl.current.focus(it.id, { animation: { duration: 400 } }) }
  }, [focusId, all])

  const toggle = (set, setter, key) => { const n = new Set(set); n.has(key) ? n.delete(key) : n.add(key); setter(n) }
  const marks = [a.start && { t: a.start, kind: 'start', label: 's' }, a.detection && { t: a.detection, kind: 'detect', label: 'd' },
    a.mitigation && { t: a.mitigation, kind: 'mit', label: 'm' }, a.recovery && { t: a.recovery, kind: 'rec', label: 'r' }].filter(Boolean)

  return (
    <>
      <div className="filters">
        <div className="chips" aria-label="Services">
          {Object.keys(a.metrics).map((s) => (
            <button key={s} className={`chip ${services.has(s) ? 'is-on' : ''}`} onClick={() => toggle(services, setServices, s)}>{s}</button>
          ))}
        </div>
        <div className="chips" aria-label="Lanes">
          {LANES.map(([k, l]) => (
            <button key={k} className={`chip lane-${k} ${lanes.has(k) ? 'is-on' : ''}`} onClick={() => toggle(lanes, setLanes, k)}>{l}</button>
          ))}
        </div>
        <label className="sel">Log severity
          <select value={minSev} onChange={(e) => setMinSev(e.target.value)}>
            {SEVS.map((s) => <option key={s} value={s}>{s} and above</option>)}
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={noise} onChange={(e) => setNoise(e.target.checked)} /> Show unchanged patterns (noise)</label>
        <label className="check"><input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> Show hidden</label>
      </div>
      <ErrorChart metrics={a.metrics} window={win} marks={marks} />
      <div className="timeline" ref={box} />
      <p className="hint">All times are UTC. Drag to move, scroll or pinch to zoom, click an item to inspect, pin or annotate it.</p>
    </>
  )
}

function Triggers({ a, onJump }) {
  const max = Math.max(...a.triggers.map((t) => t.score), 0.01)
  return (
    <section className="card">
      <h2>Trigger candidates</h2>
      <p className="muted small">Ranked by how close in time, whether the change hit the service that failed first, and whether that
        service behaved differently afterwards. Correlation, not proof.</p>
      <ol className="triggers">
        {a.triggers.slice(0, 5).map((t) => (
          <li key={t.rank} className={t.rank === 1 ? 'is-top' : ''}>
            <div className="trig-head">
              <span className="trig-rank">{t.rank}</span>
              <span className="trig-title">{t.title}</span>
            </div>
            <div className="bar" aria-label={`score ${t.score}`}><i style={{ width: `${(t.score / max) * 100}%` }} /><b>{t.score.toFixed(2)}</b></div>
            <ul className="evidence">{t.evidence.map((e) => <li key={e}>{e}</li>)}</ul>
            {t.evidence_ids.length > 0 && (
              <div className="ev-chips">{t.evidence_ids.map((e) => <button key={e} className="ev" onClick={() => onJump(e)}>{e}</button>)}</div>
            )}
          </li>
        ))}
      </ol>
      {a.service_order.length > 0 && (
        <p className="small order">Failure order: {a.service_order.map(([s, t]) => `${s} (${fmtTime(t)})`).join(' → ')}</p>
      )}
    </section>
  )
}

function Drawer({ incId, a, sel, notes, onClose, onSaved }) {
  const [events, setEvents] = useState([])
  const [note, setNote] = useState('')
  const pattern = sel.kind === 'pattern' ? a.patterns.find((p) => p.pattern_id === sel.id) : null
  const eventId = sel.kind === 'event' ? sel.id : pattern?.first_event_id
  const ann = notes[eventId] || { event_id: eventId, pinned: false, hidden: false, note: '' }

  useEffect(() => {
    setNote(ann.note || '')
    const q = pattern ? { pattern: pattern.pattern_id, limit: 12 } : { ids: sel.id, limit: 1 }
    api.events(incId, q).then(setEvents).catch(() => setEvents([]))
  }, [incId, sel.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (patch) => { await api.annotate(incId, { ...ann, note, ...patch, event_id: eventId }); onSaved() }
  const ev = events[0]

  return (
    <section className="card drawer">
      <div className="drawer-head">
        <h2>{pattern ? 'Log pattern' : ev ? `${ev.source} event` : 'Event'}</h2>
        <button className="icon-btn" onClick={onClose}>Close</button>
      </div>
      {pattern && (
        <>
          <p className="mono tpl">{pattern.template}</p>
          <p className="small">{pattern.service}, {pattern.severity}. <b>{pattern.change}</b>: {pattern.baseline_count} lines in the baseline, {pattern.incident_count} after it, {pattern.count.toLocaleString()} in total.</p>
          <ul className="samples mono">{events.map((e) => <li key={e.id}><span>{fmtTime(e.ts)}</span> {e.message}</li>)}</ul>
        </>
      )}
      {!pattern && ev && (
        <>
          <p className="small">{fmtTime(ev.ts)} UTC, {ev.service || 'no service'}, {ev.severity}. <code>{ev.id}</code></p>
          <p className="msg">{ev.message}</p>
          <details open><summary>Raw event</summary><pre className="mono raw">{JSON.stringify(ev.raw, null, 2)}</pre></details>
        </>
      )}
      <div className="ann">
        <button className={`btn ${ann.pinned ? 'btn-primary' : ''}`} onClick={() => save({ pinned: !ann.pinned })}>{ann.pinned ? 'Pinned' : 'Pin for postmortem'}</button>
        <button className="btn" onClick={() => save({ hidden: !ann.hidden })}>{ann.hidden ? 'Unhide' : 'Hide'}</button>
      </div>
      <label className="note">Note
        <textarea rows="2" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What does this event tell us?" />
      </label>
      <button className="btn" onClick={() => save({})}>Save note</button>
    </section>
  )
}

function AnswerKey({ a, truth }) {
  const tr = truth.trigger
  const top = a.triggers[0]
  const hit = tr ? top?.event_id && top.service === tr.service && top.title.includes(tr.version) : !top?.event_id
  const off = (x, y) => (x && y ? Math.abs(new Date(x) - new Date(y)) / 60000 : null)
  return (
    <details className="card answer">
      <summary>Answer key (what the chaos script really did)</summary>
      <ul className="small">
        <li><b>{hit ? 'Correct' : 'Missed'}:</b> true trigger {tr ? `${tr.service} ${tr.version} at ${fmtTime(tr.timestamp)}` : 'none (outside provider)'}.</li>
        <li><b>{a.first_failing_service === truth.root_service ? 'Correct' : 'Missed'}:</b> failure started in {truth.root_service}.</li>
        <li>Start off by {fmtMin(off(a.start, truth.start))}; recovery off by {fmtMin(off(a.recovery, truth.recovery))}.</li>
        <li>Decoys: {truth.decoys.map((d) => `${d.service} ${d.version}`).join(', ')}.</li>
      </ul>
    </details>
  )
}

const SECTION_TITLES = { summary: 'Summary', impact: 'Impact', timeline: 'Timeline', trigger_candidates: 'Trigger candidates',
  went_well: 'What went well', went_badly: 'What went badly', follow_ups: 'Follow-up actions' }

function PostmortemView({ id, onJump }) {
  const [pm, setPm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const load = (fn) => { setBusy(true); setErr(''); fn(id).then(setPm).catch((e) => setErr(e.message)).finally(() => setBusy(false)) }
  useEffect(() => load(api.postmortem), [id]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="pm">
      <div className="pm-bar">
        <p className="muted small">Blameless draft built from key moments and pinned events only. Every sentence cites event ids that were
          checked to exist; anything else is marked unverified. {pm && `Engine: ${pm.engine === 'llm' ? 'AI model' : 'template'}.`}</p>
        <button className="btn" onClick={() => load(api.regenerate)} disabled={busy}>{busy ? 'Drafting…' : 'Regenerate draft'}</button>
        <a className="btn btn-primary" href={api.mdUrl(id)}>Download Markdown</a>
      </div>
      {err && <p className="error">{err}</p>}
      {pm && (
        <article className="pm-doc">
          <h1>Postmortem: {pm.title}</h1>
          {pm.unverified > 0 && <p className="warn">{pm.unverified} sentence{pm.unverified === 1 ? ' has' : 's have'} no valid evidence and {pm.unverified === 1 ? 'is' : 'are'} marked below.</p>}
          {Object.entries(SECTION_TITLES).map(([k, title]) => (pm.sections[k]?.length ? (
            <section key={k}>
              <h2>{title}</h2>
              <ul>
                {pm.sections[k].map((s, i) => (
                  <li key={i} className={s.verified ? '' : 'unverified'}>
                    {s.text}
                    {!s.verified && <span className="tag">unverified</span>}
                    {s.evidence.map((e) => <button key={e} className="ev" onClick={() => onJump(e)} title="Show on the timeline">{e}</button>)}
                  </li>
                ))}
              </ul>
            </section>
          ) : null))}
        </article>
      )}
    </section>
  )
}

function RawEvents({ id, services, onOpen }) {
  const [f, setF] = useState({ source: '', service: '', severity: '', q: '' })
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState([])
  const LIMIT = 100
  useEffect(() => {
    const t = setTimeout(() => api.events(id, { ...f, limit: LIMIT, offset: page * LIMIT }).then(setRows).catch(() => setRows([])), 200)
    return () => clearTimeout(t)
  }, [id, f, page])
  const set = (k) => (e) => { setPage(0); setF({ ...f, [k]: e.target.value }) }
  return (
    <section className="raw-events">
      <div className="filters">
        <select value={f.source} onChange={set('source')} aria-label="Source"><option value="">All sources</option>{['log', 'alert', 'deploy', 'chat'].map((s) => <option key={s}>{s}</option>)}</select>
        <select value={f.service} onChange={set('service')} aria-label="Service"><option value="">All services</option>{services.map((s) => <option key={s}>{s}</option>)}</select>
        <select value={f.severity} onChange={set('severity')} aria-label="Severity"><option value="">Any severity</option>{SEVS.map((s) => <option key={s} value={s}>{s} and above</option>)}</select>
        <input className="text" placeholder="Search messages" value={f.q} onChange={set('q')} />
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Time (UTC)</th><th>Source</th><th>Service</th><th>Severity</th><th>Message</th></tr></thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.id} className={`sev-${e.severity}`} onClick={() => onOpen(e.id)}>
                <td className="mono">{fmtTime(e.ts)}</td><td>{e.source}</td><td>{e.service}</td><td>{e.severity}</td><td className="mono">{e.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="muted">No events match these filters.</p>}
      </div>
      <div className="pager">
        <button className="btn" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
        <span className="small">Rows {page * LIMIT + 1}–{page * LIMIT + rows.length}</span>
        <button className="btn" disabled={rows.length < LIMIT} onClick={() => setPage(page + 1)}>Next</button>
      </div>
    </section>
  )
}
