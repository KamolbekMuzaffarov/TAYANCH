// Hand-made SVG charts for the admin panel: one day-axis chart (stacked bars + lines + a reference line) and bar lists.
import { useEffect, useRef, useState } from 'react'
import { fmtDay, fmtNum } from '../auth/parts'

export interface BarSeries { key: string; label: string; color: string; values: number[] }
export interface LineSeries { key: string; label: string; color: string; values: (number | null)[]; area?: boolean }

function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setW(Math.round(el.clientWidth))
    measure()
    if (typeof ResizeObserver === 'undefined') { window.addEventListener('resize', measure); return () => window.removeEventListener('resize', measure) }
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

/** Rounds a maximum up to 1 / 2 / 2.5 / 5 × 10ⁿ so the axis has clean labels. */
function niceMax(v: number): number {
  if (v <= 0) return 4
  const pow = Math.pow(10, Math.floor(Math.log10(v)))
  for (const m of [1, 2, 2.5, 4, 5, 10]) if (v <= m * pow) return Math.max(4, m * pow)
  return 10 * pow
}

export function DayChart({ days, bars = [], lines = [], reference, unit = '', height = 220, label }: {
  days: string[]; bars?: BarSeries[]; lines?: LineSeries[]; reference?: { value: number; label: string }
  unit?: string; height?: number; label: string
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const n = days.length
  const m = { l: 38, r: 10, t: 12, b: 26 }
  const iw = Math.max(10, width - m.l - m.r), ih = height - m.t - m.b
  const stackMax = n ? Math.max(0, ...days.map((_, i) => bars.reduce((a, s) => a + (s.values[i] ?? 0), 0))) : 0
  const lineMax = Math.max(0, ...lines.flatMap((s) => s.values.filter((v): v is number => v !== null)))
  const max = niceMax(Math.max(stackMax, lineMax, reference ? reference.value * 1.15 : 0))
  const band = n ? iw / n : iw
  const x = (i: number) => m.l + band * (i + 0.5)
  const y = (v: number) => m.t + ih - (v / max) * ih
  const bw = Math.max(2, Math.min(26, band * 0.62))
  const ticks = [0, max / 2, max]
  const labelEvery = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 64))))
  const empty = n === 0 || (stackMax === 0 && lineMax === 0)

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!n) return
    const box = e.currentTarget.getBoundingClientRect()
    const i = Math.floor((e.clientX - box.left - m.l) / band)
    setHover(i >= 0 && i < n ? i : null)
  }
  const tipLeft = hover === null ? 0 : Math.min(Math.max(x(hover), 84), Math.max(84, width - 84))

  return (
    <div className="ad-chart" ref={ref} style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label} onPointerMove={onMove} onPointerLeave={() => setHover(null)} onPointerDown={onMove}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={m.l} x2={width - m.r} y1={y(t)} y2={y(t)} className={t === 0 ? 'ad-axis' : 'ad-grid'} />
              <text x={m.l - 8} y={y(t) + 4} textAnchor="end" className="ad-tick">{fmtNum(t, Number.isInteger(t) ? 0 : 1)}</text>
            </g>
          ))}
          {hover !== null && <rect x={m.l + band * hover} y={m.t} width={band} height={ih} className="ad-hover" />}
          {days.map((d, i) => {
            let acc = 0
            return (
              <g key={d + i}>
                {bars.map((s, si) => {
                  const v = s.values[i] ?? 0
                  if (v <= 0) return null
                  const y1 = y(acc + v), h = y(acc) - y1
                  acc += v
                  // 1px surface gap between stacked segments keeps them readable without borders
                  return <rect key={s.key} x={x(i) - bw / 2} y={y1 + (si > 0 ? 0 : 0)} width={bw} height={Math.max(1, h - (si < bars.length - 1 ? 1 : 0))} rx={Math.min(3, bw / 2)} fill={s.color} />
                })}
                {i % labelEvery === 0 && <text x={x(i)} y={height - 7} textAnchor="middle" className="ad-tick">{fmtDay(d)}</text>}
              </g>
            )
          })}
          {reference && (
            <g>
              <line x1={m.l} x2={width - m.r} y1={y(reference.value)} y2={y(reference.value)} className="ad-ref" />
              <text x={width - m.r} y={y(reference.value) - 6} textAnchor="end" className="ad-ref-t">{reference.label}</text>
            </g>
          )}
          {lines.map((s) => {
            const segs: string[] = []
            let open = false, areaD = '', first = -1, last = -1
            s.values.forEach((v, i) => {
              if (v === null) { open = false; return }
              segs.push(`${open ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
              if (first < 0) first = i
              last = i
              open = true
            })
            const points = s.values.filter((v) => v !== null).length
            if (s.area && points > 1 && !s.values.slice(first, last + 1).includes(null)) areaD = `${segs.join(' ')} L${x(last).toFixed(1)} ${y(0)} L${x(first).toFixed(1)} ${y(0)} Z`
            return (
              <g key={s.key}>
                {areaD && <path d={areaD} fill={s.color} opacity="0.1" />}
                <path d={segs.join(' ')} fill="none" stroke={s.color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
                {(points <= 14 || hover !== null) && s.values.map((v, i) => (v === null || (points > 14 && i !== hover) ? null
                  : <circle key={i} cx={x(i)} cy={y(v)} r={i === hover ? 4.5 : 3} fill={s.color} stroke="var(--dot-bg)" strokeWidth="1.5" />))}
              </g>
            )
          })}
        </svg>
      )}
      {empty && width > 0 && <div className="ad-chart-empty small muted">Bu davrda maʼlumot yoʻq</div>}
      {hover !== null && !empty && (
        <div className="ad-tip" style={{ left: tipLeft }} role="status">
          <div className="ad-tip-d">{fmtDay(days[hover])}</div>
          {[...bars, ...lines].map((s) => {
            const v = s.values[hover]
            return (
              <div key={s.key} className="ad-tip-r">
                <i style={{ background: s.color }} /><span className="grow">{s.label}</span>
                <b className="num">{v === null || v === undefined ? '—' : `${fmtNum(v, Number.isInteger(v) ? 0 : 1)}${unit ? ` ${unit}` : ''}`}</b>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function Legend({ items }: { items: { label: string; color: string; line?: boolean; dashed?: boolean }[] }) {
  return (
    <div className="ad-legend">
      {items.map((it) => (
        <span key={it.label}><i className={it.dashed ? 'dash' : it.line ? 'line' : ''} style={it.dashed ? { color: it.color } : { background: it.color }} />{it.label}</span>
      ))}
    </div>
  )
}

/** Horizontal glass bars: label, count, share of the largest row. */
export function BarList({ items, color = 'var(--ink)' }: { items: { code: string; label: string; count: number }[]; color?: string }) {
  const max = Math.max(1, ...items.map((i) => i.count))
  if (!items.length) return <p className="small muted">Maʼlumot yoʻq</p>
  return (
    <ul className="ad-bars">
      {items.map((it) => (
        <li key={it.code}>
          <div className="ad-bars-top"><span className="grow">{it.label}</span><b className="num">{fmtNum(it.count)}</b></div>
          <div className="ad-bars-track"><span style={{ width: `${Math.max(2, (it.count / max) * 100)}%`, background: color }} /></div>
        </li>
      ))}
    </ul>
  )
}

/** Inline mini bar for table cells. */
export function MiniBar({ value, max, color }: { value: number; max: number; color: string }) {
  return <span className="ad-mini" aria-hidden><span style={{ width: `${max > 0 ? Math.max(value > 0 ? 4 : 0, (value / max) * 100) : 0}%`, background: color }} /></span>
}
