// Small shared pieces for the sign-in, account and admin screens.
import { useEffect, useRef, type ReactNode } from 'react'
import { Icon, type IconName } from '../../ui/icons'

export function Seg<T extends string | number>({ value, onChange, options, block = false, label }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[]; block?: boolean; label: string
}) {
  return (
    <div className={`ob-seg${block ? ' block' : ''}`} role="group" aria-label={label}>
      {options.map((o) => (
        <button key={String(o.value)} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  )
}

export function Note({ kind = 'info', icon, children }: { kind?: 'info' | 'err' | 'ok' | 'demo'; icon?: IconName; children: ReactNode }) {
  const name: IconName = icon ?? (kind === 'err' ? 'alert' : kind === 'ok' ? 'check-circle' : 'info')
  return (
    <div className={`ob-note ${kind === 'info' ? '' : kind}`} role={kind === 'err' ? 'alert' : 'status'}>
      <Icon name={name} size={18} />
      <div>{children}</div>
    </div>
  )
}

/** Six boxes: digits only, auto-advance, backspace steps back, a pasted code fills all boxes. */
export function CodeInput({ value, onChange, onComplete, disabled = false, autoFocus = true, label = 'Tasdiqlash kodi' }: {
  value: string; onChange: (v: string) => void; onComplete?: (v: string) => void; disabled?: boolean; autoFocus?: boolean; label?: string
}) {
  const refs = useRef<(HTMLInputElement | null)[]>([])
  useEffect(() => { if (autoFocus) refs.current[0]?.focus() }, [autoFocus])
  const set = (next: string, focus: number) => {
    const v = next.replace(/\D/g, '').slice(0, 6)
    onChange(v)
    refs.current[Math.max(0, Math.min(5, focus))]?.focus()
    if (v.length === 6 && v !== value) onComplete?.(v)
  }
  return (
    <div className="ob-code" role="group" aria-label={label}>
      {Array.from({ length: 6 }, (_, i) => (
        <input
          key={i} ref={(el) => { refs.current[i] = el }} className="ob-code-box ob-mono" value={value[i] ?? ''} disabled={disabled}
          inputMode="numeric" pattern="[0-9]*" autoComplete={i === 0 ? 'one-time-code' : 'off'} maxLength={6} aria-label={`${i + 1}-raqam`}
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => {
            let d = e.target.value.replace(/\D/g, '')
            if (d.length === 2 && value[i]) d = d[0] === value[i] ? d[1] : d[0]
            if (!d) { set(value.slice(0, i) + value.slice(i + 1), i); return }
            // one typed digit replaces this box; several (autofill, paste into one box) spread to the right
            const merged = d.length > 1 ? (value.slice(0, i) + d) : (value.slice(0, i) + d + value.slice(i + 1))
            set(merged, i + d.length)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Backspace' && !value[i] && i > 0) { e.preventDefault(); set(value.slice(0, i - 1) + value.slice(i), i - 1) }
            else if (e.key === 'ArrowLeft' && i > 0) { e.preventDefault(); refs.current[i - 1]?.focus() }
            else if (e.key === 'ArrowRight' && i < 5) { e.preventDefault(); refs.current[i + 1]?.focus() }
          }}
          onPaste={(e) => { e.preventDefault(); set(e.clipboardData.getData('text'), 5) }}
        />
      ))}
    </div>
  )
}

const MONTHS = ['yan', 'fev', 'mar', 'apr', 'may', 'iyn', 'iyl', 'avg', 'sen', 'okt', 'noy', 'dek']
const p2 = (n: number) => String(n).padStart(2, '0')
/** "18-noy 2026, 09:05" — empty string for a missing or broken date. */
export function fmtDateTime(iso: string | null | undefined, withYear = true): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getDate()}-${MONTHS[d.getMonth()]}${withYear ? ` ${d.getFullYear()}` : ''}, ${p2(d.getHours())}:${p2(d.getMinutes())}`
}
export function fmtDay(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : `${d.getDate()}-${MONTHS[d.getMonth()]}`
}
/** 879599 → "879 599" (narrow no-break spaces, Uzbek convention). */
export function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—'
  const fixed = Math.abs(n).toFixed(digits)
  const [int, frac] = fixed.split('.')
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return `${n < 0 ? '−' : ''}${grouped}${frac ? `,${frac}` : ''}`
}
