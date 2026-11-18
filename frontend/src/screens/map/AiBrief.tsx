// "AI xulosa": a calm, optional summary of the current scope. Hidden entirely when the backend lacks it.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type AiBrief, type BriefScope } from '../../lib/api'
import { Icon } from '../../ui/icons'

const FIELD_WORD: Record<string, string> = {
  pregnant: 'Kuzatuvdagi homiladorlar soni', red: 'Qizil zonadagilar soni', yellow: 'Sariq zonadagilar soni',
  green: 'Yashil zonadagilar soni', grey: 'Maʼlumotsizlar soni', newborns: 'Chaqaloqlar soni',
  waiting_mothers: 'Javob kutayotgan ayollar soni', waiting_newborns: 'Javob kutayotgan chaqaloqlar soni',
  overdue: 'Muddati oʻtgan signallar soni', overdue_alerts: 'Muddati oʻtgan signallar soni', open_alerts: 'Ochiq signallar soni',
  median_response_min: 'Oʻrtacha javob vaqti', answered_24h: '24 soatda javob berilganlar', answered_7d: '7 kunda javob berilganlar',
  district_name: 'Tuman nomi', region_name: 'Viloyat nomi', scope: 'Hudud darajasi', top_districts: 'Eng koʻp kutayotgan tumanlar',
}
const fieldWord = (f: string) => FIELD_WORD[f] ?? f.replace(/[_.]+/g, ' ')

