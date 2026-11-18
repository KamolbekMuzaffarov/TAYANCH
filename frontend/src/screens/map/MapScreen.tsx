// Leader screen: one question — "Hozir qayerda yordam kechikyapti?"
import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent as RMouseEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  api, officialIndicators, subscribeEvents,
  type DistrictLive, type IndicatorRef, type LiveEvent, type MapData, type MapTotals, type OfficialDistrictRow, type OfficialDistricts,
  type RegionLive, type Zone,
} from '../../lib/api'
import { ErrorNote, Sheet, Sparkline, ZONE_COLOR, ZONE_WORD, ZoneBadge, ZoneIcon, countdown, timeAgo, usePoll, useTick } from '../../ui/kit'
import { TopBar } from '../../ui/TopBar'
import { FULL, H, W, loadGeo, shortRegion, uz, type Box, type Geo } from './geo'
import { mockApi, subscribeMockEvents } from './mock'
import { AiBriefButton, AiBriefInline } from './AiBrief'
import './map.css'

type Layer = 'live' | 'official'
/** What one shape on the country map stands for. */
type Level = 'region' | 'district'
const LEVEL_KEY = 'map-level'
const readLevel = (): Level => { try { return localStorage.getItem(LEVEL_KEY) === 'district' ? 'district' : 'region' } catch { return 'region' } }

/** Region colour = weighted mean of its districts' scores (green 0, yellow 1, red 2; grey is left out). */
const REGION_RULE = { score: { green: 0, yellow: 1, red: 2 } as Record<Exclude<Zone, 'grey'>, number>, yellowFrom: 0.15, redFrom: 0.6 }
interface RegionAgg { zone: Zone; red: number; yellow: number; green: number; grey: number; counted: boolean }
function aggregate(rows: Iterable<{ soato: string; status: Zone; weight: number }>): Map<string, RegionAgg> {
  const acc = new Map<string, { a: RegionAgg; ws: number; w: number; s: number; n: number }>()
  for (const r of rows) {
    let e = acc.get(r.soato)
    if (!e) { e = { a: { zone: 'grey', red: 0, yellow: 0, green: 0, grey: 0, counted: true }, ws: 0, w: 0, s: 0, n: 0 }; acc.set(r.soato, e) }
    e.a[r.status]++
    if (r.status === 'grey') continue
    const sc = REGION_RULE.score[r.status], w = Math.max(0, r.weight)
    e.ws += sc * w; e.w += w; e.s += sc; e.n++
  }
  const out = new Map<string, RegionAgg>()
  for (const [soato, e] of acc) {
    if (e.n > 0) {
      const mean = e.w > 0 ? e.ws / e.w : e.s / e.n // no weights at all → plain mean of the statuses
      e.a.zone = mean > REGION_RULE.redFrom ? 'red' : mean >= REGION_RULE.yellowFrom ? 'yellow' : 'green'
    }
    out.set(soato, e.a)
  }
  return out
}
interface Ripple { id: number; code: string; zone: Zone }

const STATUS_WORD: Record<Zone, string> = {
  red: 'Muddati oʻtgan signal bor', yellow: 'Javob kutilmoqda', green: 'Hammasi javoblangan', grey: 'Bemor yoʻq',
}
const ZONES: Zone[] = ['red', 'yellow', 'green', 'grey']
const TILE_WORD: Record<Zone, string> = { red: 'Qizil', yellow: 'Sariq', green: 'Yashil', grey: 'Maʼlumot yoʻq' }

