// Shared UI kit. Screens import from here; do not duplicate these in screen folders.
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import type { Zone } from '../lib/api'
import { Icon, type IconName } from './icons'
import { setTheme, useTheme } from './theme'

export const ZONE_WORD: Record<Zone, string> = { red: 'Xavfli', yellow: 'Eʼtibor', green: 'Yaxshi', grey: 'Maʼlumot yoʻq' }
/** Theme-aware: these are CSS variables, usable as SVG fill / stroke. */
export const ZONE_COLOR: Record<Zone, string> = { red: 'var(--red)', yellow: 'var(--yellow)', green: 'var(--green)', grey: 'var(--grey)' }

/**
 * Zone marker: one small glossy "liquid orb" in the zone colour (grey = hollow ring).
 * `size` is the box the orb sits in (the orb itself fills ~62%), so it lines up with 16–20px icons and text.
 * The zone word is exposed as tooltip + accessible label; pass `label={null}` when the word is already next to it.
 */
export function ZoneIcon({ zone, size = 16, label }: { zone: Zone; size?: number; label?: string | null }) {
  const word = label === undefined ? ZONE_WORD[zone] : label
  const orb = Math.max(7, Math.round(size * 0.62))
  return (
    <span className="zone-ic" style={{ width: size, height: size }} title={word ?? undefined}
      role={word ? 'img' : undefined} aria-label={word ?? undefined} aria-hidden={word ? undefined : true}>
      <span className={`orb ${zone}`} style={{ '--s': `${orb}px` } as CSSProperties} />
    </span>
  )
}

const ORB_GLYPH: Record<Zone, IconName> = { red: 'bell', yellow: 'eye', green: 'check', grey: 'minus' }
/** Large glossy orb with a glyph inside — for verdicts and calm "all done" states. */
export function ZoneOrb({ zone, size = 72, icon }: { zone: Zone; size?: number; icon?: IconName }) {
  return (
    <span className={`orb orb-xl ${zone}`} style={{ '--s': `${size}px` } as CSSProperties} aria-hidden>
      <Icon name={icon ?? ORB_GLYPH[zone]} size={Math.round(size * 0.48)} stroke={size >= 56 ? 2 : 2.2} />
    </span>
  )
}

export function ZoneBadge({ zone, label }: { zone: Zone; label?: string }) {
  return (<span className={`zone ${zone}`}><ZoneIcon zone={zone} size={14} label={null} />{label ?? ZONE_WORD[zone]}</span>)
}

export function DemoBadge() { return <span className="demo-badge" title="Barcha bemorlar sunʼiy yaratilgan">DEMO<span className="demo-more"> · sunʼiy maʼlumot</span></span> }

/** Sun / moon switch. The label names the theme it switches to. */
export function ThemeToggle() {
  const theme = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  const label = next === 'dark' ? 'Tungi rejim' : 'Kunduzgi rejim'
  return (
    <button className="theme-btn" aria-label={label} title={label} onClick={() => setTheme(next)}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={18} />
    </button>
  )
}

export function BrandMark() {
  return <span className="brand-mark" aria-hidden><span className="brand-glyph" /></span>
}

