import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { account, api, getSession, roleOf, type Persona, type SearchResult } from '../lib/api'
import { Icon, type IconName } from './icons'

interface Item { key: string; group: string; label: string; sub?: string; icon: IconName; run: () => void }

/** ⌘K: one box that reaches everything — the depth behind the simple screens. */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const nav = useNavigate()
  const [q, setQ] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [idx, setIdx] = useState(0)
  const [note, setNote] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => { if (open) { setQ(''); setResults([]); setIdx(0); setNote(null); setTimeout(() => input.current?.focus(), 0) } }, [open])
  useEffect(() => {
    // a facility administrator has no patient or map search: staff accounts only
    if (!open || q.trim().length < 2 || roleOf(getSession()?.user) === 'facility_admin') { setResults([]); return }
    const t = setTimeout(() => { api.search(q.trim()).then((r) => setResults(r.results)).catch(() => setResults([])) }, 160)
    return () => clearTimeout(t)
  }, [q, open])

  const go = (path: string) => { onClose(); nav(path) }
  /** Screens belong to a role: sign in as that role first, then open the page fresh (new token, new streams). */
  const switchTo = async (p: Persona, path: string) => {
    if (getSession()?.user.persona === p) { go(path); return }
    setNote(null)
    try { await api.login(p) } catch { setNote('Kirib boʻlmadi. Qayta urinib koʻring.'); return }
    onClose(); window.location.assign(path)
  }

  const items = useMemo<Item[]>(() => {
    const found: Item[] = results.map((r) => ({
      key: `${r.kind}-${r.id}`, group: r.kind === 'patient' ? 'Bemorlar' : r.kind === 'district' ? 'Tumanlar' : 'Viloyatlar',
      label: r.label, sub: r.sub, icon: r.kind === 'patient' ? 'user' : r.kind === 'district' ? 'pin' : 'map',
      run: () => (r.kind === 'patient'
        ? switchTo('specialist', `/mutaxassis/bemor/${encodeURIComponent(r.id)}`)
        : switchTo('leader', r.kind === 'district' ? `/rahbar?tuman=${encodeURIComponent(r.id)}` : `/rahbar?viloyat=${encodeURIComponent(r.id)}`)),
    }))
    const base: Item[] = roleOf(getSession()?.user) === 'facility_admin' ? [
      { key: 'fac', icon: 'users', group: 'Muassasa', label: 'Muassasa xodimlari', sub: 'Hamshiralar va shifokorlar', run: () => go('/muassasa') },
      { key: 'fac-add', icon: 'plus', group: 'Muassasa', label: 'Xodim qoʻshish', sub: 'Hamshira yoki shifokor', run: () => go('/muassasa?yangi=1') },
      { key: 'account', icon: 'shield', group: 'Hisob', label: 'Hisob va xavfsizlik', sub: 'Ikki bosqichli kirish', run: () => go('/hisob') },
      { key: 'out', icon: 'logout', group: 'Hisob', label: 'Chiqish', run: () => { void account.logout(); onClose(); nav('/') } },
    ] : [
      { key: 'p-nurse', icon: 'nurse', group: 'Ekranlar', label: 'Hamshira ekrani', sub: 'Bugungi tashriflar', run: () => switchTo('nurse', '/hamshira') },
      { key: 'p-spec', icon: 'inbox', group: 'Ekranlar', label: 'Mutaxassis ekrani', sub: 'Javob kutayotganlar', run: () => switchTo('specialist', '/mutaxassis') },
      { key: 'p-lead', icon: 'map', group: 'Ekranlar', label: 'Rahbar xaritasi', sub: '199 tuman jonli', run: () => switchTo('leader', '/rahbar') },
      { key: 'rules', icon: 'file', group: 'Batafsil', label: 'Klinik qoidalar va manbalar', run: () => go('/batafsil?tab=qoidalar') },
      { key: 'audit', icon: 'list', group: 'Batafsil', label: 'Audit jurnali', run: () => go('/batafsil?tab=audit') },
      { key: 'privacy', icon: 'lock', group: 'Batafsil', label: 'Maxfiylik', sub: 'Tashqi AI ga nima yuboriladi', run: () => go('/batafsil?tab=maxfiylik') },
      { key: 'models', icon: 'chart', group: 'Batafsil', label: 'Modellar', sub: 'Sinov rejimi, koʻrsatkichlar', run: () => go('/batafsil?tab=modellar') },
      { key: 'security', icon: 'shield', group: 'Batafsil', label: 'Xavfsizlik', sub: 'Shifrlash, rollar, audit', run: () => go('/batafsil?tab=xavfsizlik') },
      { key: 'api', icon: 'external', group: 'Batafsil', label: 'API hujjati (Swagger)', sub: '/api/docs', run: () => { onClose(); window.open('/api/docs', '_blank') } },
      { key: 'scenario', icon: 'play', group: 'Namoyish', label: 'Namoyish ssenariysini boshlash', sub: 'Preeklampsiya holati', run: () => { api.scenario('preeclampsia').catch(() => {}); onClose() } },
      { key: 'account', icon: 'shield', group: 'Hisob', label: 'Hisob va xavfsizlik', sub: 'Parol, ikki bosqichli kirish', run: () => go('/hisob') },
      ...(roleOf(getSession()?.user) === 'admin' ? [{ key: 'admin', icon: 'settings' as const, group: 'Hisob', label: 'Admin paneli', sub: 'Foydalanuvchilar va rollar', run: () => go('/admin') }] : []),
      { key: 'out', icon: 'logout', group: 'Hisob', label: 'Chiqish', run: () => { void account.logout(); onClose(); nav('/') } },
    ]
    const f = q.trim().toLowerCase()
    return [...found, ...base.filter((b) => !f || b.label.toLowerCase().includes(f) || (b.sub || '').toLowerCase().includes(f))]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results, q, open])

  useEffect(() => { setIdx(0) }, [items.length])
  if (!open || !getSession()) return null

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
    else if (e.key === 'ArrowDown') { e.preventDefault(); setIdx((i) => Math.min(items.length - 1, i + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)) }
    else if (e.key === 'Enter' && items[idx]) items[idx].run()
  }
  let last = ''
  return (
    <div className="cmdk-backdrop" onClick={onClose}>
      <div className="cmdk" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Buyruqlar">
        <div className="cmdk-search">
          <Icon name="search" size={20} />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
            placeholder={roleOf(getSession()?.user) === 'facility_admin' ? 'Buyruqni yozing…' : 'Bemor, tuman yoki buyruqni yozing…'} aria-label="Qidirish" />
          <span className="kbd">esc</span>
        </div>
        {note && <p className="small" role="alert" style={{ padding: '10px 22px 0', color: 'var(--red-ink)' }}>{note}</p>}
        <div className="cmdk-list">
          {items.length === 0 && <div className="empty small">Hech narsa topilmadi</div>}
          {items.map((it, i) => {
            const head = it.group !== last ? <div className="cmdk-group">{it.group}</div> : null
            last = it.group
            return (
              <div key={it.key}>
                {head}
                <button className={`cmdk-item${i === idx ? ' active' : ''}`} onMouseEnter={() => setIdx(i)} onClick={it.run}>
                  <span className="cmdk-ic"><Icon name={it.icon} size={16} /></span>
                  <span className="grow">{it.label}</span>
                  {it.sub && <span className="small muted">{it.sub}</span>}
                  <Icon name="arrow-right" size={15} className="cmdk-go" />
                </button>
              </div>
            )
          })}
        </div>
        <div className="cmdk-foot">
          <span><span className="kbd">↑</span><span className="kbd">↓</span> tanlash</span>
          <span><span className="kbd">↵</span> ochish</span>
        </div>
      </div>
    </div>
  )
}
