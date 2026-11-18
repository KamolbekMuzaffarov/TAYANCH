// Users: give non-clinical roles (leader, admin), block / unblock. Nurses and doctors are added by facility admins.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { admin, api, getSession, type AdminUser, type Role, type UserStatus } from '../../lib/api'
import { initials } from '../../ui/kit'
import { Icon } from '../../ui/icons'
import { loadGeo, uz } from '../map/geo'
import { Note, Seg, fmtDateTime } from '../auth/parts'
import { Loading, Problem } from './AdminScreen'

const ROLE_WORD: Record<Role, string> = { admin: 'Administrator', leader: 'Rahbar', facility_admin: 'Muassasa administratori', specialist: 'Mutaxassis', nurse: 'Hamshira', pending: 'Rol berilmagan' }
const STATUS: Record<UserStatus, { word: string; tone: string }> = {
  active: { word: 'Faol', tone: 'green' }, pending: { word: 'Kutmoqda', tone: 'yellow' }, blocked: { word: 'Bloklangan', tone: 'red' },
}
type Filter = 'all' | UserStatus
type Assignable = 'leader' | 'admin'
const ASSIGNABLE: Assignable[] = ['leader', 'admin']
const norm = (s: string | null) => (s ?? '').toLowerCase().replace(/[ʻʼ'‘’`]/g, '').replace(/\s+/g, ' ').trim()
const bare = (s: string | null) => norm(s).replace(/ (viloyati|tumani|shahri|respublikasi)$/, '')

/** Names for the selects: the live layer's own names when reachable (they match what users carry), else the map files. */
interface Places { regions: { soato: string; name: string }[]; districts: { code: string; name: string; soato: string }[] }
async function loadPlaces(): Promise<Places> {
  const geo = await loadGeo().catch(() => null)
  try {
    const m = await api.map()
    if (m.regions.length && m.districts.length) {
      return {
        regions: m.regions.map((r) => ({ soato: r.soato, name: uz(r.name) })),
        districts: m.districts.map((d) => ({ code: d.code, name: uz(d.name), soato: d.region_soato })),
      }
    }
  } catch { /* fall back to the map files */ }
  if (!geo) throw new Error('Hududlar roʻyxati yuklanmadi')
  return { regions: geo.regions.map((r) => ({ soato: r.soato, name: r.name })), districts: geo.districts.map((d) => ({ code: d.code, name: d.name, soato: d.soato })) }
}

/** The system admin gives non-clinical roles only. Nurses, doctors and facility admins are shown read-only here. */
function Editor({ user, geo, onSaved, onClose }: { user: AdminUser; geo: Places | null; onSaved: (u: AdminUser) => void; onClose: () => void }) {
  const regions = useMemo(() => (geo ? geo.regions.slice().sort((a, b) => a.name.localeCompare(b.name)) : []), [geo])
  const staff = user.role === 'nurse' || user.role === 'specialist' || user.role === 'facility_admin'
  const [role, setRole] = useState<Assignable>(user.role === 'admin' ? 'admin' : 'leader')
  const [region, setRegion] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  // the list only carries names, so the current place is matched back to its code once the map names are loaded
  useEffect(() => {
    if (!geo || staff) return
    const r = geo.regions.find((x) => norm(x.name) === norm(user.region_name)) ?? geo.regions.find((x) => bare(x.name) === bare(user.region_name))
    if (r) setRegion(r.soato)
  }, [geo, staff, user.region_name])
  const self = getSession()?.user.id === user.id

  const save = async (status: 'active' | 'blocked', key: string) => {
    if (busy) return
    setBusy(key); setErr(null)
    try {
      const u = await admin.updateUser(user.id, staff || status === 'blocked' ? { status }
        : { role, status, region_soato: role === 'leader' ? region || null : null })
      onSaved({ ...user, ...u, name: u.name || user.name })
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  return (
    <div className="ad-edit fade-in">
      {staff ? (
        <p className="small">
          {ROLE_WORD[user.role]}{user.facility_name ? <span className="muted"> · {user.facility_name}</span> : null}
          {user.role === 'facility_admin' && <span className="muted"> — almashtirish «Muassasalar» boʻlimida.</span>}
        </p>
      ) : (
        <div className="ad-edit-grid">
          <label className="ob-field">
            <span className="ob-label">Rol</span>
            <select className="ob-input" value={role} onChange={(e) => setRole(e.target.value as Assignable)}>
              {ASSIGNABLE.map((r) => <option key={r} value={r}>{ROLE_WORD[r]}</option>)}
            </select>
          </label>
          <label className="ob-field">
            <span className="ob-label">Viloyat{role === 'leader' ? ' (boʻsh — respublika)' : ''}</span>
            <select className="ob-input" value={role === 'admin' ? '' : region} disabled={role === 'admin' || !geo} onChange={(e) => setRegion(e.target.value)}>
              <option value="">{role === 'admin' ? 'Kerak emas' : 'Butun respublika'}</option>
              {regions.map((r) => <option key={r.soato} value={r.soato}>{r.name}</option>)}
            </select>
          </label>
        </div>
      )}
      <p className="small muted">Hamshira va shifokorlarni faqat muassasa administratori qoʻshadi.</p>
      {err && <Note kind="err">{err}</Note>}
      <div className="ad-edit-actions">
        {(!staff || user.status === 'blocked') && (
          <button className="btn small" onClick={() => save('active', 'save')} disabled={busy !== null}>
            <Icon name="check" size={16} />{busy === 'save' ? 'Saqlanmoqda…' : user.status === 'blocked' ? 'Blokdan chiqarish' : user.status === 'pending' ? 'Tasdiqlash' : 'Saqlash'}
          </button>
        )}
        {user.status !== 'blocked' && !self && (
          <button className="btn small ghost ad-danger" onClick={() => save('blocked', 'block')} disabled={busy !== null}><Icon name="lock" size={16} />{busy === 'block' ? 'Bloklanmoqda…' : 'Bloklash'}</button>
        )}
        <button className="btn small soft" onClick={onClose} disabled={busy !== null}>Yopish</button>
      </div>
    </div>
  )
}

export default function AdminUsers({ onChanged }: { onChanged: () => void }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [items, setItems] = useState<AdminUser[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [geo, setGeo] = useState<Places | null>(null)
  const [q, setQ] = useState('')

  // one request for everybody: the filter then works instantly and the counters stay honest
  const load = useCallback(async () => {
    try { setItems(await admin.users()); setError(null) } catch (e) { setError((e as Error).message) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => { loadPlaces().then(setGeo).catch(() => {}) }, [])

  const counts = useMemo(() => {
    const c = { all: 0, pending: 0, active: 0, blocked: 0 }
    for (const u of items ?? []) { c.all++; c[u.status]++ }
    return c
  }, [items])
  const shown = useMemo(() => {
    const f = q.trim().toLowerCase()
    const rank: Record<UserStatus, number> = { pending: 0, active: 1, blocked: 2 }
    return (items ?? [])
      .filter((u) => (filter === 'all' || u.status === filter) && (!f || `${u.name} ${u.email ?? ''} ${u.phone ?? ''}`.toLowerCase().includes(f)))
      .sort((a, b) => rank[a.status] - rank[b.status] || (b.created_at ?? '').localeCompare(a.created_at ?? ''))
  }, [items, filter, q])

  if (!items) return error ? <Problem message={error} onRetry={load} /> : <Loading />

  return (
    <>
      <div className="ad-toolbar">
        <Seg label="Holat boʻyicha" value={filter} onChange={setFilter} options={[
          { value: 'all', label: `Hammasi · ${counts.all}` }, { value: 'pending', label: `Kutmoqda · ${counts.pending}` },
          { value: 'active', label: `Faol · ${counts.active}` }, { value: 'blocked', label: `Bloklangan · ${counts.blocked}` },
        ]} />
        <label className="ad-search">
          <Icon name="search" size={18} />
          <input className="ob-input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ism, email yoki telefon" aria-label="Foydalanuvchini qidirish" />
        </label>
      </div>
      {error && <Note kind="info">{error}</Note>}

      {shown.length === 0 ? (
        <div className="ad-panel ob-glass"><p className="muted center" style={{ padding: '28px 0' }}>{filter === 'pending' ? 'Rol kutayotgan foydalanuvchi yoʻq.' : 'Foydalanuvchi topilmadi.'}</p></div>
      ) : (
        <ul className="ad-users">
          {shown.map((u) => {
            const open = openId === u.id
            const place = [u.facility_name ?? u.region_name, u.facility_name ? null : u.district_name].filter(Boolean).join(' · ')
            return (
              <li key={u.id} className={`ad-user ob-glass${open ? ' open' : ''}`}>
                <div className="ad-user-row">
                  <span className="avatar">{initials(u.name || '?') || '?'}</span>
                  <div className="ad-user-who">
                    <span className="ad-user-n">{u.name || 'Ismsiz'}{u.two_step && <Icon name="shield" size={15} title="2 bosqichli kirish yoqilgan" className="ad-2fa" />}</span>
                    <span className="small muted ad-user-c">{[u.email, u.phone].filter(Boolean).join(' · ') || 'Aloqa maʼlumoti yoʻq'}</span>
                  </div>
                  <div className="ad-user-meta">
                    <span className="small">{ROLE_WORD[u.role]}{place ? <span className="muted"> · {place}</span> : null}</span>
                    <span className="tiny muted num">{u.last_login_at ? `Oxirgi kirish: ${fmtDateTime(u.last_login_at, false)}` : u.created_at ? `Roʻyxatdan oʻtgan: ${fmtDateTime(u.created_at, false)}` : ''}</span>
                  </div>
                  <span className={`ob-status ${STATUS[u.status].tone}`}>{STATUS[u.status].word}</span>
                  <button className={`btn small ${u.status === 'pending' && !open ? '' : 'soft'}`} onClick={() => setOpenId(open ? null : u.id)} aria-expanded={open}>
                    {open ? 'Yopish' : u.status === 'pending' ? 'Rol berish' : 'Oʻzgartirish'}
                  </button>
                </div>
                {open && (
                  <Editor user={u} geo={geo} onClose={() => setOpenId(null)}
                    onSaved={(nu) => { setItems((xs) => (xs ? xs.map((x) => (x.id === nu.id ? nu : x)) : xs)); setOpenId(null); onChanged(); void load() }} />
                )}
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}
