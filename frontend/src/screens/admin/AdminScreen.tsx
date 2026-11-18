// Admin panel. Everything here is aggregate — no patient names ever reach this screen.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { account, admin, getSession, type AdminOverview, type AdminRegionRow } from '../../lib/api'
import { BrandMark, ThemeToggle, initials } from '../../ui/kit'
import { Icon, type IconName } from '../../ui/icons'
import { Note, Seg, fmtDateTime, fmtNum } from '../auth/parts'
import { BarList, DayChart, Legend, MiniBar } from './charts'
import AdminUsers from './AdminUsers'
import AdminFacilities from './AdminFacilities'
import { AdminAudit, AdminReports } from './AdminReports'
import '../landing/base.css'
import './admin.css'

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: '/admin', label: 'Umumiy koʻrinish', icon: 'chart', end: true },
  { to: '/admin/hududlar', label: 'Hududlar', icon: 'building' },
  { to: '/admin/muassasalar', label: 'Muassasalar', icon: 'stethoscope' },
  { to: '/admin/foydalanuvchilar', label: 'Foydalanuvchilar', icon: 'users' },
  { to: '/admin/hisobotlar', label: 'Hisobotlar', icon: 'download' },
  { to: '/admin/audit', label: 'Audit', icon: 'layers' },
]
const LINKS: { to: string; label: string; icon: IconName }[] = [
  { to: '/rahbar', label: 'Xarita', icon: 'map' },
  { to: '/batafsil', label: 'Batafsil', icon: 'info' },
]

export interface OverviewState { data: AdminOverview | null; error: string | null; loading: boolean; days: number; setDays: (d: number) => void; reload: () => void }

function useOverview(): OverviewState {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<AdminOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const load = useCallback(async (d: number) => {
    setLoading(true)
    try { setData(await admin.overview(d)); setError(null) } catch (e) { setError((e as Error).message) } finally { setLoading(false) }
  }, [])
  useEffect(() => {
    void load(days)
    const t = setInterval(() => { void load(days) }, 60000)
    return () => clearInterval(t)
  }, [days, load])
  return { data, error, loading, days, setDays, reload: () => { void load(days) } }
}

const pct = (v: number | null) => (v === null ? '—' : `${fmtNum(v, Number.isInteger(v) ? 0 : 1)}%`)
const mins = (v: number | null) => (v === null ? '—' : fmtNum(v, Number.isInteger(v) ? 0 : 1))

function PageHead({ title, sub, children }: { title: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="ad-head">
      <div className="grow"><h1>{title}</h1>{sub && <p className="muted">{sub}</p>}</div>
      {children}
    </div>
  )
}
export function Loading() { return <div className="ad-skel"><div className="skel" /><div className="skel" /><div className="skel" /></div> }
export function Problem({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="ad-panel ob-glass ad-problem">
      <Icon name="wifi-off" size={28} />
      <p>{message}</p>
      <button className="btn small soft" onClick={onRetry}><Icon name="refresh" size={16} />Qayta urinish</button>
    </div>
  )
}

