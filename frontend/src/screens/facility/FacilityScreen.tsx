// Facility administrator's page: the staff accounts of ONE facility. No patient data ever reaches this screen.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { facility, type FacilityInfo, type FacilityStaff, type StaffRole } from '../../lib/api'
import { TopBar } from '../../ui/TopBar'
import { initials } from '../../ui/kit'
import { Icon } from '../../ui/icons'
import { Note, Seg, fmtDateTime } from '../auth/parts'
import { facilityType, looksLikeContact } from './labels'
import '../landing/base.css'
import './facility.css'

const ROLE_WORD: Record<StaffRole, string> = { nurse: 'Hamshira', specialist: 'Shifokor' }

function AddSheet({ info, onAdded, onClose }: { info: FacilityInfo; onAdded: (s: FacilityStaff, contact: string) => void; onClose: () => void }) {
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [role, setRole] = useState<StaffRole>('nurse')
  const [district, setDistrict] = useState(info.facility.district_code ?? '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onClose])
  const valid = name.trim().length >= 2 && looksLikeContact(contact) && (role !== 'nurse' || !!district)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy || !valid) return
    setBusy(true); setErr(null)
    try {
      const s = await facility.add({ full_name: name.trim(), identifier: contact.trim(), role, district_code: role === 'nurse' ? district : null })
      onAdded(s, contact.trim())
    } catch (x) { setErr((x as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="sheet-back" onClick={onClose}>
      <form className="sheet fc-sheet" onClick={(e) => e.stopPropagation()} onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="fc-add-title">
        <header className="fc-sheet-head">
          <h2 id="fc-add-title">Xodim qoʻshish</h2>
          <button type="button" className="ob-icon-btn" onClick={onClose} aria-label="Yopish"><Icon name="x" size={18} /></button>
        </header>
        <p className="muted">{info.facility.name}</p>
        <label className="ob-field">
          <span className="ob-label">Ism va familiya</span>
          <input className="ob-input" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoFocus autoComplete="off" />
        </label>
        <label className="ob-field">
          <span className="ob-label">Email yoki telefon</span>
          <input className="ob-input" value={contact} onChange={(e) => setContact(e.target.value)} maxLength={254} placeholder="ism@muassasa.uz yoki +998…"
            autoComplete="off" autoCapitalize="none" spellCheck={false} inputMode="email" />
        </label>
        <div className="ob-field">
          <span className="ob-label">Lavozim</span>
          <Seg block label="Lavozim" value={role} onChange={setRole} options={[{ value: 'nurse', label: 'Hamshira' }, { value: 'specialist', label: 'Shifokor' }]} />
        </div>
        {role === 'nurse' && (
          <label className="ob-field">
            <span className="ob-label">Tuman</span>
            <select className="ob-input" value={district} onChange={(e) => setDistrict(e.target.value)}>
              <option value="">Tanlang</option>
              {info.districts.map((d) => <option key={d.code} value={d.code}>{d.name}</option>)}
            </select>
          </label>
        )}
        <p className="small muted">Xodim shu email yoki telefonga keladigan bir martalik kod bilan kiradi.</p>
        {err && <Note kind="err">{err}</Note>}
        <span className="grow" />
        <button className="btn block" type="submit" disabled={!valid || busy}><Icon name="plus" size={18} />{busy ? 'Qoʻshilmoqda…' : 'Qoʻshish'}</button>
      </form>
    </div>
  )
}

export default function FacilityScreen() {
  const [params, setParams] = useSearchParams()
  const [info, setInfo] = useState<FacilityInfo | null>(null)
  const [staff, setStaff] = useState<FacilityStaff[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(params.get('yangi') === '1')
  const [done, setDone] = useState<string | null>(null)
  const [rowErr, setRowErr] = useState<{ id: string; text: string } | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [i, s] = await Promise.all([facility.me(), facility.staff()])
      setInfo(i); setStaff(s); setError(null)
    } catch (e) { setError((e as Error).message) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => { if (params.get('yangi') === '1') { setAdding(true); setParams({}, { replace: true }) } }, [params, setParams])

  // counted from the list on screen, so the chips never disagree with the rows
  const counts = useMemo(() => ({
    nurses: (staff ?? []).filter((s) => s.role === 'nurse').length,
    doctors: (staff ?? []).filter((s) => s.role === 'specialist').length,
    blocked: (staff ?? []).filter((s) => s.status === 'blocked').length,
  }), [staff])

  const act = async (s: FacilityStaff, kind: 'block' | 'unblock' | 'remove') => {
    if (busy) return
    setBusy(`${kind}-${s.id}`); setRowErr(null); setDone(null)
    try {
      if (kind === 'remove') {
        await facility.remove(s.id)
        setStaff((xs) => (xs ? xs.filter((x) => x.id !== s.id) : xs))
        setDone(`${s.name} roʻyxatdan olib tashlandi.`)
      } else {
        const u = await facility.update(s.id, { status: kind === 'block' ? 'blocked' : 'active' })
        setStaff((xs) => (xs ? xs.map((x) => (x.id === s.id ? { ...x, ...u, name: u.name || x.name } : x)) : xs))
      }
      setConfirm(null)
    } catch (e) { setRowErr({ id: s.id, text: (e as Error).message }) } finally { setBusy(null) }
  }

  return (
    <div className="fc">
      <TopBar />
      <main className="fc-main fade-in">
        {!info || !staff ? (
          error ? (
            <section className="fc-card ob-glass fc-empty">
              <Icon name="wifi-off" size={28} />
              <p>{error}</p>
              <button className="btn small soft" onClick={() => { void load() }}><Icon name="refresh" size={16} />Qayta urinish</button>
            </section>
          ) : <div className="fc-skel"><div className="skel" /><div className="skel" /><div className="skel" /></div>
        ) : (
          <>
            <section className="fc-card fc-hero ob-glass strong">
              <span className="fc-hero-ic" aria-hidden><Icon name="building" size={26} /></span>
              <div className="grow">
                <span className="ob-eyebrow">Muassasa administratori</span>
                <h1>{info.facility.name}</h1>
                <p className="muted">{[facilityType(info.facility.type), info.facility.district_name, info.facility.region_name].filter(Boolean).join(' · ')}</p>
              </div>
            </section>

            <div className="fc-stats">
              <div className="fc-stat ob-glass"><span className="fc-stat-v num">{counts.nurses}</span><span className="fc-stat-l">Hamshiralar</span></div>
              <div className="fc-stat ob-glass"><span className="fc-stat-v num">{counts.doctors}</span><span className="fc-stat-l">Shifokorlar</span></div>
              <div className="fc-stat ob-glass"><span className={`fc-stat-v num${counts.blocked ? ' bad' : ''}`}>{counts.blocked}</span><span className="fc-stat-l">Bloklangan</span></div>
            </div>

            <div className="fc-bar">
              <h2 className="grow">Xodimlar</h2>
              <button className="btn" onClick={() => { setDone(null); setAdding(true) }}><Icon name="plus" size={18} />Xodim qoʻshish</button>
            </div>
            {error && <Note kind="info">{error}</Note>}
            {done && <Note kind="ok">{done}</Note>}

            {staff.length === 0 ? (
              <section className="fc-card ob-glass fc-empty">
                <Icon name="users" size={30} />
                <p><b>Hali xodim yoʻq.</b></p>
                <p className="muted">Hamshira va shifokorlarni faqat siz qoʻshasiz.</p>
              </section>
            ) : (
              <ul className="fc-list">
                {staff.map((s) => (
                  <li key={s.id} className="fc-row ob-glass">
                    <div className="fc-row-main">
                      <span className="avatar" aria-hidden>{initials(s.name || '?') || '?'}</span>
                      <div className="fc-who">
                        <span className="fc-name">{s.name || 'Ismsiz'}</span>
                        <span className="small muted fc-sub">{[ROLE_WORD[s.role], s.district_name].filter(Boolean).join(' · ')}</span>
                      </div>
                      <div className="fc-meta">
                        <span className="small ob-mono">{s.contact ?? '—'}</span>
                        <span className="tiny muted num">{s.last_login_at ? `Oxirgi kirish: ${fmtDateTime(s.last_login_at, false)}` : 'Hali kirmagan'}</span>
                      </div>
                      <span className={`ob-status ${s.status === 'blocked' ? 'red' : 'green'}`}>{s.status === 'blocked' ? 'Bloklangan' : 'Faol'}</span>
                    </div>
                    {!s.demo && (
                      <div className="fc-actions">
                        {confirm === s.id ? (
                          <>
                            <span className="small grow">Olib tashlansinmi? Kiritgan maʼlumotlari saqlanib qoladi.</span>
                            <button className="btn small ghost fc-danger" onClick={() => act(s, 'remove')} disabled={busy !== null}>{busy === `remove-${s.id}` ? 'Olib tashlanmoqda…' : 'Ha, olib tashlash'}</button>
                            <button className="btn small soft" onClick={() => setConfirm(null)} disabled={busy !== null}>Bekor qilish</button>
                          </>
                        ) : (
                          <>
                            {s.status === 'blocked'
                              ? <button className="btn small soft" onClick={() => act(s, 'unblock')} disabled={busy !== null}><Icon name="check" size={16} />{busy === `unblock-${s.id}` ? 'Ochilmoqda…' : 'Blokdan chiqarish'}</button>
                              : <button className="btn small soft" onClick={() => act(s, 'block')} disabled={busy !== null}><Icon name="lock" size={16} />{busy === `block-${s.id}` ? 'Bloklanmoqda…' : 'Bloklash'}</button>}
                            <button className="btn small ghost fc-danger" onClick={() => { setRowErr(null); setConfirm(s.id) }} disabled={busy !== null}><Icon name="trash" size={16} />Olib tashlash</button>
                          </>
                        )}
                      </div>
                    )}
                    {rowErr?.id === s.id && <Note kind="err">{rowErr.text}</Note>}
                  </li>
                ))}
              </ul>
            )}
            <p className="small muted center">Bu sahifada bemor maʼlumotlari koʻrsatilmaydi.</p>
          </>
        )}
      </main>
      {adding && info && (
        <AddSheet info={info} onClose={() => setAdding(false)}
          onAdded={(s, contact) => { setAdding(false); setStaff((xs) => (xs ? [s, ...xs.filter((x) => x.id !== s.id)] : [s])); setDone(`Xodim qoʻshildi. U ${contact} orqali kirishi mumkin.`) }} />
      )}
    </div>
  )
}
