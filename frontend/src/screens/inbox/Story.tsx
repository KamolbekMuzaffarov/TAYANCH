import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { InboxItem, NewbornDetail, PatientDetail, Zone } from '../../lib/api'
import { ErrorNote, ZONE_COLOR, ZoneBadge, ZoneIcon, ZoneOrb, countdown, fmtDate, usePoll, useTick } from '../../ui/kit'
import { getSession } from '../../lib/api'
import { Icon } from '../../ui/icons'
import { LIST_PATH, MOCK, ignoreKey, src } from './data'

type Decision = 'refer' | 'recheck' | 'no_risk'
type AlertRow = PatientDetail['alerts'][number]

const num = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1)).replace('.', ',')
const signed = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${num(Math.abs(Math.round(v * 10) / 10))}`
const clock = (iso: string) => { const d = new Date(iso); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}` }
const join = (parts: (string | null | false | undefined)[]) => parts.filter(Boolean).join(' · ')

const STATUS: Record<string, string> = { open: 'Javob kutilmoqda', acknowledged: 'Qabul qilingan', resolved: 'Yopilgan' }
const RESOLUTION: Record<string, string> = { refer: 'Statsionarga yoʻnaltirildi', recheck: '24 soatda qayta koʻrik', no_risk: 'Xavf yoʻq' }
const SOURCE: Record<string, string> = { voice: 'ovoz', manual: 'qoʻlda' }
const cap = (t: string) => (t ? t[0].toUpperCase() + t.slice(1) : t)
const ZRANK: Record<string, number> = { red: 0, yellow: 1, grey: 2, green: 3 }
/** Newborn visit fields in reading order; anything not listed here stays hidden so raw keys never reach the screen. */
const NB_FIELDS: [keys: string[], render: (v: number) => string][] = [
  [['temperature_c'], (v) => `Harorat ${num(v)} °C`],
  [['hr', 'heart_rate'], (v) => `puls ${num(v)}`],
  [['rr', 'respiratory_rate'], (v) => `nafas ${num(v)}`],
  [['spo2'], (v) => `kislorod ${num(v)}%`],
  [['weight_g'], (v) => `vazn ${num(v)} g`],
]
function newbornVisitLine(v: Record<string, unknown>): string {
  const parts: string[] = []
  for (const [keys, render] of NB_FIELDS) {
    const x = keys.map((k) => v[k]).find((x) => typeof x === 'number')
    if (typeof x === 'number') parts.push(render(x))
  }
  if (Array.isArray(v.symptoms)) parts.push(...v.symptoms.filter((x): x is string => typeof x === 'string'))
  return parts.join(' · ') || 'Oʻlchovlar kiritilmagan'
}

// ---------------------------------------------------------------- chart
interface Series { values: number[]; color: string }
interface Band { value: number; color: string }