const nf = (n: number) => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
/** "879 599", "12,58", "—": thousands are grouped, the decimal mark is a comma. */
const dec = (n: number | null) => {
  if (n == null) return '—'
  const [int, frac] = String(n).split('.')
  return int.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + (frac ? `,${frac}` : '')
}
/** Units such as "100 ming tugʻilishga" repeat what the label already says and read badly after a number. */
const unitOf = (u: string | null | undefined) => (u && !/^\d/.test(u.trim()) ? ` ${u.trim()}` : '')
/** The national reference line is drawn only when it shares the scale of the series (never for raw counts). */
function refBand(series: { value: number }[], national: number | null) {
  if (national == null || !series.length) return []
  const lo = Math.min(...series.map((p) => p.value)), hi = Math.max(...series.map((p) => p.value))
  const room = Math.max(hi - lo, Math.abs(hi) * 0.5, 1) * 2
  return national >= lo - room && national <= hi + room ? [{ value: national, color: 'var(--grey)' }] : []
}
const commaDecimals = (t: string) => t.replace(/(\d)\.(\d)/g, '$1,$2')
const clock = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : [d.getHours(), d.getMinutes(), d.getSeconds()].map((x) => String(x).padStart(2, '0')).join(':')
}
const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Smoothly counts from the previous value to the new one. */
function useCountUp(target: number, ms = 700) {
  const [v, setV] = useState(0)
  const cur = useRef(0)
  useEffect(() => {
    const from = cur.current
    if (from === target) return
    if (reducedMotion()) { cur.current = target; setV(target); return }
    let raf = 0
    const t0 = performance.now()
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms)
      const x = Math.round(from + (target - from) * (1 - Math.pow(1 - p, 3)))
      cur.current = x; setV(x)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}
function Num({ value }: { value: number }) { return <span className="num">{nf(useCountUp(value))}</span> }

// ------------------------------------------------------------------ map
// The country is drawn either as 14 whole regions or as all districts (the level switch); a click on a region
// zooms into it and shows its districts one by one.
// Geometry never changes after load — polls and events only swap class names on memoised paths.
const DPath = memo(function DPath({ d, code, cls }: { d: string; code: string; cls: string }) {
  return <path d={d} data-code={code} className={cls} />
})
const RPath = memo(function RPath({ d, soato, cls }: { d: string; soato: string; cls: string }) {
  return <path d={d} data-soato={soato} className={cls} />
})

const at = (x: number, y: number): CSSProperties => ({ transform: `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(var(--k, 1))` })


const ZOOM_MS = 360

interface CanvasProps {
  geo: Geo; live: Map<string, DistrictLive>; regions: Map<string, RegionLive>; layer: Layer
  level: Level; agg: Map<string, RegionAgg>
  viloyat: string | null; selected: string | null; ripples: Ripple[]; ready: boolean
  /** District-level official values; null → districts of the open region take the region's official colour. */
  od: Map<string, OfficialDistrictRow> | null; odInd: IndicatorRef | null
  onDistrict: (code: string) => void; onRegion: (soato: string) => void; onZoom: (soato: string | null) => void
}

const MapCanvas = memo(function MapCanvas({ geo, live, regions, layer, level, agg, viloyat, selected, ripples, ready, od, odInd, onDistrict, onRegion, onZoom }: CanvasProps) {
  const byDistrict = layer === 'official' && od !== null
  const svgRef = useRef<SVGSVGElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const boxRef = useRef<Box>(FULL)
  const hoverRef = useRef<string | null>(null)
  /** "d:<district code>" or "r:<region soato>" */
  const [hover, setHover] = useState<string | null>(null)
  const zoomed = viloyat && geo.regionBySoato.has(viloyat) ? viloyat : null

  // districts of the open region stay mounted while the camera flies back out, then leave the DOM
  const [held, setHeld] = useState<string | null>(zoomed)
  useEffect(() => {
    if (zoomed) { setHeld(zoomed); return }
    const t = setTimeout(() => setHeld(null), ZOOM_MS + 40)
    return () => clearTimeout(t)
  }, [zoomed])
  const shown = zoomed ?? held
  const bySoato = useMemo(() => {
    const m = new Map<string, typeof geo.districts>()
    for (const g of geo.districts) { const a = m.get(g.soato); if (a) a.push(g); else m.set(g.soato, [g]) }
    return m
  }, [geo])
  const shownDistricts = shown ? bySoato.get(shown) ?? [] : []

  // on a small map (phone, stacked layout) labels and markers are enlarged so they stay readable and tappable
  const [boost, setBoost] = useState(1)
  const boostRef = useRef(1)
  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return
    const measure = () => {
      const s = Math.min(wrap.clientWidth / W, wrap.clientHeight / H)
      const b = s > 0 ? Math.round(Math.min(2, Math.max(1, 0.72 / s)) * 20) / 20 : 1
      if (b === boostRef.current) return
      boostRef.current = b; setBoost(b)
      svgRef.current?.style.setProperty('--k', ((boxRef.current[2] / W) * b).toFixed(4))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(wrap)
    return () => ro.disconnect()
  }, [])

  // animated viewBox: written straight to the DOM, React never re-renders during the zoom
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const to = (zoomed && geo.regionBySoato.get(zoomed)?.box) || FULL
    const from = boxRef.current
    const apply = (b: Box) => {
      boxRef.current = b
      svg.setAttribute('viewBox', b.map((n) => n.toFixed(2)).join(' '))
      svg.style.setProperty('--k', ((b[2] / W) * boostRef.current).toFixed(4))
    }
    if (reducedMotion() || from.every((v, i) => Math.abs(v - to[i]) < 0.01)) { apply(to); return }
    let raf = 0
    const t0 = performance.now()
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ZOOM_MS)
      const e = 1 - Math.pow(1 - p, 3.4) // fast start, soft landing
      apply(from.map((v, i) => v + (to[i] - v) * e) as Box)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [zoomed, geo])

  const keyOf = (el: Element): string | null => {
    const code = el.getAttribute('data-code')
    if (code) {
      const g = geo.districtByCode.get(code)
      return zoomed && g && g.soato !== zoomed ? `r:${g.soato}` : `d:${code}`
    }
    const soato = el.getAttribute('data-soato')
    return soato && soato !== zoomed ? `r:${soato}` : null
  }
  const onMove = (e: RMouseEvent<SVGGElement>) => {
    const key = keyOf(e.target as Element)
    if (key !== hoverRef.current) { hoverRef.current = key; setHover(key) }
    const wrap = wrapRef.current, tip = tipRef.current
    if (!wrap || !tip) return
    const r = wrap.getBoundingClientRect()
    const x = e.clientX - r.left, y = e.clientY - r.top
    const flipX = x > r.width - 300, flipY = y > r.height - 150
    tip.style.transform = `translate(${x + (flipX ? -14 : 14)}px, ${y + (flipY ? -14 : 14)}px) translate(${flipX ? '-100%' : '0'}, ${flipY ? '-100%' : '0'})`
  }
  const onLeave = () => { hoverRef.current = null; setHover(null) }
  const onClick = (e: RMouseEvent<SVGGElement>) => {
    const el = e.target as Element
    const code = el.getAttribute('data-code'), soato = el.getAttribute('data-soato')
    if (code) {
      e.stopPropagation()
      const g = geo.districtByCode.get(code)
      if (g && g.soato !== zoomed) onZoom(g.soato) // country view: the first click always opens the region
      else if (layer === 'live' || byDistrict) onDistrict(code)
      else if (g) onRegion(g.soato)
    } else if (soato) {
      e.stopPropagation()
      if (soato !== zoomed) onZoom(soato)
    }
  }

  const hd = hover?.startsWith('d:') ? geo.districtByCode.get(hover.slice(2)) : undefined
  const hr = hover?.startsWith('r:') ? geo.regionBySoato.get(hover.slice(2)) : undefined
  const hl = hd ? live.get(hd.code) : undefined
  const ho = hd && byDistrict ? od?.get(hd.code) : undefined
  const unit = unitOf(odInd?.unit)
  const tipSoato = hd?.soato ?? hr?.soato
  const tipRegion = tipSoato ? regions.get(tipSoato) : undefined
  const tipRegionName = tipSoato ? (tipRegion?.name ? uz(tipRegion.name) : geo.regionBySoato.get(tipSoato)?.name ?? '') : ''
  const sg = selected ? geo.districtByCode.get(selected) : undefined

  // district names in the open region: busiest first, a name that would cover another one is nudged or left out
  const zoomLabels = useMemo(() => {
    const box = zoomed ? geo.regionBySoato.get(zoomed)?.box : null
    if (!zoomed || !box) return []
    const k = (box[2] / W) * boost
    const placed: { x: number; y: number; w: number }[] = []
    const out: { code: string; name: string; x: number; y: number }[] = []
    const list = (bySoato.get(zoomed) ?? [])
      .map((g) => ({ g, l: live.get(g.code) }))
      .sort((a, b) => (b.l?.open_alerts ?? 0) - (a.l?.open_alerts ?? 0) || (b.l?.pregnant ?? 0) - (a.l?.pregnant ?? 0))
    for (const { g, l } of list) {
      const name = l?.name ?? g.name
      const w = (name.length * 6.4 + 8) * k
      const base = g.cy + (layer === 'live' && l?.open_alerts ? 20 : 3) * k
      for (const dy of [0, 13, -13, 26]) {
        const y = base + dy * k
        if (placed.some((p) => Math.abs(p.y - y) < 12 * k && Math.abs(p.x - g.cx) < (p.w + w) / 2)) continue
        placed.push({ x: g.cx, y, w }); out.push({ code: g.code, name, x: g.cx, y })
        break
      }
    }
    return out
  }, [zoomed, geo, bySoato, live, layer, boost])

  // region names on the country map; a name sits under its "late" marker, and on a small map overlapping names are left out
  const regionLabels = useMemo(() => {
    const all = geo.regions.map((r) => {
      const city = r.soato === '1726'
      const late = level === 'region' && layer === 'live' && (regions.get(r.soato)?.overdue_alerts ?? 0) > 0
      return { soato: r.soato, name: r.name, short: r.short, city, x: r.cx, y: r.cy + (city ? -13 : late ? 21 : 4) * boost }
    })
    if (boost < 1.2) return all
    const placed: { x: number; y: number; w: number }[] = []
    return all
      .sort((a, b) => (regions.get(b.soato)?.open_alerts ?? 0) - (regions.get(a.soato)?.open_alerts ?? 0))
      .filter((t) => {
        const w = (t.short.length * 7 + 6) * boost
        if (placed.some((p) => Math.abs(p.y - t.y) < 13 * boost && Math.abs(p.x - t.x) < (p.w + w) / 2)) return false
        placed.push({ x: t.x, y: t.y, w }); return true
      })
  }, [geo, regions, boost, layer, level])

  return (
    <div className="mx-canvas" ref={wrapRef}>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className={`mx-svg${ready ? '' : ' loading'}${zoomed ? ' zoomed' : ''}`}
        role="img" aria-label="Oʻzbekiston viloyatlari va tumanlari xaritasi" onClick={() => { if (zoomed) onZoom(null) }}>
        <g onMouseMove={onMove} onMouseLeave={onLeave} onClick={onClick}>
          {level === 'region' && geo.regions.map((r) => {
            const state = !zoomed ? '' : r.soato === zoomed ? ' cur' : ' dim'
            return <RPath key={r.soato} d={r.d} soato={r.soato} cls={`rg ${layer === 'live' ? 'z' : 'o'}-${agg.get(r.soato)?.zone ?? 'grey'}${state}`} />
          })}
          <g key={level === 'district' ? 'all' : shown ?? 'none'} className={`mx-dg${level === 'district' || zoomed ? '' : ' out'}`}>
            {(level === 'district' ? geo.districts : shownDistricts).map((g) => {
              const z: Zone = layer === 'live' ? live.get(g.code)?.status ?? 'grey'
                : byDistrict ? od?.get(g.code)?.status ?? 'grey' : regions.get(g.soato)?.official_status ?? 'grey'
              const dim = zoomed && g.soato !== zoomed ? ' dim' : ''
              return <DPath key={g.code} d={g.d} code={g.code} cls={`dp ${layer === 'live' ? 'z' : byDistrict ? 'd' : 'o'}-${z}${dim}`} />
            })}
          </g>
        </g>
        <g className="mx-noev">
          {/* the open region is outlined by its own districts: the simplified region contour does not match them exactly */}
          {level === 'district' && geo.regions.map((r) => (r.soato === zoomed ? null : <path key={r.soato} d={r.d} className={`rb${zoomed ? ' dim' : ''}`} />))}
          {hr && <path d={hr.d} className="hp" />}
          {hd && (layer === 'live' || byDistrict) && <path d={hd.d} className="hp" />}
          {sg && sg.soato === zoomed && <path d={sg.d} className="sp" />}
        </g>

        {ready && layer === 'live' && (
          <g className="mx-noev">
            {/* country: one pulsing marker per region with a late signal */}
            {!zoomed && level === 'region' && geo.regions.map((r) => ((regions.get(r.soato)?.overdue_alerts ?? 0) > 0 ? (
              <g key={r.soato} style={at(r.cx, r.cy)}>
                <circle r="7" className="mk-ring" /><circle r="7" className="mk-ring delay" />
                <circle r="5.4" className="mk late" />
              </g>
            ) : null))}
            {/* open region: every district that is waiting for an answer */}
            {(zoomed || level === 'district') && (level === 'district' ? geo.districts : shownDistricts).map((g) => {
              const l = live.get(g.code)
              if (!l || l.open_alerts <= 0 || (zoomed && g.soato !== zoomed)) return null
              const late = l.overdue_alerts > 0
              return (
                <g key={g.code} style={at(g.cx, g.cy)} className="mk-in">
                  {late && <circle r="7" className="mk-ring" />}
                  {late && <circle r="7" className="mk-ring delay" />}
                  <circle r={late ? 5 : 3.6} className={late ? 'mk late' : 'mk'} />
                </g>
              )
            })}
          </g>
        )}
        <g className="mx-noev">
          {ripples.map((r) => {
            const g = geo.districtByCode.get(r.code)
            return g ? (<g key={r.id} style={at(g.cx, g.cy)}><circle r="26" className="rip" stroke={ZONE_COLOR[r.zone]} /><circle r="26" className="rip two" stroke={ZONE_COLOR[r.zone]} /></g>) : null
          })}
        </g>

        <g className="mx-labels">
          {!zoomed && regionLabels.map((r) => (
            <text key={r.soato} style={at(r.x, r.y)} className={`rl${r.city ? ' city' : ''}`}
              onClick={(e) => { e.stopPropagation(); onZoom(r.soato) }}>
              <title>{`${r.name} — tumanlarni koʻrish`}</title>{r.short}
            </text>
          ))}
          {zoomLabels.map((t) => <text key={t.code} style={at(t.x, t.y)} className="dl">{t.name}</text>)}
        </g>
      </svg>

      <div ref={tipRef} className={`mx-tip${hd || hr ? ' on' : ''}`} role="tooltip">
        {hr && (() => {
          const a = agg.get(hr.soato)
          const z = a?.zone ?? 'grey'
          return (
            <>
              <strong>{tipRegionName}</strong>
              <span className="mx-tip-zone"><ZoneIcon zone={z} size={14} />{layer === 'official' ? `Rasmiy holat: ${ZONE_WORD[z]}` : ZONE_WORD[z]}</span>
              {a?.counted && <span className="tiny num">Qizil {a.red} · Sariq {a.yellow} · Yashil {a.green} tuman{a.grey > 0 ? ` · ${a.grey} tasida maʼlumot yoʻq` : ''}</span>}
              {layer === 'live' && tipRegion && <span className="tiny num">Homiladorlar {tipRegion.pregnant} · Javob kutmoqda {tipRegion.open_alerts}{tipRegion.overdue_alerts > 0 ? ` · Kechikkan ${tipRegion.overdue_alerts}` : ''}</span>}
              <span className="tiny muted">Tumanlarni koʻrish uchun bosing</span>
            </>
          )
        })()}
        {hd && layer === 'live' && (
          <>
            <strong>{hl?.name ?? hd.name}</strong>
            <span className="tiny muted">{tipRegionName}</span>
            <span className="mx-tip-zone"><ZoneIcon zone={hl?.status ?? 'grey'} size={14} />{STATUS_WORD[hl?.status ?? 'grey']}</span>
            {hl && <span className="tiny num">Homiladorlar {hl.pregnant} · Qizil {hl.red} · Sariq {hl.yellow} · Javob kutmoqda {hl.open_alerts}</span>}
          </>
        )}
        {hd && byDistrict && (
          <>
            <strong>{ho?.name ? uz(ho.name) : hl?.name ?? hd.name}</strong>
            <span className="tiny muted">{tipRegionName}</span>
            <span className="mx-tip-zone"><ZoneIcon zone={ho?.status ?? 'grey'} size={14} />{ho && ho.value != null ? ZONE_WORD[ho.status] : 'Rasmiy maʼlumot yoʻq'}</span>
            {ho && ho.value != null && (
              <span className="tiny num">
                {odInd?.label ?? 'Qiymat'}: <b>{dec(ho.value)}</b>{unit}
                {ho.national != null && <> · Respublika: <b>{dec(ho.national)}</b>{unit}</>}
              </span>
            )}
          </>
        )}
        {hd && layer === 'official' && !byDistrict && (
          <>
            <strong>{tipRegionName}</strong>
            <span className="mx-tip-zone"><ZoneIcon zone={tipRegion?.official_status ?? 'grey'} size={14} />Rasmiy holat: {ZONE_WORD[tipRegion?.official_status ?? 'grey']}</span>
            <span className="tiny muted">Koʻrsatkichlar uchun bosing</span>
          </>
        )}
      </div>
    </div>
  )
})

