import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { subscribeEvents, type Inbox, type InboxItem } from '../../lib/api'
import { ErrorNote, ZoneIcon, ZoneOrb, countdown, timeAgo, usePoll, useTick } from '../../ui/kit'
import { Icon } from '../../ui/icons'
import { TopBar } from '../../ui/TopBar'
import { LIST_PATH, MOCK, ignoreKey, itemPath, src } from './data'
import { Story } from './Story'
import './inbox.css'

const RANK: Record<string, number> = { red: 0, yellow: 1, grey: 2, green: 3 }

// ---- soft two-note chime; only after the user has interacted with the page (browser autoplay rules)
let interacted = false
let audio: AudioContext | null = null
function chime() {
  if (!interacted) return
  try {
    audio ??= new AudioContext()
    const ctx = audio
    if (ctx.state === 'suspended') void ctx.resume()
    ;[[660, 0], [880, 0.16]].forEach(([freq, at]) => {
      const o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime + at
      o.type = 'sine'; o.frequency.value = freq
      g.gain.setValueAtTime(0.0001, t)
      g.gain.exponentialRampToValueAtTime(0.09, t + 0.03)
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.42)
      o.connect(g); g.connect(ctx.destination); o.start(t); o.stop(t + 0.45)
    })
  } catch { /* sound is optional */ }
}

function Sla({ item }: { item: InboxItem }) {
  useTick(1000)
  if (item.status === 'acknowledged') return <div className="ibx-sla calm"><b>{timeAgo(item.created_at)}</b></div>
  const c = countdown(item.sla_due_at)
  return (
    <div className={`ibx-sla${c.overdue ? ' over' : ''}`} aria-label={c.overdue ? `${c.text} kechikdi` : `Javobgacha ${c.text}`}>
      <b>{c.text}</b>
      <span>{c.overdue ? 'kechikdi' : 'qoldi'}</span>
    </div>
  )
}

function Card({ item, selected, cursor, fresh }: { item: InboxItem; selected: boolean; cursor: boolean; fresh: boolean }) {
  const p = item.patient
  const meta = [
    item.subject_type === 'newborn' ? 'Chaqaloq' : null,
    p.age != null ? `${p.age} yosh` : null,
    p.weeks != null ? `${p.weeks} hafta` : null,
    p.district_name,
  ].filter(Boolean).join(' · ')
  const cls = ['ibx-card', item.status === 'acknowledged' && 'quiet', selected && 'sel', cursor && !selected && 'cur', fresh && 'ibx-new'].filter(Boolean).join(' ')
  return (
    <Link to={itemPath(item)} className={cls} data-alert={item.alert_id} aria-current={selected ? 'true' : undefined}>
      <div className="ibx-card-main">
        <div className="ibx-card-top"><ZoneIcon zone={item.severity} size={18} /><span className="ibx-name">{p.name}</span></div>
        <span className="ibx-meta">{meta}</span>
        <p className="ibx-summary">{item.summary}</p>
        <div className="ibx-from">
          <span>{item.from}</span>
          {item.escalation_level > 0 && <span className="badge red"><Icon name="arrow-up" size={11} stroke={2.2} />Yuqoriga koʻtarildi</span>}
        </div>
      </div>
      <Sla item={item} />
    </Link>
  )
}