/** Minimal line chart. `bands` draws threshold lines (e.g. 140 / 160). */
export function Sparkline({ values, width = 160, height = 44, color = 'var(--ink)', bands = [] as { value: number; color: string }[], dots = true }:
  { values: number[]; width?: number; height?: number; color?: string; bands?: { value: number; color: string }[]; dots?: boolean }) {
  if (!values.length) return <svg width={width} height={height} />
  const all = [...values, ...bands.map((b) => b.value)]
  const min = Math.min(...all), max = Math.max(...all)
  const pad = 6, span = max - min || 1
  const x = (i: number) => pad + (values.length === 1 ? (width - 2 * pad) / 2 : (i * (width - 2 * pad)) / (values.length - 1))
  const y = (v: number) => height - pad - ((v - min) / span) * (height - 2 * pad)
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  return (
    <svg width={width} height={height} role="img" aria-label={`Qiymatlar: ${values.join(', ')}`}>
      {bands.map((b, i) => (<line key={i} x1={0} x2={width} y1={y(b.value)} y2={y(b.value)} stroke={b.color} strokeWidth="1" strokeDasharray="3 3" opacity=".7" />))}
      {values.length > 1 && <path d={`${d} L${x(values.length - 1).toFixed(1)} ${height} L${x(0).toFixed(1)} ${height} Z`} fill={color} opacity=".07" />}
      <path d={d} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {dots && values.map((v, i) => (<circle key={i} cx={x(i)} cy={y(v)} r={i === values.length - 1 ? 3.6 : 2.2} fill={i === values.length - 1 ? color : 'var(--dot-bg)'} stroke={color} strokeWidth="1.6" />))}
    </svg>
  )
}

/** Re-renders every `ms` so countdowns stay live. */
export function useTick(ms = 1000) {
  const [, set] = useState(0)
  useEffect(() => { const t = setInterval(() => set((n) => n + 1), ms); return () => clearInterval(t) }, [ms])
}

/** Polls `fn` every `ms`; returns data, error, loading and a manual refresh. */
export function usePoll<T>(fn: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const ref = useRef(fn); ref.current = fn
  const refresh = async () => {
    try { setData(await ref.current()); setError(null) } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }
  useEffect(() => {
    let alive = true
    const run = async () => { if (alive) await refresh() }
    run()
    const t = ms > 0 ? setInterval(run, ms) : null
    return () => { alive = false; if (t) clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, error, loading, refresh }
}

/** "mm:ss" until `iso`; negative values are shown as overdue. */
export function countdown(iso: string): { text: string; overdue: boolean; seconds: number } {
  const s = Math.round((new Date(iso).getTime() - Date.now()) / 1000)
  const a = Math.abs(s)
  const p2 = (n: number) => String(n).padStart(2, '0')
  const text = a >= 3600 ? `${Math.floor(a / 3600)}:${p2(Math.floor((a % 3600) / 60))}:${p2(a % 60)}` : `${p2(Math.floor(a / 60))}:${p2(a % 60)}`
  return { text: s < 0 ? `+${text}` : text, overdue: s < 0, seconds: s }
}

export function timeAgo(iso: string): string {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (m < 1) return 'hozir'
  if (m < 60) return `${m} daqiqa oldin`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} soat oldin`
  return `${Math.round(h / 24)} kun oldin`
}

export function fmtDate(iso: string): string {
  const d = new Date(iso)
  const months = ['yan', 'fev', 'mar', 'apr', 'may', 'iyn', 'iyl', 'avg', 'sen', 'okt', 'noy', 'dek']
  return `${d.getDate()}-${months[d.getMonth()]}`
}

export function initials(name: string): string {
  return name.split(/\s+/).filter((w) => /^\p{L}/u.test(w)).slice(0, 2).map((w) => w[0]).join('').toUpperCase()
}

/** One calm line (403 / 404 / offline …) and, when it makes sense, a retry button. */
export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="card flat col center" role="alert" style={{ alignItems: 'center', gap: 14, padding: '22px 20px' }}>
      <span className="muted" style={{ display: 'grid' }}><Icon name="wifi-off" size={22} /></span>
      <p>{message || 'Server bilan aloqa yoʻq'}</p>
      {onRetry && <button className="btn small soft" onClick={onRetry}><Icon name="refresh" size={15} />Qayta urinish</button>}
    </div>
  )
}

export function Sheet({ open, onClose, children, width = 560 }: { open: boolean; onClose: () => void; children: ReactNode; width?: number }) {
  useEffect(() => {
    if (!open) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="sheet-back" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()} style={{ width: `min(${width}px, 100vw)` }}>
        {children}
      </div>
    </div>
  )
}