// ------------------------------------------------------------------ hero + rail
function Hero({ totals }: { totals: MapTotals | null }) {
  if (!totals) return <div className="mx-hero"><div className="skel" style={{ height: '1.1em', width: 'min(780px, 90%)' }} /></div>
  const waiting = totals.waiting_mothers + totals.waiting_newborns
  return (
    <h1 className="mx-hero" aria-live="polite">
      {waiting === 0 ? (
        <span className="mx-allok"><ZoneIcon zone="green" size={40} />Barcha signallarga javob berilgan</span>
      ) : (
        <span>
          <span className="q">Hozir javob kutayotganlar: </span>
          <span className="n"><Num value={totals.waiting_mothers} /> ayol</span>
          <span className="q">, </span>
          <span className="n"><Num value={totals.waiting_newborns} /> chaqaloq</span>
        </span>
      )}
      {totals.overdue > 0 && (
        <span className="mx-pill"><ZoneIcon zone="red" size={18} /><span><Num value={totals.overdue} /> tasi muddatidan oʻtgan</span></span>
      )}
    </h1>
  )
}

const Kpis = memo(function Kpis({ totals }: { totals: MapTotals | null }) {
  const tiles: { label: string; value: number | null; unit?: string; zone?: Zone }[] = [
    { label: 'Kuzatuvdagi homiladorlar', value: totals?.pregnant ?? null },
    { label: 'Qizil zonada', value: totals?.red ?? null, zone: 'red' },
    { label: 'Oʻrtacha javob vaqti', value: totals ? totals.median_response_min : null, unit: 'daq' },
    { label: '24 soatda javob berilgan', value: totals?.answered_24h ?? null },
  ]
  return (
    <div className="mx-kpis">
      {tiles.map((t) => (
        <div key={t.label} className="mx-kpi">
          <span className="mx-kpi-v">
            {!totals ? <span className="skel" style={{ display: 'inline-block', width: 72, height: 30 }} />
              : t.value == null ? '—' : <><Num value={t.value} />{t.unit && <small> {t.unit}</small>}</>}
            {t.zone && totals && <ZoneIcon zone={t.zone} size={16} />}
          </span>
          <span className="mx-kpi-l">{t.label}</span>
        </div>
      ))}
    </div>
  )
})

