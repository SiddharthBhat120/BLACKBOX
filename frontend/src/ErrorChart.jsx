import { useMemo } from 'react'

const COLORS = ['#F26B1D', '#4FB3A9', '#A48BF0', '#6CB3E8', '#E5C07B', '#E5484D']

// Error rate per service per minute, drawn for the same time window the timeline shows.
export default function ErrorChart({ metrics, window: win, marks }) {
  const W = 1000, H = 120, PAD = 6
  const services = Object.keys(metrics)
  const { lines, maxRate } = useMemo(() => {
    const [a, b] = win
    let max = 0.05
    const lines = services.map((svc) => {
      const pts = metrics[svc].filter((p) => { const t = new Date(p.t).getTime(); return t >= a - 60000 && t <= b + 60000 })
      pts.forEach((p) => { max = Math.max(max, p.error_rate) })
      return { svc, pts }
    })
    return { lines, maxRate: max }
  }, [metrics, win, services])
  const x = (t) => ((new Date(t).getTime() - win[0]) / (win[1] - win[0])) * W
  const y = (r) => H - PAD - (r / maxRate) * (H - 2 * PAD)

  return (
    <figure className="chart">
      <figcaption>
        Error rate per minute
        <span className="legend">
          {lines.map(({ svc }, i) => <span key={svc}><i style={{ background: COLORS[i % COLORS.length] }} />{svc}</span>)}
        </span>
        <span className="scale">peak {(maxRate * 100).toFixed(0)}%</span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Error rate per service per minute">
        <line x1="0" x2={W} y1={y(0.05)} y2={y(0.05)} className="threshold" />
        {marks.map((m) => <line key={m.label} x1={x(m.t)} x2={x(m.t)} y1="0" y2={H} className={`mark mark-${m.kind}`} />)}
        {lines.map(({ svc, pts }, i) => (
          <polyline key={svc} fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth="2" vectorEffect="non-scaling-stroke"
            points={pts.map((p) => `${x(p.t).toFixed(1)},${y(p.error_rate).toFixed(1)}`).join(' ')} />
        ))}
      </svg>
    </figure>
  )
}
