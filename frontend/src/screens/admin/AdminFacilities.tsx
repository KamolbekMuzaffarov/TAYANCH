// Facilities: each one has exactly one administrator. The system admin appoints, replaces or removes that person.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { admin, type AdminFacility, type AdminUser } from '../../lib/api'
import { Icon } from '../../ui/icons'
import { Note, Seg } from '../auth/parts'
import { facilityType, looksLikeContact } from '../facility/labels'
import { Loading, Problem } from './AdminScreen'

const PAGE = 40
const fold = (s: string | null) => (s ?? '').toLowerCase().replace(/[ʻʼ'‘’`]/g, '')

function Appoint({ fac, users, onSaved, onClose }: {
  fac: AdminFacility; users: AdminUser[]; onSaved: (f: AdminFacility, note: string) => void; onClose: () => void
}) {
  const [how, setHow] = useState<'new' | 'existing'>('new')
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [userId, setUserId] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  // anyone who is not the system admin, not a demo persona, not blocked and not this facility's admin already
  const candidates = useMemo(() => users
    .filter((u) => !u.demo && u.role !== 'admin' && u.status !== 'blocked' && u.id !== fac.facility_admin?.id && (u.email || u.phone))
    .sort((a, b) => a.name.localeCompare(b.name)), [users, fac.facility_admin?.id])
  const valid = how === 'new' ? name.trim().length >= 2 && looksLikeContact(contact) : !!userId
  const fixed = fac.facility_admin?.demo === true

  const save = async () => {
    if (busy || !valid) return
    setBusy('save'); setErr(null)
    try {
      const f = await admin.appointFacilityAdmin(fac.id, how === 'new' ? { identifier: contact.trim(), full_name: name.trim() } : { user_id: userId })
      const via = how === 'new' ? contact.trim() : (candidates.find((u) => u.id === userId)?.email ?? candidates.find((u) => u.id === userId)?.phone ?? '')
      onSaved(f, `Administrator tayinlandi.${via ? ` U ${via} orqali kirishi mumkin.` : ''}`)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }
  const remove = async () => {
    if (busy) return
    setBusy('remove'); setErr(null)
    try { onSaved(await admin.removeFacilityAdmin(fac.id), 'Administrator olib tashlandi.') }
    catch (e) { setErr((e as Error).message); setConfirm(false) } finally { setBusy(null) }
  }

  if (fixed) {
    return (
      <div className="ad-edit fade-in">
        <p className="small muted">Bu namoyish muassasasi — uning administratori oʻzgartirilmaydi.</p>
        <div className="ad-edit-actions"><button className="btn small soft" onClick={onClose}>Yopish</button></div>
      </div>
    )
  }
  return (
    <div className="ad-edit fade-in">
      <Seg label="Kimni tayinlash" value={how} onChange={setHow} options={[{ value: 'new', label: 'Yangi shaxs' }, { value: 'existing', label: 'Mavjud foydalanuvchi' }]} />
      {how === 'new' ? (
        <div className="ad-edit-grid">
          <label className="ob-field">
            <span className="ob-label">Ism va familiya</span>
            <input className="ob-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoComplete="off" />
          </label>
          <label className="ob-field">
            <span className="ob-label">Email yoki telefon</span>
            <input className="ob-input" value={contact} onChange={(e) => setContact(e.target.value)} maxLength={254} placeholder="ism@muassasa.uz yoki +998…"
              autoComplete="off" autoCapitalize="none" spellCheck={false} onKeyDown={(e) => { if (e.key === 'Enter') void save() }} />
          </label>
        </div>
      ) : (
        <label className="ob-field">
          <span className="ob-label">Foydalanuvchi</span>
          <select className="ob-input" value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">{candidates.length ? 'Tanlang' : 'Mos foydalanuvchi yoʻq'}</option>
            {candidates.map((u) => <option key={u.id} value={u.id}>{u.name} — {u.email ?? u.phone}</option>)}
          </select>
        </label>
      )}
      <p className="small muted">
        {fac.facility_admin ? `Hozirgi administrator (${fac.facility_admin.name}) oʻrnidan boʻshatiladi. ` : ''}Tayinlangan shaxs email yoki telefoniga keladigan bir martalik kod bilan kiradi.
      </p>
      {err && <Note kind="err">{err}</Note>}
      <div className="ad-edit-actions">
        {confirm ? (
          <>
            <span className="small" style={{ alignSelf: 'center' }}>Administrator olib tashlansinmi?</span>
            <button className="btn small ghost ad-danger" onClick={remove} disabled={busy !== null}>{busy === 'remove' ? 'Olib tashlanmoqda…' : 'Ha, olib tashlash'}</button>
            <button className="btn small soft" onClick={() => setConfirm(false)} disabled={busy !== null}>Bekor qilish</button>
          </>
        ) : (
          <>
            <button className="btn small" onClick={save} disabled={!valid || busy !== null}>
              <Icon name="check" size={16} />{busy === 'save' ? 'Saqlanmoqda…' : fac.facility_admin ? 'Almashtirish' : 'Tayinlash'}
            </button>
            {fac.facility_admin && (
              <button className="btn small ghost ad-danger" onClick={() => setConfirm(true)} disabled={busy !== null}><Icon name="trash" size={16} />Olib tashlash</button>
            )}
            <button className="btn small soft" onClick={onClose} disabled={busy !== null}>Yopish</button>
          </>
        )}
      </div>
    </div>
  )
}

export default function AdminFacilities({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<AdminFacility[] | null>(null)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [type, setType] = useState('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [limit, setLimit] = useState(PAGE)

  const load = useCallback(async () => {
    try { setItems(await admin.facilities()); setError(null) } catch (e) { setError((e as Error).message) }
    admin.users().then(setUsers).catch(() => {})
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => { setLimit(PAGE) }, [q, type])

  const types = useMemo(() => Array.from(new Set((items ?? []).map((f) => f.type))).sort(), [items])
  const shown = useMemo(() => {
    const f = fold(q.trim())
    return (items ?? []).filter((x) => (type === 'all' || x.type === type)
      && (!f || fold(`${x.name} ${x.region ?? ''} ${x.district ?? ''} ${x.facility_admin?.name ?? ''}`).includes(f)))
  }, [items, q, type])
  const withAdmin = useMemo(() => (items ?? []).filter((f) => f.facility_admin).length, [items])

  if (!items) return error ? <Problem message={error} onRetry={load} /> : <Loading />

  return (
    <>
      <div className="ad-toolbar">
        <Seg label="Muassasa turi" value={type} onChange={setType} options={[{ value: 'all', label: `Hammasi · ${items.length}` }, ...types.map((t) => ({ value: t, label: facilityType(t) }))]} />
        <label className="ad-search">
          <Icon name="search" size={18} />
          <input className="ob-input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Muassasa, tuman yoki administrator" aria-label="Muassasani qidirish" />
        </label>
      </div>
      <p className="small muted num">{withAdmin} ta muassasada administrator bor · {items.length - withAdmin} tasida tayinlanmagan</p>
      {error && <Note kind="info">{error}</Note>}
      {done && <Note kind="ok">{done}</Note>}

      {shown.length === 0 ? (
        <div className="ad-panel ob-glass"><p className="muted center" style={{ padding: '28px 0' }}>Muassasa topilmadi.</p></div>
      ) : (
        <ul className="ad-users">
          {shown.slice(0, limit).map((f) => {
            const open = openId === f.id
            const a = f.facility_admin
            return (
              <li key={f.id} className={`ad-user ob-glass${open ? ' open' : ''}`}>
                <div className="ad-user-row">
                  <span className="avatar" aria-hidden><Icon name="building" size={17} /></span>
                  <div className="ad-user-who">
                    <span className="ad-user-n">{f.name}</span>
                    <span className="small muted ad-user-c">{[facilityType(f.type), f.region, f.district].filter(Boolean).join(' · ')}</span>
                  </div>
                  <div className="ad-user-meta">
                    <span className="small">{a ? a.name : <span className="muted">Administrator yoʻq</span>}</span>
                    <span className="tiny muted num">{a ? (a.email ?? a.phone ?? 'Namoyish hisobi') : ''}{a ? ' · ' : ''}{f.staff_count} xodim</span>
                  </div>
                  <span className={`ob-status ${a ? 'green' : 'grey'}`}>{a ? 'Tayinlangan' : 'Tayinlanmagan'}</span>
                  <button className={`btn small ${a || open ? 'soft' : ''}`} onClick={() => { setDone(null); setOpenId(open ? null : f.id) }} aria-expanded={open}>
                    {open ? 'Yopish' : a ? 'Almashtirish' : 'Admin tayinlash'}
                  </button>
                </div>
                {open && (
                  <Appoint fac={f} users={users} onClose={() => setOpenId(null)}
                    onSaved={(nf, note) => { setItems((xs) => (xs ? xs.map((x) => (x.id === nf.id ? nf : x)) : xs)); setOpenId(null); setDone(note); onChanged(); void load() }} />
                )}
              </li>
            )
          })}
        </ul>
      )}
      {shown.length > limit && (
        <button className="btn small soft" style={{ alignSelf: 'center' }} onClick={() => setLimit((n) => n + PAGE)}>Yana koʻrsatish · {shown.length - limit}</button>
      )}
    </>
  )
}