function Chart({ series, bands = [], label }: { series: Series[]; bands?: Band[]; label: string }) {
  const W = 240, H = 92, L = 6, R = 30, T = 8, B = 8
  const all = [...series.flatMap((s) => s.values), ...bands.map((b) => b.value)]
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1
  const n = Math.max(...series.map((s) => s.values.length))
  const x = (i: number) => (n === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / (n - 1))
  const y = (v: number) => H - B - ((v - lo) / span) * (H - T - B)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
      {bands.map((b) => (
        <g key={b.value}>
          <line x1={0} x2={W - R + 4} y1={y(b.value)} y2={y(b.value)} stroke={b.color} strokeWidth="1" strokeDasharray="3 3" opacity=".8" />
          <text x={W} y={y(b.value) + 3.5} textAnchor="end" fontSize="10" fontWeight="600" fill={b.color}>{b.value}</text>
        </g>
      ))}
      {series.map((s, k) => (
        <g key={k}>
          <path d={s.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')}
            fill="none" stroke={s.color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          {s.values.map((v, i) => {
            const last = i === s.values.length - 1
            return <circle key={i} cx={x(i)} cy={y(v)} r={last ? 3.6 : 2.2} fill={last ? s.color : 'var(--dot-bg)'} stroke={s.color} strokeWidth="1.6" />
          })}
        </g>
      ))}
    </svg>
  )
}

function ChartCard({ title, value, unit, delta, weeks, children }:
  { title: string; value: string | null; unit: string; delta: string | null; weeks: (number | null)[]; children: ReactNode }) {
  const first = weeks[0], last = weeks[weeks.length - 1]
  return (
    <div className="ibx-chart">
      <span className="ibx-chart-t">{title}</span>
      {value === null ? <p className="ibx-chart-none">Oʻlchov yoʻq</p> : (
        <>
          <div className="ibx-chart-v">
            <b>{value}</b><span>{unit}</span>
            {delta && <span className="ibx-delta" title="Birinchi oʻlchovga nisbatan">{delta}</span>}
          </div>
          {children}
          <div className="ibx-chart-x">
            <span>{first != null ? `${first}-hafta` : ''}</span>
            <span>{weeks.length > 1 && last != null ? `${last}-hafta` : ''}</span>
          </div>
        </>
      )}
    </div>
  )
}

function Trends({ d }: { d: PatientDetail }) {
  const { bp, hb, weight, flags } = d.trend
  const ink = 'var(--ink)', soft = 'var(--ink-3)'
  const lastBp = bp[bp.length - 1], lastHb = hb[hb.length - 1], lastW = weight[weight.length - 1]
  const si = d.shock_index
  return (
    <section>
      <div className="ibx-h">
        <span>Oʻzgarish</span>
        {si != null && (
          <span className={`ibx-chip${si >= 0.9 ? ' warn' : ''}`}>
            <Icon name="activity" size={13} stroke={2} />Shok indeksi {si.toFixed(2).replace('.', ',')}{si >= 0.9 ? ' — yuqori' : ''}
          </span>
        )}
      </div>
      <div className="ibx-charts">
        <ChartCard title="Bosim" value={lastBp ? `${lastBp.sbp}/${lastBp.dbp}` : null} unit="mm"
          delta={bp.length > 1 ? signed(lastBp.sbp - bp[0].sbp) : null} weeks={bp.map((p) => p.week)}>
          <Chart label={`Bosim: ${bp.map((p) => `${p.sbp}/${p.dbp}`).join(', ')}`}
            series={[{ values: bp.map((p) => p.dbp), color: soft }, { values: bp.map((p) => p.sbp), color: ink }]}
            bands={[{ value: 140, color: ZONE_COLOR.yellow }, { value: 160, color: ZONE_COLOR.red }]} />
        </ChartCard>
        <ChartCard title="Gemoglobin" value={lastHb ? num(lastHb.value) : null} unit="g/l"
          delta={hb.length > 1 ? signed(lastHb.value - hb[0].value) : null} weeks={hb.map((p) => p.week)}>
          <Chart label={`Gemoglobin: ${hb.map((p) => p.value).join(', ')}`} series={[{ values: hb.map((p) => p.value), color: ink }]}
            bands={[{ value: 100, color: ZONE_COLOR.yellow }, { value: 70, color: ZONE_COLOR.red }]} />
        </ChartCard>
        <ChartCard title="Vazn" value={lastW ? num(lastW.value) : null} unit="kg"
          delta={weight.length > 1 ? signed(lastW.value - weight[0].value) : null} weeks={weight.map((p) => p.week)}>
          <Chart label={`Vazn: ${weight.map((p) => p.value).join(', ')}`} series={[{ values: weight.map((p) => p.value), color: ink }]} />
        </ChartCard>
      </div>
      {flags.map((f, i) => (
        <div className="ibx-flag" key={i}>
          <Icon name="trending-up" size={18} />
          <div><b>Erta ogohlantirish</b><span>{f.text}</span></div>
        </div>
      ))}
    </section>
  )
}

function NewbornStats({ d }: { d: NewbornDetail }) {
  const p = d.patient
  const cell = (label: string, v: number | null, unit: string) => (
    <div className="ibx-stat"><span>{label}</span><b>{v == null ? '—' : <>{v.toLocaleString('ru-RU')}<small>{unit}</small></>}</b></div>
  )
  return (
    <section>
      <div className="ibx-h"><span>Tugʻilish maʼlumotlari</span></div>
      <div className="ibx-stats">
        {cell('Tugʻilgandagi vazn', p.birth_weight_g, 'g')}
        {cell('Tugʻilgan muddati', p.gestational_age_weeks, 'hafta')}
        {cell('Apgar (5 daqiqa)', p.apgar_5, 'ball')}
        {cell('Yoshi', p.age_days, 'kun')}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- details
function Details({ kind, d }: { kind: 'p' | 'n'; d: PatientDetail | NewbornDetail }) {
  const [open, setOpen] = useState(false)
  const pd = kind === 'p' ? (d as PatientDetail) : null
  const nd = kind === 'n' ? (d as NewbornDetail) : null
  return (
    <section>
      <button className="ibx-more-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name="chevron-right" size={16} stroke={2} />
        Batafsil
      </button>
      <div className={`ibx-more${open ? ' open' : ''}`} aria-hidden={!open}>
        <div className="ibx-more-in">
          {pd && pd.visits.length > 0 && (
            <div>
              <div className="ibx-h"><span>Tashriflar</span></div>
              <ol className="ibx-visits">
                {pd.visits.map((v, i) => (
                  <li className="ibx-visit" key={i}>
                    <div className="ibx-visit-when">{fmtDate(v.at)}<small>{v.week != null ? `${v.week}-hafta` : ''}</small></div>
                    <div className="ibx-visit-dot"><ZoneIcon zone={v.zone} size={18} /></div>
                    <div className="ibx-visit-main">
                      <span className="num">{join([
                        v.sbp != null && v.dbp != null && `Bosim ${v.sbp}/${v.dbp}`,
                        v.hr != null && `puls ${v.hr}`,
                        v.proteinuria && v.proteinuria !== 'neg' && `siydikda oqsil ${v.proteinuria === 'trace' ? 'izlari' : v.proteinuria}`,
                      ]) || 'Oʻlchovlar kiritilmagan'}</span>
                      {v.symptoms.length > 0 && (
                        <div className="ibx-symptoms">{v.symptoms.map((s, k) => <span className="badge" key={k}>{s}</span>)}</div>
                      )}
                      <small>{join([SOURCE[v.source] ?? v.source, v.by])}</small>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {nd && nd.visits.length > 0 && (
            <div>
              <div className="ibx-h"><span>Tashriflar</span></div>
              <ul className="ibx-lines">
                {nd.visits.map((v, i) => (
                  <li key={i}>
                    {typeof v.at === 'string' && <b>{fmtDate(v.at)}</b>}
                    <span className="num">{newbornVisitLine(v)}</span>
                    {typeof v.by === 'string' && <small>{v.by}</small>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {d.alerts.length > 0 && (
            <div>
              <div className="ibx-h"><span>Signallar tarixi</span></div>
              <ul className="ibx-lines">
                {d.alerts.map((a) => (
                  <li key={a.id}>
                    <ZoneIcon zone={a.severity} size={14} />
                    <span className="num">{fmtDate(a.created_at)}, {clock(a.created_at)}</span>
                    <span>{a.status === 'resolved' && a.resolution ? RESOLUTION[a.resolution] ?? cap(a.resolution) : STATUS[a.status] ?? 'Yopilgan'}</span>
                    {a.escalation_level > 0 && <span className="badge red">Yuqoriga koʻtarildi</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {d.action_card && (
            <div>
              <div className="ibx-h"><span>Hamshiraga berilgan koʻrsatma</span></div>
              <ol className="ibx-steps">{d.action_card.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
            </div>
          )}
          {pd && (
            <div>
              <div className="ibx-h"><span>Anamnez</span></div>
              <ul className="ibx-lines">
                {pd.patient.history.length === 0 && <li><small>Qayd etilmagan</small></li>}
                {pd.patient.history.map((h, k) => <li key={k}>{h}</li>)}
                {(pd.patient.nurse || pd.patient.doctor) && (
                  <li><small>{join([pd.patient.nurse && `Hamshira: ${pd.patient.nurse}`, pd.patient.doctor && `Shifokor: ${pd.patient.doctor}`, pd.patient.edd && `Tugʻruq muddati: ${fmtDate(pd.patient.edd)}`])}</small></li>
                )}
              </ul>
            </div>
          )}
          {pd?.ml_shadow && (
            <div className="ibx-ml">
              <b>Sinov rejimidagi model — qarorga taʼsir qilmaydi</b>
              <span>{pd.ml_shadow.label.replace(/\s*\((sinov|shadow)[^)]*\)/i, '').replace(/(\d)\.(\d)/g, '$1,$2')}</span>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- action bar
function Due({ iso }: { iso: string }) {
  useTick(1000)
  const c = countdown(iso)
  return c.overdue
    ? <span className="over num">{c.text} kechikdi</span>
    : <span>Javob muddati <span className="num">{c.text}</span></span>
}

function ActionBar({ alertId, status, resolution, slaDueAt, onAcked, onDecided }: {
  alertId: string | null; status: string | null; resolution: string | null; slaDueAt: string | null
  onAcked: () => void; onDecided: (alertId: string) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [ackMin, setAckMin] = useState<number | null>(null)
  const [local, setLocal] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const state = done ? 'resolved' : local ?? status
  // only the specialist answers a signal; other roles read the story without dead buttons
  const canAct = MOCK || getSession()?.user.persona === 'specialist'

  const ack = async () => {
    if (!alertId || busy) return
    setBusy('ack'); setErr(null)
    try { const r = await src.ack(alertId); setAckMin(r.response_minutes); setLocal('acknowledged'); onAcked() }
    catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }
  const decide = async (d: Decision) => {
    if (!alertId || busy) return
    setBusy(d); setErr(null)
    try { const r = await src.decide(alertId, d); setDone(r.message || RESOLUTION[d]); onDecided(alertId) }
    catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  const fns = useRef({ ack, decide, state, canAct }); fns.current = { ack, decide, state, canAct }
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (ignoreKey(e) || e.repeat) return
      const f = fns.current, k = e.key.toLowerCase()
      if (!f.canAct) return
      if (k === 'a' && f.state === 'open') { e.preventDefault(); f.ack() }
      else if (f.state === 'acknowledged' && (k === '1' || k === '2' || k === '3')) {
        e.preventDefault(); f.decide(k === '1' ? 'refer' : k === '2' ? 'recheck' : 'no_risk')
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])

  let body: ReactNode
  if (done) {
    body = <div className="ibx-done fade-in" role="status"><ZoneOrb zone="green" size={30} />{done}</div>
  } else if (!alertId || !state) {
    body = <div className="ibx-done quiet">Ochiq signal yoʻq</div>
  } else if (!canAct && state !== 'resolved') {
    body = <div className="ibx-done quiet">{state === 'open' ? 'Mutaxassis javobi kutilmoqda' : 'Mutaxassis qabul qilgan — qaror kutilmoqda'}</div>
  } else if (state === 'open') {
    body = (
      <>
        {slaDueAt && <div className="ibx-bar-note"><Due iso={slaDueAt} /></div>}
        <button className="btn big block" disabled={busy !== null} onClick={ack}>
          {busy === 'ack' ? 'Yuborilmoqda…' : <><Icon name="check" size={22} stroke={2.2} />Qabul qildim</>}<span className="ibx-kbd">A</span>
        </button>
      </>
    )
  } else if (state === 'acknowledged') {
    body = (
      <>
        <div className="ibx-bar-note fade-in">
          <span>{ackMin != null ? `${ackMin} daqiqada javob berildi` : 'Qabul qilingan'} · Qaroringiz?</span>
        </div>
        <div className="ibx-decide fade-in">
          <button className="btn" disabled={busy !== null} onClick={() => decide('refer')}>Statsionarga yoʻnaltirish<span className="ibx-kbd">1</span></button>
          <button className="btn soft" disabled={busy !== null} onClick={() => decide('recheck')}>24 soatda qayta koʻrik<span className="ibx-kbd">2</span></button>
          <button className="btn soft" disabled={busy !== null} onClick={() => decide('no_risk')}>Xavf yoʻq<span className="ibx-kbd">3</span></button>
        </div>
      </>
    )
  } else {
    body = <div className="ibx-done quiet">Signal yopilgan{resolution ? ` — ${RESOLUTION[resolution] ?? resolution}` : ''}</div>
  }
  return (
    <div className="ibx-bar">
      <div className="ibx-bar-in">
        {body}
        {err && <p className="ibx-err" role="alert">{err}</p>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- story
function pickAlert(alerts: AlertRow[], signal: string | null): AlertRow | undefined {
  if (signal) { const a = alerts.find((x) => x.id === signal); if (a) return a }
  return alerts.filter((a) => a.status !== 'resolved').sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
}

function Skeleton() {
  return (
    <div className="ibx-page" aria-busy="true" aria-label="Yuklanmoqda">
      <div className="skel" style={{ height: 44, width: '55%' }} />
      <div className="skel" style={{ height: 150, borderRadius: 28 }} />
      <div className="ibx-charts">{[0, 1, 2].map((i) => <div key={i} className="skel" style={{ height: 180, borderRadius: 22 }} />)}</div>
    </div>
  )
}

export function Story({ kind, id, signal, item, onChanged, onDecided }: {
  kind: 'p' | 'n'; id: string; signal: string | null; item: InboxItem | undefined
  onChanged: () => void; onDecided: (alertId: string) => void
}) {
  const { data, error, loading, refresh } = usePoll<PatientDetail | NewbornDetail>(
    () => (kind === 'p' ? src.patient(id) : src.newborn(id)), 10_000, [kind, id])
  const stick = useRef<string | null>(null) // keep acting on the same alert after it resolves

  const back = (
    <Link className="ibx-back" to={LIST_PATH}>
      <Icon name="chevron-left" size={18} stroke={2} />
      Roʻyxat
    </Link>
  )

  if (!data) {
    return (
      <>
        <div className="ibx-scroll">
          {loading ? <Skeleton /> : <div className="ibx-page">{back}<ErrorNote message={error ?? 'Maʼlumot topilmadi'} onRetry={refresh} /></div>}
        </div>
      </>
    )
  }

  const alert = pickAlert(data.alerts, signal ?? stick.current)
  const alertId = alert?.id ?? signal ?? item?.alert_id ?? null
  if (alertId) stick.current = alertId
  const status = alert?.status ?? item?.status ?? null
  // while a signal is waiting, the story carries that signal's colour (the list and the story must agree)
  const zone: Zone = alert && alert.status !== 'resolved' && (ZRANK[alert.severity] ?? 9) < (ZRANK[data.zone] ?? 9) ? alert.severity : data.zone
  const pd = kind === 'p' ? (data as PatientDetail) : null
  const nd = kind === 'n' ? (data as NewbornDetail) : null

  const sub = pd
    ? join([
      pd.patient.age != null && `${pd.patient.age} yosh`,
      pd.patient.weeks != null && `${pd.patient.weeks} hafta`,
      pd.patient.gravida != null && `${pd.patient.gravida}-homiladorlik`,
      [pd.patient.district_name, pd.patient.mahalla].filter(Boolean).join(', '),
    ])
    : join([nd && `${nd.patient.age_days} kunlik chaqaloq`, nd?.patient.district_name])

  return (
    <>
      <div className="ibx-scroll">
        <div className="ibx-page fade-in">
          {back}
          <header className="ibx-phead">
            <div>
              <h1>{data.patient.name}</h1>
              <p>{sub}</p>
              <div className="ibx-code">{data.patient.code}</div>
            </div>
            <ZoneBadge zone={zone} />
          </header>

          <section className={`ibx-sentence ${zone}`}>
            <p>{data.summary}</p>
            {data.reasons.length > 0 && (
              <ul className="ibx-reasons">
                {data.reasons.map((r, i) => (
                  <li key={`${r.code}:${i}`}>
                    <ZoneIcon zone={r.severity} size={14} />
                    <span>{r.text}{r.reference && <small>{r.reference}</small>}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {pd && <Trends d={pd} />}
          {nd && <NewbornStats d={nd} />}
          <Details kind={kind} d={data} />
        </div>
      </div>
      <ActionBar key={alertId ?? 'none'} alertId={alertId} status={status} resolution={alert?.resolution ?? null}
        slaDueAt={item?.alert_id === alertId ? item?.sla_due_at ?? null : null}
        onAcked={() => { onChanged(); refresh() }} onDecided={onDecided} />
    </>
  )
}