// ---------------------------------------------------------------- overview
function Overview({ ov }: { ov: OverviewState }) {
  const d = ov.data
  const range = (
    <Seg label="Davr" value={ov.days} onChange={ov.setDays} options={[{ value: 7, label: '7 kun' }, { value: 30, label: '30 kun' }, { value: 90, label: '90 kun' }]} />
  )
  if (!d) {
    return (
      <>
        <PageHead title="Umumiy koʻrinish">{range}</PageHead>
        {ov.error ? <Problem message={ov.error} onRetry={ov.reload} /> : <Loading />}
      </>
    )
  }
  const k = d.kpis
  const zones = [
    { key: 'red', v: k.red, c: 'var(--red)', l: 'Xavfli' }, { key: 'yellow', v: k.yellow, c: 'var(--yellow)', l: 'Eʼtibor' },
    { key: 'green', v: k.green, c: 'var(--green)', l: 'Yaxshi' }, { key: 'grey', v: k.grey, c: 'var(--grey)', l: 'Maʼlumot yoʻq' },
  ]
  const zoneSum = zones.reduce((a, z) => a + z.v, 0)
  const a = d.series.alerts_by_day, r = d.series.response_by_day, v = d.series.visits_by_day
  const sum = (xs: number[]) => xs.reduce((p, c) => p + c, 0)

  return (
    <>
      <PageHead title="Umumiy koʻrinish" sub={d.generated_at ? `Yangilandi: ${fmtDateTime(d.generated_at, false)}` : undefined}>{range}</PageHead>
      {ov.error && <Note kind="info">{ov.error} Oxirgi olingan maʼlumot koʻrsatilmoqda.</Note>}

      <div className={`ad-kpis${ov.loading ? ' dim' : ''}`}>
        <div className="ad-kpi ob-glass">
          <span className="ad-kpi-l">Homiladorlar</span>
          <span className="ad-kpi-v num">{fmtNum(k.pregnant)}</span>
          {zoneSum > 0 && (
            <span className="ad-zones" role="img" aria-label={zones.map((z) => `${z.l}: ${z.v}`).join(', ')}>
              {zones.map((z) => (z.v > 0 ? <i key={z.key} style={{ flexGrow: z.v, background: z.c }} /> : null))}
            </span>
          )}
          <span className="tiny muted num">{fmtNum(k.newborns)} chaqaloq kuzatuvda</span>
        </div>
        <div className="ad-kpi ob-glass">
          <span className="ad-kpi-l">Ochiq signallar</span>
          <span className="ad-kpi-v num">{fmtNum(k.open_alerts)}</span>
          <span className={`ob-status ${k.overdue_alerts > 0 ? 'red' : 'green'}`}>{k.overdue_alerts > 0 ? `${fmtNum(k.overdue_alerts)} tasi muddatidan oʻtgan` : 'Muddati oʻtgani yoʻq'}</span>
        </div>
        <div className="ad-kpi ob-glass">
          <span className="ad-kpi-l">Oʻrtacha javob vaqti</span>
          <span className="ad-kpi-v num">{mins(k.median_response_min)}<small> daq</small></span>
          <span className="tiny muted num">10 tadan 9 tasi — {mins(k.p90_response_min)} daqiqagacha</span>
        </div>
        <div className="ad-kpi ob-glass">
          <span className="ad-kpi-l">30 daqiqa ichida javob</span>
          <span className="ad-kpi-v num">{pct(k.answered_in_sla_pct)}</span>
          {k.answered_in_sla_pct !== null && <span className="ad-meter"><i style={{ width: `${Math.min(100, Math.max(0, k.answered_in_sla_pct))}%` }} /></span>}
        </div>
      </div>

      <div className={`ad-kpis sm${ov.loading ? ' dim' : ''}`}>
        {([
          { l: 'Xavfli', v: fmtNum(k.red), dot: 'var(--red)' },
          { l: 'Eʼtibor', v: fmtNum(k.yellow), dot: 'var(--yellow)' },
          { l: '7 kunlik tashriflar', v: fmtNum(k.visits_7d) },
          { l: 'Ovoz bilan kiritilgan', v: pct(k.voice_share_pct) },
          { l: 'Foydalanuvchilar', v: fmtNum(k.users) },
        ] as { l: string; v: string; dot?: string }[]).map((s) => (
          <div key={s.l} className="ad-kpi sm ob-glass">
            <span className="ad-kpi-l">{s.dot && <i className="ad-dot" style={{ background: s.dot }} />}{s.l}</span>
            <span className="ad-kpi-v num">{s.v}</span>
          </div>
        ))}
        <Link to="/admin/foydalanuvchilar" className={`ad-kpi sm ob-glass ad-kpi-link${k.pending_users > 0 ? ' hot' : ''}`}>
          <span className="ad-kpi-l">Rol kutayotganlar</span>
          <span className="ad-kpi-v num">{fmtNum(k.pending_users)}<Icon name="chevron-right" size={18} /></span>
        </Link>
      </div>

      <div className="ad-grid2">
        <section className="ad-panel ob-glass ad-wide">
          <header className="ad-panel-head">
            <div className="grow"><h2>Signallar</h2><p className="small muted num">Kunlik · jami {fmtNum(sum(a.map((x) => x.red + x.yellow)))} ta, yopilgan {fmtNum(sum(a.map((x) => x.resolved)))} ta</p></div>
            <Legend items={[{ label: 'Xavfli', color: 'var(--red)' }, { label: 'Eʼtibor', color: 'var(--yellow)' }, { label: 'Yopilgan', color: 'var(--green-fill)', line: true }]} />
          </header>
          <DayChart label="Kunlik signallar: xavfli, eʼtibor va yopilganlar" days={a.map((x) => x.date)} unit="ta"
            bars={[{ key: 'red', label: 'Xavfli', color: 'var(--red)', values: a.map((x) => x.red) }, { key: 'yellow', label: 'Eʼtibor', color: 'var(--yellow)', values: a.map((x) => x.yellow) }]}
            lines={[{ key: 'resolved', label: 'Yopilgan', color: 'var(--green-fill)', values: a.map((x) => x.resolved) }]} />
        </section>

        <section className="ad-panel ob-glass">
          <header className="ad-panel-head">
            <div className="grow"><h2>Javob vaqti</h2><p className="small muted">Kunlik oʻrtacha, daqiqa</p></div>
            <Legend items={[{ label: '30 daqiqa — meʼyor', color: 'var(--red)', dashed: true }]} />
          </header>
          <DayChart label="Kunlik oʻrtacha javob vaqti, 30 daqiqalik meʼyor chizigʻi bilan" days={r.map((x) => x.date)} unit="daq"
            lines={[{ key: 'median', label: 'Oʻrtacha javob', color: 'var(--ink)', values: r.map((x) => x.median_min), area: true }]}
            reference={{ value: 30, label: '30 daq' }} />
        </section>

        <section className="ad-panel ob-glass">
          <header className="ad-panel-head">
            <div className="grow"><h2>Tashriflar</h2><p className="small muted num">Kunlik · jami {fmtNum(sum(v.map((x) => x.count)))} ta</p></div>
          </header>
          <DayChart label="Kunlik tashriflar soni" days={v.map((x) => x.date)} unit="ta"
            bars={[{ key: 'visits', label: 'Tashriflar', color: 'var(--blue)', values: v.map((x) => x.count) }]} />
        </section>

        <section className="ad-panel ob-glass">
          <header className="ad-panel-head"><div className="grow"><h2>Asosiy sabablar</h2><p className="small muted">Signal nimadan chiqdi</p></div></header>
          <BarList items={d.top_reasons.slice(0, 7)} color="var(--ink)" />
        </section>
        <section className="ad-panel ob-glass">
          <header className="ad-panel-head"><div className="grow"><h2>Qarorlar</h2><p className="small muted">Signal qanday yopildi</p></div></header>
          <BarList items={d.resolutions.slice(0, 7)} color="var(--green-fill)" />
        </section>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- regions
type SortKey = 'name' | 'pregnant' | 'red' | 'yellow' | 'open_alerts' | 'overdue_alerts' | 'median_response_min' | 'answered_in_sla_pct'
const COLS: { key: SortKey; label: string; short: string }[] = [
  { key: 'pregnant', label: 'Homilador', short: 'Homilador' },
  { key: 'red', label: 'Xavfli', short: 'Xavfli' },
  { key: 'yellow', label: 'Eʼtibor', short: 'Eʼtibor' },
  { key: 'open_alerts', label: 'Ochiq signal', short: 'Ochiq' },
  { key: 'overdue_alerts', label: 'Muddati oʻtgan', short: 'Kechikkan' },
  { key: 'median_response_min', label: 'Oʻrtacha javob, daq', short: 'Javob, daq' },
  { key: 'answered_in_sla_pct', label: '30 daqiqada javob', short: '30 daq ichida' },
]

function Regions({ ov }: { ov: OverviewState }) {
  const nav = useNavigate()
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'red', dir: -1 })
  const rows = ov.data?.by_region ?? []
  const sorted = useMemo(() => {
    const out = rows.slice()
    out.sort((p, q) => {
      if (sort.key === 'name') return p.name.localeCompare(q.name) * sort.dir
      const a = p[sort.key], b = q[sort.key]
      if (a === null && b === null) return 0
      if (a === null) return 1
      if (b === null) return -1
      return (a - b) * sort.dir || p.name.localeCompare(q.name)
    })
    return out
  }, [rows, sort])
  const max = (k: keyof AdminRegionRow) => Math.max(1, ...rows.map((r) => (typeof r[k] === 'number' ? (r[k] as number) : 0)))
  const mx = { pregnant: max('pregnant'), red: max('red'), yellow: max('yellow'), open: max('open_alerts'), overdue: max('overdue_alerts') }
  const by = (key: SortKey) => setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: key === 'name' ? 1 : -1 }))
  const th = (k: SortKey, children: React.ReactNode, left = false) => (
    <th key={k} className={left ? 'left' : ''} aria-sort={sort.key === k ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
      <button onClick={() => by(k)} className={sort.key === k ? 'on' : ''}>{children}{sort.key === k && <Icon name={sort.dir === 1 ? 'arrow-up' : 'arrow-down'} size={13} />}</button>
    </th>
  )
  const open = (soato: string) => nav(`/rahbar?viloyat=${encodeURIComponent(soato)}`)

  return (
    <>
      <PageHead title="Hududlar" sub="Ustun nomini bosing — saralanadi. Qatorni bosing — xaritada ochiladi." />
      {!ov.data ? (ov.error ? <Problem message={ov.error} onRetry={ov.reload} /> : <Loading />) : rows.length === 0 ? (
        <div className="ad-panel ob-glass"><p className="muted center">Hududlar boʻyicha maʼlumot yoʻq.</p></div>
      ) : (
        <div className="ad-panel ob-glass ad-table-wrap">
          <table className="ad-table">
            <thead>
              <tr>
                {th('name', 'Hudud', true)}
                {COLS.map((c) => th(c.key, <><span className="ad-th-long">{c.label}</span><span className="ad-th-short">{c.short}</span></>))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.soato} tabIndex={0} role="link" onClick={() => open(r.soato)} onKeyDown={(e) => { if (e.key === 'Enter') open(r.soato) }} aria-label={`${r.name} — xaritada ochish`}>
                  <th scope="row" className="left">{r.name}</th>
                  <td><span className="num">{fmtNum(r.pregnant)}</span><MiniBar value={r.pregnant} max={mx.pregnant} color="var(--ink-3)" /></td>
                  <td><span className="num">{fmtNum(r.red)}</span><MiniBar value={r.red} max={mx.red} color="var(--red)" /></td>
                  <td><span className="num">{fmtNum(r.yellow)}</span><MiniBar value={r.yellow} max={mx.yellow} color="var(--yellow)" /></td>
                  <td><span className="num">{fmtNum(r.open_alerts)}</span><MiniBar value={r.open_alerts} max={mx.open} color="var(--blue)" /></td>
                  <td><span className={`num${r.overdue_alerts > 0 ? ' ad-bad' : ''}`}>{fmtNum(r.overdue_alerts)}</span><MiniBar value={r.overdue_alerts} max={mx.overdue} color="var(--red)" /></td>
                  <td><span className={`num${r.median_response_min !== null && r.median_response_min > 30 ? ' ad-bad' : ''}`}>{mins(r.median_response_min)}</span><MiniBar value={Math.min(r.median_response_min ?? 0, 60)} max={60} color={r.median_response_min !== null && r.median_response_min > 30 ? 'var(--red)' : 'var(--green-fill)'} /></td>
                  <td><span className="num">{pct(r.answered_in_sla_pct)}</span><MiniBar value={r.answered_in_sla_pct ?? 0} max={100} color="var(--green-fill)" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------- shell
export default function AdminScreen() {
  const nav = useNavigate()
  const session = getSession()
  const ov = useOverview()
  if (!session) return <Navigate to="/kirish" replace />
  const logout = async () => { await account.logout(); nav('/', { replace: true }) }
  const pending = ov.data?.kpis.pending_users ?? 0

  return (
    <div className="ad">
      <aside className="ad-side ob-glass">
        <Link to="/" className="brand ad-brand" aria-label="Bosh sahifa"><BrandMark /><span>Tayanch</span></Link>
        <span className="ad-role ob-eyebrow">Administrator</span>
        <nav className="ad-nav" aria-label="Admin boʻlimlari">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? 'on' : '')}>
              <Icon name={n.icon} size={19} /><span>{n.label}</span>
              {n.to === '/admin/foydalanuvchilar' && pending > 0 && <b className="ad-count num">{pending}</b>}
            </NavLink>
          ))}
          <span className="ad-nav-sep" />
          {LINKS.map((n) => (
            <Link key={n.to} to={n.to}><Icon name={n.icon} size={19} /><span>{n.label}</span><Icon name="external" size={14} className="ad-ext" /></Link>
          ))}
        </nav>
        <div className="ad-side-foot">
          <Link to="/hisob" className="ad-me" title="Hisob xavfsizligi">
            <span className="avatar">{initials(session.user.name || 'A') || 'A'}</span>
            <span className="grow col" style={{ gap: 0 }}><span className="ad-me-n">{session.user.name || 'Administrator'}</span><span className="tiny muted">Hisob xavfsizligi</span></span>
          </Link>
          <div className="row" style={{ gap: 8 }}>
            <ThemeToggle />
            <button className="ob-icon-btn" onClick={logout} aria-label="Chiqish" title="Chiqish"><Icon name="logout" size={17} /></button>
          </div>
        </div>
      </aside>

      <main className="ad-main fade-in">
        <Routes>
          <Route index element={<Overview ov={ov} />} />
          <Route path="hududlar" element={<Regions ov={ov} />} />
          <Route path="muassasalar" element={<><PageHead title="Muassasalar" sub="Har bir muassasada bitta administrator. Xodimlarni faqat u qoʻshadi." /><AdminFacilities onChanged={ov.reload} /></>} />
          <Route path="foydalanuvchilar" element={<><PageHead title="Foydalanuvchilar" sub="Rahbar va administrator rollari. Hamshira va shifokorlarni faqat muassasa administratori qoʻshadi." /><AdminUsers onChanged={ov.reload} /></>} />
          <Route path="hisobotlar" element={<><PageHead title="Hisobotlar" sub="Faqat jamlangan sonlar. 5 dan kichik guruhlar yashiriladi." /><AdminReports ov={ov} /></>} />
          <Route path="audit" element={<><PageHead title="Audit" sub="Har bir harakat oʻzgartirib boʻlmaydigan zanjirga yoziladi." /><AdminAudit /></>} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </main>
    </div>
  )
}