const Ranking = memo(function Ranking({ data, geo, onOpen }: { data: MapData | null; geo: Geo | null; onOpen: (code: string) => void }) {
  const rows = useMemo(() => (data?.districts ?? [])
    .filter((d) => d.open_alerts > 0)
    .sort((a, b) => b.overdue_alerts - a.overdue_alerts || b.open_alerts - a.open_alerts || a.name.localeCompare(b.name))
    .slice(0, 8), [data])
  const max = Math.max(1, ...rows.map((r) => r.open_alerts))
  const regionName = (soato: string) => {
    const n = data?.regions.find((r) => r.soato === soato)?.name ?? geo?.regionBySoato.get(soato)?.name ?? ''
    return shortRegion(uz(n))
  }
  return (
    <section className="mx-panel">
      <h3 className="mx-h">Javob kutayotgan tumanlar</h3>
      {!data ? <div className="skel" style={{ height: 120 }} />
        : rows.length === 0 ? <p className="mx-calm"><ZoneIcon zone="green" size={18} />Hozir hech bir tuman javob kutmayapti</p>
          : (
            <ol className="mx-rank">
              {rows.map((d) => (
                <li key={d.code}>
                  <button onClick={() => onOpen(d.code)} title={`${d.name} — batafsil`}>
                    <ZoneIcon zone={d.overdue_alerts > 0 ? 'red' : 'yellow'} size={15} />
                    <span className="mx-rank-name"><b>{d.name}</b><span className="tiny muted">{regionName(d.region_soato)}</span></span>
                    <span className="mx-bar" aria-hidden>
                      <i className="late" style={{ width: `${(d.overdue_alerts / max) * 100}%` }} />
                      <i style={{ width: `${((d.open_alerts - d.overdue_alerts) / max) * 100}%` }} />
                    </span>
                    <span className="mx-rank-n num">
                      <b>{d.open_alerts}</b>
                      {d.overdue_alerts > 0 && <span className="late">{d.overdue_alerts} kechikkan</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
    </section>
  )
})

const Feed = memo(function Feed({ events, onOpen }: { events: LiveEvent[]; onOpen: (code: string) => void }) {
  useTick(15000)
  return (
    <section className="mx-panel mx-feed">
      <h3 className="mx-h">Jonli lenta</h3>
      {events.length === 0 ? <p className="small muted">Hozircha hodisa yoʻq</p> : (
        <ul>
          {events.map((e) => (
            <li key={e.id}>
              <button onClick={() => onOpen(e.district_code)}>
                <ZoneIcon zone={e.zone} size={15} />
                <span className="grow">{e.text}</span>
                <time className="tiny muted num">{timeAgo(e.at)}</time>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
})

// ------------------------------------------------------------------ sheets
function SheetHead({ title, sub, onClose }: { title: string; sub?: string; onClose: () => void }) {
  return (
    <header className="mx-sh-head">
      <div className="grow"><h2>{title}</h2>{sub && <p className="muted">{sub}</p>}</div>
      <button className="mx-x" onClick={onClose} aria-label="Yopish">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden><path d="m2 2 10 10M12 2 2 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
      </button>
    </header>
  )
}

function DistrictSheet({ code, mock, fallbackName, onClose }: { code: string; mock: boolean; fallbackName: string; onClose: () => void }) {
  const { data, error, loading, refresh } = usePoll(() => (mock ? mockApi : api).district(code), 5000, [code, mock])
  useTick(1000)
  const official = useMemo(() => officialIndicators(data?.official), [data?.official])
  return (
    <div className="mx-sheet">
      <SheetHead title={data?.district.name ?? fallbackName} sub={data ? uz(data.district.region_name) : undefined} onClose={onClose} />
      {loading && !data && <div className="col"><div className="skel" style={{ height: 96 }} /><div className="skel" style={{ height: 72 }} /><div className="skel" style={{ height: 160 }} /></div>}
      {error && !data && <ErrorNote message={error} onRetry={refresh} />}
      {data && (
        <>
          <div className="mx-zones">
            {ZONES.map((z) => (
              <div key={z} className={`mx-zone ${z}`}>
                <ZoneIcon zone={z} size={18} />
                <b className="num">{data.counts[z]}</b>
                <span>{TILE_WORD[z]}</span>
              </div>
            ))}
          </div>
          <p className="small muted num">Homiladorlar {data.counts.pregnant} · Chaqaloqlar {data.counts.newborns} · Hamshiralar {data.nurses}</p>
          <AiBriefInline id={code} title={data.district.name} enabled={!mock} />

          <div className="mx-resp">
            <div><span className="muted small">Oʻrtacha javob</span><b className="num">{data.response.median_response_min == null ? '—' : `${data.response.median_response_min} daqiqa`}</b></div>
            <div><span className="muted small">7 kunda javob berilgan</span><b className="num">{data.response.answered_7d}</b></div>
          </div>

          <h3 className="mx-h" style={{ marginTop: 8 }}>Ochiq holatlar{data.cases.length > 0 && <span className="muted num"> · {data.cases.length}</span>}</h3>
          {data.cases.length === 0 ? <p className="mx-calm"><ZoneIcon zone="green" size={18} />Barcha signallarga javob berilgan</p> : (
            <ul className="mx-cases">
              {data.cases.map((c) => {
                const t = countdown(c.sla_due_at)
                return (
                  <li key={c.alert_id}>
                    <ZoneIcon zone={c.severity} size={18} />
                    <div className="grow col" style={{ gap: 2 }}>
                      <div className="row spread" style={{ gap: 8 }}>
                        <b className="num">{c.code}</b>
                        {c.status === 'acknowledged'
                          ? <span className="mx-cd ok">Qabul qilingan</span>
                          : <span className={`mx-cd num${t.overdue ? ' late' : ''}`}>{t.overdue ? `${t.text} kechikdi` : `${t.text} qoldi`}</span>}
                      </div>
                      <span>{c.summary}</span>
                      <span className="tiny muted">Masʼul: {c.responsible}{c.escalation_level > 0 ? ' · yuqoriga uzatilgan' : ''}</span>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
          <p className="tiny muted">Bemor ismlari bu yerda koʻrsatilmaydi.</p>

          {official.items.length > 0 && (
            <>
              <h3 className="mx-h" style={{ marginTop: 12 }}>Rasmiy statistika (SIAT){official.year != null && <span className="muted num"> · {official.year}</span>}</h3>
              <div className="mx-ofl">
                {official.items.map((it) => {
                  const s = [...it.series].sort((a, b) => a.year - b.year)
                  return (
                    <div key={it.code} className="mx-ofl-row">
                      <div className="grow col" style={{ gap: 1 }}>
                        <span className="small muted">{it.label}</span>
                        <span className="num"><b>{dec(it.value)}</b>{unitOf(it.unit)}
                          {it.national != null && <span className="small muted"> · Respublika: {dec(it.national)}</span>}</span>
                      </div>
                      {s.length > 1 && (
                        <Sparkline values={s.map((p) => p.value)} width={96} height={34} dots={false} bands={refBand(s, it.national)} />
                      )}
                    </div>
                  )
                })}
              </div>
              <p className="tiny muted">{official.source ? `Manba: ${official.source}. ` : ''}Bu hudud koʻrsatkichi, ayrim bemor bahosi emas.</p>
            </>
          )}
        </>
      )}
    </div>
  )
}

function OfficialSheet({ soato, mock, fallbackName, onClose }: { soato: string; mock: boolean; fallbackName: string; onClose: () => void }) {
  const { data, error, loading, refresh } = usePoll(() => (mock ? mockApi : api).regionOfficial(soato), 0, [soato, mock])
  return (
    <div className="mx-sheet">
      <SheetHead title={data ? uz(data.region.name) : fallbackName} sub={data ? `Rasmiy statistika · ${data.year}-yil` : 'Rasmiy statistika'} onClose={onClose} />
      {loading && !data && <div className="col"><div className="skel" style={{ height: 110 }} /><div className="skel" style={{ height: 110 }} /><div className="skel" style={{ height: 110 }} /></div>}
      {error && !data && <ErrorNote message={error} onRetry={refresh} />}
      {data && (
        <>
          <div><ZoneBadge zone={data.status} label={`Rasmiy holat: ${ZONE_WORD[data.status]}`} /></div>
          {data.indicators.length === 0 && <p className="muted">Bu hudud uchun koʻrsatkichlar yoʻq</p>}
          {data.indicators.map((it) => {
            const s = [...it.series].sort((a, b) => a.year - b.year)
            return (
              <div key={it.code} className="mx-ind">
                <div className="grow col" style={{ gap: 4 }}>
                  <span className="small muted">{it.label}</span>
                  <span className="mx-ind-v num">{dec(it.value)}{unitOf(it.unit) && <small>{unitOf(it.unit)}</small>}</span>
                  {it.national != null && <span className="small num">Respublika boʻyicha: <b>{dec(it.national)}</b>{unitOf(it.unit)}</span>}
                </div>
                {s.length > 1 && (
                  <div className="col" style={{ gap: 0, alignItems: 'stretch' }}>
                    <Sparkline values={s.map((p) => p.value)} width={170} height={56} dots={false} bands={refBand(s, it.national)} />
                    <div className="row spread tiny muted num"><span>{s[0].year}</span><span>{s[s.length - 1].year}</span></div>
                  </div>
                )}
              </div>
            )
          })}
          <p className="tiny muted">Manba: {data.source}. Punktir chiziq — respublika koʻrsatkichi.</p>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ screen
export default function MapScreen() {
  const [sp, setSp] = useSearchParams()
  const mock = sp.get('mock') === '1'
  const tuman = sp.get('tuman')
  const viloyat = sp.get('viloyat')

  const [geo, setGeo] = useState<Geo | null>(null)
  const [geoErr, setGeoErr] = useState<string | null>(null)
  const [geoTry, setGeoTry] = useState(0)
  useEffect(() => {
    let alive = true
    loadGeo().then((g) => { if (alive) { setGeo(g); setGeoErr(null) } }).catch((e: Error) => { if (alive) setGeoErr(e.message) })
    return () => { alive = false }
  }, [geoTry])

  const { data, error, refresh } = usePoll<MapData>(() => (mock ? mockApi : api).map(), 5000, [mock])
  const refreshRef = useRef(refresh); refreshRef.current = refresh

  const [layer, setLayer] = useState<Layer>('live')
  const [level, setLevelState] = useState<Level>(readLevel)
  const setLevel = useCallback((l: Level) => {
    setLevelState(l)
    try { localStorage.setItem(LEVEL_KEY, l) } catch { /* the choice lasts for this page only */ }
  }, [])
  const [official, setOfficial] = useState<string | null>(null)
  const [events, setEvents] = useState<LiveEvent[]>([])
  const [ripples, setRipples] = useState<Ripple[]>([])

  // district-level official layer: optional — while it is missing the region-level layer stays as it was
  const [od, setOd] = useState<OfficialDistricts | null>(null)
  const [odPick, setOdPick] = useState<{ indicator: string | null; year: number | null }>({ indicator: null, year: null })
  const [odBusy, setOdBusy] = useState(false)
  useEffect(() => {
    if (layer !== 'official' || mock) return
    let alive = true
    setOdBusy(true)
    api.officialDistricts(odPick.indicator, odPick.year)
      .then((r) => { if (alive) setOd(r) })
      .catch(() => { /* keep what is shown */ })
      .finally(() => { if (alive) setOdBusy(false) })
    return () => { alive = false }
  }, [layer, mock, odPick])
  const odOn = layer === 'official' && !mock && od !== null
  const odMap = useMemo(() => (od ? new Map(od.districts.map((d) => [d.code, d])) : null), [od])
  const odYearAt = od ? od.years.indexOf(od.year) : -1

  // feed: seed, then live stream; every event ripples on the map and triggers a debounced refresh
  useEffect(() => {
    let alive = true
    const timers = new Set<ReturnType<typeof setTimeout>>()
    let debounce: ReturnType<typeof setTimeout> | null = null
    const merge = (prev: LiveEvent[], add: LiveEvent[]) => {
      const seen = new Set(prev.map((e) => e.id))
      const fresh = add.filter((e) => e && !seen.has(e.id))
      return fresh.length ? [...fresh, ...prev].sort((a, b) => b.id - a.id).slice(0, 40) : prev
    }
    setEvents([])
    ;(mock ? mockApi : api).events(30).then((xs) => { if (alive && Array.isArray(xs)) setEvents((p) => merge(p, xs)) }).catch(() => { /* feed is optional */ })
    const unsub = (mock ? subscribeMockEvents : subscribeEvents)((e) => {
      if (!alive || !e || typeof e.id !== 'number') return
      setEvents((p) => merge(p, [e]))
      const rip: Ripple = { id: e.id, code: e.district_code, zone: e.zone }
      setRipples((p) => (p.some((r) => r.id === rip.id) ? p : [...p.slice(-5), rip]))
      const t = setTimeout(() => { timers.delete(t); if (alive) setRipples((p) => p.filter((r) => r.id !== rip.id)) }, 1900)
      timers.add(t)
      if (debounce) clearTimeout(debounce)
      debounce = setTimeout(() => { if (alive) void refreshRef.current() }, 400)
    })
    return () => { alive = false; unsub(); timers.forEach(clearTimeout); if (debounce) clearTimeout(debounce) }
  }, [mock])

  const spRef = useRef(setSp); spRef.current = setSp
  const patch = useCallback((p: Record<string, string | null>) => {
    spRef.current((prev) => {
      const n = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(p)) { if (v == null) n.delete(k); else n.set(k, v) }
      return n
    })
  }, [])
  const openDistrict = useCallback((code: string) => { setLayer('live'); patch({ tuman: code }) }, [patch])
  const peekDistrict = useCallback((code: string) => patch({ tuman: code }), [patch]) // keeps the current layer
  const closeDistrict = useCallback(() => patch({ tuman: null }), [patch])
  const zoomTo = useCallback((soato: string | null) => patch({ viloyat: soato }), [patch])
  const openOfficial = useCallback((soato: string) => setOfficial(soato), [])
  const closeOfficial = useCallback(() => setOfficial(null), [])

  const live = useMemo(() => new Map((data?.districts ?? []).map((d) => [d.code, d])), [data])
  const regions = useMemo(() => new Map((data?.regions ?? []).map((r) => [r.soato, r])), [data])
  const odWeighted = useMemo(() => !!od && od.districts.some((d) => (d.weight ?? 0) > 0), [od])
  // one colour per region, recomputed only when the data changes
  const agg = useMemo(() => {
    if (layer === 'live') return aggregate((data?.districts ?? []).map((d) => ({ soato: d.region_soato, status: d.status, weight: d.pregnant })))
    if (odOn && od) {
      const weighted = odWeighted
      return aggregate(od.districts.map((d) => ({
        soato: d.region_soato || geo?.districtByCode.get(d.code)?.soato || '', status: d.status, weight: weighted ? d.weight ?? 0 : 1,
      })))
    }
    return new Map<string, RegionAgg>((data?.regions ?? []).map((r) => [r.soato, { zone: r.official_status, red: 0, yellow: 0, green: 0, grey: 0, counted: false }]))
  }, [layer, data, odOn, od, odWeighted, geo])
  const regionName = (soato: string) => uz(regions.get(soato)?.name ?? '') || geo?.regionBySoato.get(soato)?.name || soato
  const zoomName = viloyat && (regions.has(viloyat) || geo?.regionBySoato.has(viloyat)) ? regionName(viloyat) : null

  const regionView = level === 'region' && !zoomName
  const ruleLine = layer === 'live' ? 'Viloyat rangi — tumanlar holatining homiladorlar soniga koʻra oʻrtachasi'
    : odWeighted ? 'Viloyat rangi — tumanlar holatining tugʻilishlar soniga koʻra oʻrtachasi' : 'Viloyat rangi — tumanlar holatining oʻrtachasi'

  // a district link (search, ranking, feed, ?tuman=) first flies to its region, then the sheet opens over it
  useEffect(() => {
    const soato = tuman && geo ? geo.districtByCode.get(tuman)?.soato : null
    if (!soato) return
    spRef.current((prev) => {
      if (prev.get('viloyat') === soato) return prev
      const n = new URLSearchParams(prev); n.set('viloyat', soato); return n
    }, { replace: true })
  }, [tuman, geo])

  // Esc: back to the whole country (sheets, the AI card and the palette handle their own Esc first)
  useEffect(() => {
    if (!viloyat) return
    const h = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || tuman || official) return
      if (document.querySelector('.cmdk-backdrop, .ai-pop')) return
      zoomTo(null)
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [viloyat, tuman, official, zoomTo])

  return (
    <div className="mx">
      <TopBar>
        <span className={`mx-live${error ? ' off' : ''}`} title={error ?? 'Maʼlumot har 5 soniyada yangilanadi'}>
          <i />{error ? 'Aloqa yoʻq' : 'Jonli'}
          {data && <span className="num muted">{clock(data.generated_at)}</span>}
        </span>
      </TopBar>

      <main className="mx-main">
        <div className="mx-top">
          <Hero totals={data?.totals ?? null} />
          <AiBriefButton scope={zoomName && viloyat ? 'region' : 'national'} id={zoomName ? viloyat : null}
            title={zoomName ?? 'Oʻzbekiston'} enabled={!mock && !!data} />
        </div>

        <div className="mx-grid">
          <section className="mx-map">
            <div className="mx-map-top">
              <nav className="mx-crumb" aria-label="Hudud">
                {zoomName ? (
                  <>
                    <button className="mx-back" onClick={() => zoomTo(null)} title="Butun mamlakatga qaytish (Esc)" aria-label="Oʻzbekiston xaritasiga qaytish">
                      <svg width="15" height="12" viewBox="0 0 15 12" aria-hidden><path d="M6 1 1 6l5 5M1.500 6H14" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
                      Oʻzbekiston
                    </button>
                    <span className="muted" aria-hidden>›</span>
                    <b>{zoomName}</b>
                  </>
                ) : <b>Oʻzbekiston</b>}
              </nav>
              <div className="mx-segs">
                <div className="mx-seg" role="tablist" aria-label="Xarita kesimi">
                  <button role="tab" aria-selected={level === 'region'} className={level === 'region' ? 'on' : ''} onClick={() => setLevel('region')}>Viloyat kesimida</button>
                  <button role="tab" aria-selected={level === 'district'} className={level === 'district' ? 'on' : ''} onClick={() => setLevel('district')}>Tuman kesimida</button>
                </div>
                <div className="mx-seg" role="tablist" aria-label="Xarita qatlami">
                  <button role="tab" aria-selected={layer === 'live'} className={layer === 'live' ? 'on' : ''} onClick={() => setLayer('live')}>Jonli holat</button>
                  <button role="tab" aria-selected={layer === 'official'} className={layer === 'official' ? 'on' : ''} onClick={() => setLayer('official')}>Rasmiy statistika</button>
                </div>
              </div>
              {odOn && od && (
                <div className={`mx-ofc fade-in${odBusy ? ' busy' : ''}`}>
                  {od.available_indicators.length > 1 ? (
                    <label className="mx-sel">
                      <span className="mx-sr">Koʻrsatkich</span>
                      <select value={od.indicator.code} onChange={(e) => setOdPick({ indicator: e.target.value, year: null })}>
                        {od.available_indicators.map((a) => <option key={a.code} value={a.code}>{a.label}</option>)}
                      </select>
                      <svg width="10" height="6" viewBox="0 0 10 6" aria-hidden><path d="m1 1 4 4 4-4" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </label>
                  ) : <span className="mx-sel static">{od.indicator.label}</span>}
                  <div className="mx-step" role="group" aria-label="Yil">
                    <button aria-label="Oldingi yil" disabled={odYearAt <= 0}
                      onClick={() => setOdPick({ indicator: od.indicator.code, year: od.years[odYearAt - 1] })}>
                      <svg width="7" height="11" viewBox="0 0 8 13" aria-hidden><path d="M6.5 1.5 1.5 6.5l5 5" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </button>
                    <b className="num">{od.year || '—'}</b>
                    <button aria-label="Keyingi yil" disabled={odYearAt < 0 || odYearAt >= od.years.length - 1}
                      onClick={() => setOdPick({ indicator: od.indicator.code, year: od.years[odYearAt + 1] })}>
                      <svg width="7" height="11" viewBox="0 0 8 13" aria-hidden><path d="m1.5 1.5 5 5-5 5" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </button>
                  </div>
                </div>
              )}
            </div>

            <div className="mx-map-body">
              {geoErr ? <div className="mx-center"><ErrorNote message={geoErr} onRetry={() => setGeoTry((n) => n + 1)} /></div>
                : !geo ? <div className="skel mx-map-skel" />
                  : (error && !data) ? (
                    <>
                      <MapCanvas geo={geo} live={live} regions={regions} layer="live" level={level} agg={agg} viloyat={null} selected={null} ripples={[]} ready={false}
                        od={null} odInd={null} onDistrict={openDistrict} onRegion={openOfficial} onZoom={zoomTo} />
                      <div className="mx-center over"><ErrorNote message={error} onRetry={refresh} /></div>
                    </>
                  ) : (
                    <MapCanvas geo={geo} live={live} regions={regions} layer={layer} level={level} agg={agg} viloyat={viloyat} selected={layer === 'live' || odOn ? tuman : null}
                      ripples={ripples} ready={!!data} od={odOn ? odMap : null} odInd={odOn && od ? od.indicator : null}
                      onDistrict={odOn ? peekDistrict : openDistrict} onRegion={openOfficial} onZoom={zoomTo} />
                  )}
              {layer === 'official' && !odOn && (
                <p className="mx-caption fade-in">Manba: SIAT, Milliy statistika qoʻmitasi. Tuman kesimida rasmiy maʼlumot yoʻq.</p>
              )}
            </div>

            <footer className="mx-legend">
              <ul>
                {ZONES.map((z) => (
                  <li key={z}><ZoneIcon zone={z} size={15} />{layer === 'live' && !regionView ? STATUS_WORD[z] : ZONE_WORD[z]}</li>
                ))}
              </ul>
              {odOn && od ? (
                <div className="tiny muted mx-src">
                  {regionView && <span>{ruleLine}</span>}
                  <span>
                    {od.source ? `Manba: ${od.source} · ` : ''}<span className="num">{od.matched} ta tuman moslashtirildi</span>
                    {od.unmatched > 0 && <span className="num">, {od.unmatched} tasi topilmadi</span>}
                  </span>
                  {od.method && <span className="mx-method" title={commaDecimals(od.method)}>{commaDecimals(od.method)}</span>}
                </div>
              ) : regionView && layer === 'live' ? (
                <p className="tiny muted">{ruleLine}</p>
              ) : <p className="tiny muted">Ikki qatlam mustaqil: hudud yashil boʻlsa ham, undagi bemor qizil boʻlishi mumkin.</p>}
            </footer>
          </section>

          <aside className="mx-rail">
            <Kpis totals={data?.totals ?? null} />
            <Ranking data={data} geo={geo} onOpen={openDistrict} />
            <Feed events={events} onOpen={openDistrict} />
          </aside>
        </div>
      </main>

      <Sheet open={!!tuman} onClose={closeDistrict} width={540}>
        {tuman && <DistrictSheet key={tuman} code={tuman} mock={mock} onClose={closeDistrict}
          fallbackName={live.get(tuman)?.name ?? geo?.districtByCode.get(tuman)?.name ?? 'Tuman'} />}
      </Sheet>
      <Sheet open={!!official} onClose={closeOfficial} width={540}>
        {official && <OfficialSheet key={official} soato={official} mock={mock} onClose={closeOfficial} fallbackName={regionName(official)} />}
      </Sheet>
    </div>
  )
}