export default function InboxScreen() {
  const [phone, setPhone] = useState(() => window.matchMedia('(max-width: 599px)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 599px)')
    const on = () => setPhone(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  const nav = useNavigate()
  const loc = useLocation()
  const { data, error, loading, refresh } = usePoll<Inbox>(() => src.inbox(), 5000)

  // ---- selection from the URL
  const m = loc.pathname.match(/^\/mutaxassis\/(bemor|chaqaloq)\/([^/]+)\/?$/)
  const kind = m ? (m[1] === 'bemor' ? 'p' as const : 'n' as const) : null
  const subjectId = m ? decodeURIComponent(m[2]) : null
  const signal = new URLSearchParams(loc.search).get('signal')

  // ---- items: waiting first (red on top), acknowledged in a quieter group
  const [gone, setGone] = useState<Set<string>>(() => new Set())
  const { open, acked, order } = useMemo(() => {
    const items = (data?.items ?? []).filter((i) => !gone.has(i.alert_id))
    const open = items.filter((i) => i.status === 'open').sort((a, b) => (RANK[a.severity] ?? 9) - (RANK[b.severity] ?? 9))
    const acked = items.filter((i) => i.status !== 'open')
    return { open, acked, order: [...open, ...acked] }
  }, [data, gone])

  const selected = order.find((i) => (signal ? i.alert_id === signal : kind === 'p' ? i.pregnancy_id === subjectId : kind === 'n' && i.newborn_id === subjectId))
  const selectedId = kind ? selected?.alert_id ?? signal : null

  // ---- live refresh on stream events
  const refreshRef = useRef(refresh); refreshRef.current = refresh
  useEffect(() => {
    if (MOCK) return
    return subscribeEvents((e) => {
      if (e.type === 'alert' || e.type === 'escalation' || e.type === 'ack' || e.type === 'resolved') void refreshRef.current()
    })
  }, [])

  // ---- first interaction unlocks sound
  useEffect(() => {
    const on = () => { interacted = true }
    window.addEventListener('pointerdown', on, { once: true })
    window.addEventListener('keydown', on, { once: true })
    return () => { window.removeEventListener('pointerdown', on); window.removeEventListener('keydown', on) }
  }, [])

  // ---- new items animate in; a new red one also chimes
  const seen = useRef<Set<string> | null>(null)
  const [fresh, setFresh] = useState<Set<string>>(() => new Set())
  useEffect(() => {
    if (!data) return
    if (!seen.current) { seen.current = new Set(data.items.map((i) => i.alert_id)); return }
    const added = data.items.filter((i) => !seen.current!.has(i.alert_id))
    if (!added.length) return
    added.forEach((i) => seen.current!.add(i.alert_id))
    setFresh(new Set(added.map((i) => i.alert_id)))
    if (added.some((i) => i.severity === 'red' && i.status === 'open')) chime()
    const t = setTimeout(() => setFresh(new Set()), 3200)
    return () => clearTimeout(t)
  }, [data])

  // ---- tab title carries the waiting count
  const waiting = open.length
  useEffect(() => {
    if (!data) return
    document.title = waiting > 0 ? `(${waiting}) Sizni kutayotganlar — Tayanch` : 'Tayanch'
  }, [waiting, data])
  useEffect(() => { const prev = document.title; return () => { document.title = prev } }, [])

  // ---- keyboard: J/K or arrows move, Enter opens
  const [cursor, setCursor] = useState<string | null>(null)
  const cursorId = cursor && order.some((i) => i.alert_id === cursor) ? cursor : selectedId
  const keyState = useRef({ order, cursorId }); keyState.current = { order, cursorId }
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (ignoreKey(e)) return
      const { order, cursorId } = keyState.current
      if (!order.length) return
      const k = e.key
      const down = k === 'j' || k === 'J' || k === 'ArrowDown', up = k === 'k' || k === 'K' || k === 'ArrowUp'
      if (down || up) {
        e.preventDefault()
        const at = order.findIndex((i) => i.alert_id === cursorId)
        const next = at < 0 ? 0 : Math.min(order.length - 1, Math.max(0, at + (down ? 1 : -1)))
        const id = order[next].alert_id
        setCursor(id)
        document.querySelector(`[data-alert="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      } else if (k === 'Enter' && cursorId && !(e.target as HTMLElement | null)?.closest('a,button')) {
        const it = order.find((i) => i.alert_id === cursorId)
        if (it) { e.preventDefault(); nav(itemPath(it)) }
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [nav])

  // ---- after a decision: the item leaves the list, then the next waiting one opens
  const here = useRef({ selectedId, order }); here.current = { selectedId, order }
  const onDecided = (alertId: string) => {
    setGone((g) => new Set(g).add(alertId))
    void refresh()
    setTimeout(() => {
      if (here.current.selectedId !== alertId) return // the specialist already moved on
      const rest = here.current.order.filter((i) => i.alert_id !== alertId)
      const next = rest.find((i) => i.status === 'open') ?? rest[0]
      setCursor(null)
      nav(next ? itemPath(next) : LIST_PATH)
    }, 1900)
  }

  const list = (
    <aside className="ibx-list" aria-label="Javob kutayotgan bemorlar">
      <div className="ibx-head">
        <h1>Sizni kutayotganlar</h1>
        <span className={`ibx-count${waiting === 0 ? ' zero' : ''}`} aria-live="polite">{data ? waiting : '–'}</span>
      </div>
      <p className="ibx-sub">Bugun yopilgan: <span className="num">{data ? data.counts.resolved_today : '–'}</span></p>
      {error && data && <p className="ibx-offline pulse" role="status">Aloqa uzildi — qayta ulanmoqda…</p>}

      {!data && loading && (
        <div className="ibx-cards" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="skel" style={{ height: 132, borderRadius: 24 }} />)}</div>
      )}
      {!data && !loading && <div style={{ marginTop: 24 }}><ErrorNote message={error ?? 'Maʼlumot olinmadi'} onRetry={refresh} /></div>}

      {data && order.length === 0 && (
        <div className="ibx-zero fade-in"><ZoneOrb zone="green" size={84} /><p>Hammasi nazoratda. Javob kutayotgan bemor yoʻq.</p></div>
      )}
      {open.length > 0 && (
        <div className="ibx-cards stagger">
          {open.map((i) => <Card key={i.alert_id} item={i} selected={i.alert_id === selectedId} cursor={i.alert_id === cursorId} fresh={fresh.has(i.alert_id)} />)}
        </div>
      )}
      {acked.length > 0 && (
        <>
          <h2 className="ibx-group">Qabul qilingan — qaror kutilmoqda</h2>
          <div className="ibx-cards">
            {acked.map((i) => <Card key={i.alert_id} item={i} selected={i.alert_id === selectedId} cursor={i.alert_id === cursorId} fresh={fresh.has(i.alert_id)} />)}
          </div>
        </>
      )}
    </aside>
  )

  return (
    <div className="ibx">
      <TopBar compact={phone} />
      <div className={`ibx-body${kind ? ' has-sel' : ''}`}>
        {list}
        <main className="ibx-story glass">
          {kind && subjectId ? (
            <Story key={`${kind}:${subjectId}:${signal ?? ''}`} kind={kind} id={subjectId} signal={signal} item={selected}
              onChanged={() => void refresh()} onDecided={onDecided} />
          ) : (
            <div className="ibx-hint fade-in">
              {data && order.length > 0 && (
                <>
                  <span className="ibx-hint-ic"><Icon name="inbox" size={34} /></span>
                  <h2>Bemorni tanlang</h2>
                  <p>Eng shoshilinchi bemor roʻyxat boshida.</p>
                  <div className="ibx-keys">
                    <span><span className="kbd">J</span><span className="kbd">K</span> tanlash</span>
                    <span><span className="kbd">Enter</span> ochish</span>
                  </div>
                </>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