let available: boolean | null = null
/** True once the backend is known to serve the AI endpoints. Probes the cheap policy endpoint, never the model. */
export function useAiAvailable(enabled = true): boolean {
  const [ok, setOk] = useState(available === true)
  useEffect(() => {
    if (!enabled || available === true) { setOk(enabled && available === true); return }
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const probe = () => {
      api.aiPrivacy().then(() => { available = true; if (alive) setOk(true) })
        .catch(() => { if (alive) timer = setTimeout(probe, 30000) })
    }
    probe()
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [enabled])
  return ok
}

export function Spark({ size = 16 }: { size?: number }) {
  return <Icon name="sparkles" size={size} />
}

const cache = new Map<string, AiBrief>()

/** The card body: text, source line, "Nima yuborildi?" disclosure, regenerate. Calls `onGone` if the feature is unavailable. */
export function BriefCard({ scope, id, title, onClose, onGone }: {
  scope: BriefScope; id: string | null; title: string; onClose?: () => void; onGone: () => void
}) {
  const key = `${scope}:${id ?? ''}`
  const [brief, setBrief] = useState<AiBrief | null>(() => cache.get(key) ?? null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const seq = useRef(0)
  const goneRef = useRef(onGone); goneRef.current = onGone

  const load = useCallback((force: boolean) => {
    const hit = !force ? cache.get(key) : undefined
    if (hit) { setBrief(hit); setFailed(false); setBusy(false); return }
    const n = ++seq.current
    setBusy(true); setFailed(false)
    api.aiBrief(scope, id).then((b) => {
      if (n !== seq.current) return
      if (!b || typeof b.text !== 'string' || !b.text.trim()) throw new Error('empty')
      const clean: AiBrief = { ...b, sent_fields: Array.isArray(b.sent_fields) ? b.sent_fields.map(String) : [] }
      cache.set(key, clean); setBrief(clean); setBusy(false)
    }).catch(() => {
      if (n !== seq.current) return
      setBusy(false)
      // nothing to show at all → the feature quietly disappears; otherwise keep the previous text
      if (cache.has(key)) setFailed(true); else goneRef.current()
    })
  }, [key, scope, id])

  useEffect(() => { setBrief(cache.get(key) ?? null); load(false); return () => { seq.current++ } }, [key, load])

  const external = brief?.source === 'claude-haiku'
  return (
    <div className="ai-card">
      <header className="ai-head">
        <span className="ai-title"><Spark />AI xulosa<span className="muted"> · {title}</span></span>
        {onClose && (
          <button className="ai-x" onClick={onClose} aria-label="Yopish">
            <Icon name="x" size={14} stroke={2.2} />
          </button>
        )}
      </header>

      {!brief ? (
        <div className="ai-shimmer" aria-busy="true" aria-label="Xulosa tayyorlanmoqda">
          <i className="skel" style={{ width: '96%' }} /><i className="skel" style={{ width: '88%' }} />
          <i className="skel" style={{ width: '93%' }} /><i className="skel" style={{ width: '54%' }} />
        </div>
      ) : (
        <div className={`ai-text${busy ? ' busy' : ''}`} aria-live="polite">
          {brief.text.split(/\n{2,}|\n(?=[-•])/).map((para, i) => <p key={i}>{para.trim()}</p>)}
        </div>
      )}

      {brief && (
        <footer className="ai-foot">
          <p className="tiny muted">
            {external ? 'Manba: Claude Haiku · faqat umumlashtirilgan sonlar yuborildi' : 'Mahalliy xulosa (tashqi xizmatsiz)'}
            {failed && ' · Yangilab boʻlmadi'}
          </p>
          <div className="ai-actions">
            <details className="ai-sent">
              <summary>Nima yuborildi?</summary>
              {external && brief.sent_fields.length > 0 ? (
                <ul>{brief.sent_fields.map((f) => <li key={f}>{fieldWord(f)}</li>)}</ul>
              ) : (
                <p>{brief.blocked ? 'Tashqi xizmatga yuborish toʻxtatildi. Xulosa shu serverda tuzildi.' : 'Hech narsa. Xulosa shu serverda tuzildi.'}</p>
              )}
              <p className="muted">Ism, manzil, telefon va tibbiy yozuvlar hech qachon yuborilmaydi.</p>
            </details>
            <button className="ai-again" onClick={() => load(true)} disabled={busy}>
              <Icon name="refresh" size={14} stroke={2} className={busy ? 'ai-spin' : undefined} />
              {busy ? 'Tayyorlanmoqda…' : 'Qayta tuzish'}
            </button>
          </div>
        </footer>
      )}
    </div>
  )
}

/** Hero pill + popover (national or zoomed region). Renders nothing until the backend is known to support it. */
export function AiBriefButton({ scope, id, title, enabled }: { scope: BriefScope; id: string | null; title: string; enabled: boolean }) {
  const ok = useAiAvailable(enabled)
  const [open, setOpen] = useState(false)
  const [gone, setGone] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    const down = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false) }
    window.addEventListener('keydown', key)
    window.addEventListener('mousedown', down)
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('mousedown', down) }
  }, [open])

  if (!ok || gone) return null
  return (
    <div className="ai-wrap" ref={wrap}>
      <button className={`glass-chip ai-pill${open ? ' on' : ''}`} onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-haspopup="dialog">
        <Spark />AI xulosa
      </button>
      {open && (
        <div className="ai-pop pop-in" role="dialog" aria-label="AI xulosa">
          <BriefCard scope={scope} id={id} title={title} onClose={() => setOpen(false)} onGone={() => { setOpen(false); setGone(true) }} />
        </div>
      )}
    </div>
  )
}

/** Inline variant for the district sheet: a quiet button that unfolds into the card. */
export function AiBriefInline({ id, title, enabled }: { id: string; title: string; enabled: boolean }) {
  const ok = useAiAvailable(enabled)
  const [open, setOpen] = useState(false)
  const [gone, setGone] = useState(false)
  if (!ok || gone) return null
  if (!open) return <div><button className="glass-chip ai-pill quiet" onClick={() => setOpen(true)}><Spark />AI xulosa</button></div>
  return <div className="ai-inline fade-in"><BriefCard scope="district" id={id} title={title} onClose={() => setOpen(false)} onGone={() => setGone(true)} /></div>
}
