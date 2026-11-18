import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type AiPrivacy, type ModelCard } from '../lib/api'
import { TopBar } from '../ui/TopBar'
import { ErrorNote, ZoneBadge, ZoneIcon, usePoll } from '../ui/kit'
import { Icon, type IconName } from '../ui/icons'
import './power.css'

const TABS = [
  { id: 'qoidalar', icon: 'file', label: 'Klinik qoidalar' },
  { id: 'audit', icon: 'list', label: 'Audit jurnali' },
  { id: 'maxfiylik', icon: 'lock', label: 'Maxfiylik' },
  { id: 'modellar', icon: 'chart', label: 'Modellar' },
  { id: 'xavfsizlik', icon: 'shield', label: 'Xavfsizlik' },
  { id: 'api', icon: 'external', label: 'API' },
] as const satisfies readonly { id: string; icon: IconName; label: string }[]

/** undefined = still loading, null = not available on this backend (the tab is hidden). */
function useOptional<T>(fn: () => Promise<T>): T | null | undefined {
  const [v, setV] = useState<T | null | undefined>(undefined)
  useEffect(() => {
    let alive = true
    fn().then((r) => { if (alive) setV(r) }).catch(() => { if (alive) setV(null) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return v
}

/** Depth for power users: rules with sources, tamper-evident audit trail, privacy, models, API. Reached via ⌘K. */
export default function PowerScreen() {
  const [sp, setSp] = useSearchParams()
  const tab = sp.get('tab') || 'qoidalar'
  const privacy = useOptional<AiPrivacy>(() => api.aiPrivacy())
  const models = useOptional<ModelCard[]>(() => api.models())
  const hidden = new Set<string>()
  if (privacy === null) hidden.add('maxfiylik')
  if (models === null || (models && models.length === 0)) hidden.add('modellar')
  const calm = <p className="empty">Bu boʻlim hozircha mavjud emas.</p>
  return (
    <div style={{ minHeight: '100%' }}>
      <TopBar />
      <main className="col fade-in pw-main">
        <div className="col" style={{ gap: 8 }}>
          <h1 className="pw-title">Batafsil</h1>
          <p className="muted pw-lead">Sodda ekranlar ortidagi mexanizm: qoidalar, manbalar, iz va integratsiya.</p>
        </div>
        <div className="tabs pw-tabs" role="tablist">
          {TABS.filter((t) => !hidden.has(t.id)).map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id}
              onClick={() => setSp({ tab: t.id })}><Icon name={t.icon} size={15} />{t.label}</button>
          ))}
        </div>
        {tab === 'qoidalar' && <Rules />}
        {tab === 'audit' && <Audit />}
        {tab === 'maxfiylik' && (privacy === undefined ? <div className="skel" style={{ height: 320 }} /> : privacy ? <Privacy p={privacy} /> : calm)}
        {tab === 'modellar' && (models === undefined ? <div className="skel" style={{ height: 320 }} /> : models && models.length ? <Models items={models} /> : calm)}
        {tab === 'xavfsizlik' && <Security />}
        {tab === 'api' && <ApiInfo />}
      </main>
    </div>
  )
}

function Rules() {
  const { data, error, loading, refresh } = usePoll(() => api.rules(), 0)
  if (loading) return <div className="skel" style={{ height: 320 }} />
  if (error || !data) return <ErrorNote message={error || ''} onRetry={refresh} />
  const groups = [
    { key: 'pregnancy', title: 'Homilador ayol' },
    { key: 'newborn', title: 'Chaqaloq' },
  ]
  return (
    <div className="col" style={{ gap: 20 }}>
      <p className="small muted">Har bir signal quyidagi ochiq qoidalardan biridan kelib chiqadi. Qoidalar xalqaro qoʻllanmalarga asoslangan va klinik kengash tasdigʻini kutmoqda. Model emas, qoida ogohlantiradi.</p>
      {groups.map((g) => (
        <section key={g.key} className="card col" style={{ gap: 0, padding: '8px 22px' }}>
          <h3 className="row" style={{ padding: '16px 0 10px', gap: 9 }}><Icon name={g.key === 'newborn' ? 'baby' : 'heart-pulse'} size={19} />{g.title}</h3>
          {data.items.filter((r) => r.subject_type === g.key).map((r) => (
            <div key={r.rule_id} title={`Qoida kodi: ${r.rule_id}`} className="row" style={{ padding: '12px 0', borderTop: '1px solid var(--line)', alignItems: 'flex-start', flexWrap: 'wrap', rowGap: 6 }}>
              <ZoneBadge zone={r.severity} />
              <div className="col" style={{ gap: 2, flex: '1 1 220px', minWidth: 0 }}>
                <span style={{ fontWeight: 600 }}>{r.label}</span>
                <span className="small muted" style={{ overflowWrap: 'anywhere' }}>{r.reference}</span>
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  )
}

function Audit() {
  const { data, error, loading, refresh } = usePoll(() => api.audit(80), 8000)
  if (loading) return <div className="skel" style={{ height: 320 }} />
  if (error || !data) return <ErrorNote message={error || ''} onRetry={refresh} />
  return (
    <div className="col">
      <div className="row" style={{ flexWrap: 'wrap', rowGap: 6 }}>
        <ZoneBadge zone={data.chain_ok ? 'green' : 'red'} label={data.chain_ok ? 'Zanjir butun: yozuvlar oʻzgartirilmagan' : 'Zanjir buzilgan'} />
        <span className="small muted">Har bir yozuv oldingisining xeshi bilan bogʻlangan.</span>
      </div>
      <section className="card pw-rows">
        {data.items.map((a, i) => (
          <div key={i} className="pw-row">
            <span className="num pw-when">{when(a.at)}</span>
            <span className="pw-who">{a.actor}</span>
            <span className="pw-grow">{a.action}</span>
            <span className="muted pw-break num">{resourceWord(a.resource)}</span>
          </div>
        ))}
      </section>
    </div>
  )
}

const RESOURCE_WORD: Record<string, string> = { policy: 'maxfiylik siyosati', aggregate: 'umumlashtirilgan maʼlumot', user: 'foydalanuvchi', alert: 'signal', patient: 'bemor', pregnancy: 'homiladorlik', newborn: 'chaqaloq', district: 'tuman', region: 'viloyat' }
const resourceWord = (r: string) => String(r ?? '').replace(/^[a-z_]+/i, (w) => RESOURCE_WORD[w.toLowerCase()] ?? w)

// ------------------------------------------------------------------ generic readers (payload shapes are not fixed)
type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)
const first = (o: Obj, keys: string[]): unknown => { for (const k of keys) if (o[k] != null) return o[k]; return undefined }
const human = (k: string) => { const s = k.replace(/[_.-]+/g, ' ').trim(); return s.charAt(0).toUpperCase() + s.slice(1) }
function when(v: unknown): string {
  const d = new Date(String(v ?? ''))
  return Number.isNaN(d.getTime()) ? String(v ?? '') : d.toLocaleString('uz-UZ', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
function text(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'boolean') return v ? 'ha' : 'yoʻq'
  if (typeof v === 'number') return fmtNum(v)
  if (typeof v === 'string') return v
  if (Array.isArray(v)) return v.map(text).filter(Boolean).join(', ')
  if (isObj(v)) {
    const main = first(v, ['label', 'name', 'title', 'field', 'text', 'id', 'code'])
    const note = first(v, ['description', 'reason', 'note', 'why', 'detail'])
    if (main != null) return note != null ? `${text(main)} — ${text(note)}` : text(main)
    return Object.entries(v).map(([k, x]) => `${human(k)}: ${text(x)}`).join(' · ')
  }
  return String(v)
}
function list(v: unknown): string[] {
  if (v == null) return []
  if (Array.isArray(v)) return v.map(text).filter(Boolean)
  if (isObj(v)) return Object.entries(v).map(([k, x]) => `${human(k)}: ${text(x)}`)
  return [text(v)].filter(Boolean)
}
function fmtNum(n: number): string {
  if (!Number.isFinite(n)) return '—'
  if (Number.isInteger(n)) return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return (Math.abs(n) < 10 ? n.toFixed(3).replace(/0+$/, '').replace(/\.$/, '') : n.toFixed(1)).replace('.', ',')
}

// ------------------------------------------------------------------ Maxfiylik
const SENT_KEYS = ['sent', 'sent_fields', 'sent_to_ai', 'allowed', 'allowed_fields']
const NEVER_KEYS = ['never_sent', 'never', 'never_sent_fields', 'blocked_fields', 'forbidden']
const MODEL_KEYS = ['model', 'model_name', 'provider_model']
const PLACE_KEYS = ['processing_location', 'location', 'region']
const LOG_KEYS = ['decisions', 'recent_decisions', 'log', 'events']
const EXTRA_WORD: Record<string, string> = {
  provider: 'Provayder', retention: 'Saqlash muddati', enabled: 'Tashqi AI yoqilgan', external_enabled: 'Tashqi AI yoqilgan',
  k_anonymity: 'Eng kichik guruh', min_cell_size: 'Eng kichik guruh', purpose: 'Maqsad', note: 'Izoh', policy_version: 'Siyosat versiyasi',
  generated_at: 'Yangilangan', updated_at: 'Yangilangan', feature: 'Funksiya', mode: 'Rejim', enforcement: 'Himoya choralari',
}
/** Machine values of the policy payload, in words. Unknown values are shown as they come. */
const VALUE_WORD: Record<string, string> = {
  live_ai_brief: 'AI xulosa', local: 'mahalliy (tashqi xizmatsiz)', external: 'tashqi model', not_sent: 'yuborilmadi', sent: 'yuborildi',
  blocked: 'toʻxtatildi', ok: 'bajarildi', success: 'bajarildi', error: 'xatolik', timeout: 'javob kelmadi', no_api_key: 'API kaliti yoʻq',
}
const word = (v: unknown) => { const t = text(v); return VALUE_WORD[t.toLowerCase()] ?? t }
const DECISION: Record<string, { zone: 'green' | 'yellow' | 'grey'; word: string }> = {
  allowed: { zone: 'green', word: 'Ruxsat berildi' }, allow: { zone: 'green', word: 'Ruxsat berildi' }, sent: { zone: 'green', word: 'Yuborildi' },
  blocked: { zone: 'yellow', word: 'Toʻxtatildi' }, block: { zone: 'yellow', word: 'Toʻxtatildi' }, denied: { zone: 'yellow', word: 'Toʻxtatildi' }, deny: { zone: 'yellow', word: 'Toʻxtatildi' },
  local: { zone: 'grey', word: 'Mahalliy' }, fallback: { zone: 'grey', word: 'Mahalliy' }, skipped: { zone: 'grey', word: 'Oʻtkazib yuborildi' },
}

function Privacy({ p }: { p: AiPrivacy }) {
  const sent = list(first(p, SENT_KEYS))
  const never = list(first(p, NEVER_KEYS))
  const model = text(first(p, MODEL_KEYS))
  const proc = p.processing
  const place = text(first(p, PLACE_KEYS)) || (isObj(proc) ? text(first(proc, ['patient_data', 'location'])) : '')
  const guards = Array.isArray(p.enforcement) ? p.enforcement.filter((x): x is string => typeof x === 'string') : []
  const rawLog = first(p, LOG_KEYS)
  const log = (Array.isArray(rawLog) ? rawLog : []).filter(isObj).slice(0, 40)
  const used = new Set([...SENT_KEYS, ...NEVER_KEYS, ...MODEL_KEYS, ...PLACE_KEYS, ...LOG_KEYS, ...(guards.length ? ['enforcement'] : [])])
  const extra = Object.entries(p).filter(([k, v]) => !used.has(k) && v != null && (typeof v !== 'object' || (Array.isArray(v) && v.every((x) => typeof x === 'string'))))
  return (
    <div className="col" style={{ gap: 20 }}>
      <p className="small muted">Tashqi AI faqat umumlashtirilgan sonlarni koʻradi. Bemor haqidagi maʼlumot serverdan chiqmaydi.</p>
      <div className="pw-two">
        <section className="card col">
          <span className="pw-kicker"><Icon name="send" size={14} />Tashqi AI ga yuboriladi</span>
          {sent.length ? (
            <ul className="pw-list">{sent.map((s, i) => <li key={i}><Icon name="arrow-right" size={15} />{s}</li>)}</ul>
          ) : <p className="muted">Hech narsa yuborilmaydi.</p>}
        </section>
        <section className="card col">
          <span className="pw-kicker"><Icon name="lock" size={14} />Hech qachon yuborilmaydi</span>
          {never.length ? (
            <ul className="pw-list">{never.map((s, i) => <li key={i}><Icon name="lock" size={15} />{s}</li>)}</ul>
          ) : <p className="muted">Roʻyxat berilmagan.</p>}
        </section>
      </div>

      {(model || place) && (
        <p className="pw-meta">
          {model && <span><span className="muted">Model: </span><b>{model}</b></span>}
          {place && <span><span className="muted">Qayta ishlash joyi: </span><b>{place}</b></span>}
        </p>
      )}
      {extra.length > 0 && (
        <p className="pw-meta small">
          {extra.map(([k, v]) => <span key={k}><span className="muted">{EXTRA_WORD[k] ?? human(k)}: </span>{/_at$/.test(k) ? when(v) : word(v)}</span>)}
        </p>
      )}
      {guards.length > 0 && (
        <section className="card col">
          <span className="pw-kicker">Himoya choralari</span>
          <ul className="pw-list">{guards.map((g, i) => <li key={i}><Icon name="shield" size={15} />{g}</li>)}</ul>
        </section>
      )}

      <div className="col" style={{ gap: 10 }}>
        <span className="pw-kicker">Soʻnggi qarorlar</span>
        {log.length === 0 ? <p className="muted small">Hozircha yozuv yoʻq.</p> : (
          <section className="card pw-rows">
            {log.map((d, i) => {
              const raw = String(first(d, ['decision', 'outcome', 'result']) ?? '').toLowerCase()
              const known = DECISION[raw]
              const status = word(first(d, ['status', 'http_status']))
              const why = list(first(d, ['reason', 'reasons', 'note', 'detail'])).map(word).join(', ')
              return (
                <div key={i} className="pw-row">
                  <span className="num pw-when">{when(first(d, ['at', 'created_at', 'time', 'timestamp']))}</span>
                  <span className="pw-who pw-break">{word(first(d, ['feature', 'purpose', 'task'])) || '—'}</span>
                  <span className="pw-grow" style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                    {known && <ZoneIcon zone={known.zone} size={14} label={null} />}{known ? known.word : raw || '—'}
                    {why && <span className="muted pw-break"> · {why}</span>}
                  </span>
                  <span className="muted pw-break">{[text(first(d, ['model', 'provider'])), status].filter(Boolean).join(' · ')}</span>
                </div>
              )
            })}
          </section>
        )}
      </div>
    </div>
  )
}
// ------------------------------------------------------------------ Modellar
const MODEL_NAME: [RegExp, string][] = [
  [/fetal.*ctg/i, 'Homila yurak urishi (KTG) modeli'],
  [/maternal.*baseline/i, 'Onalik xavfi — bazaviy model'],
  [/maternal/i, 'Onalik xavfi modeli'],
  [/newborn|neonat/i, 'Chaqaloq xavfi modeli'],
]
const METRIC_WORD: [RegExp, string][] = [
  [/\b(n|rows|test rows|samples)$/i, 'Test namunalari'], [/accuracy/i, 'Aniqlik'], [/macro f1|f1/i, 'Macro F1'],
  [/high risk recall/i, 'Yuqori xavfni topish'], [/recall/i, 'Sezgirlik'], [/roc auc/i, 'ROC AUC'], [/pr auc/i, 'PR AUC'], [/brier/i, 'Brier xatosi'],
]
/** "test_all_inputs macro_f1" → "Macro F1 · barcha belgilar": metric names in words, never raw keys. */
function metricWord(k: string): string {
  const t = k.replace(/[_.-]+/g, ' ').trim()
  const base = METRIC_WORD.find(([re]) => re.test(t))?.[1] ?? human(t.replace(/^test\s+/i, ''))
  const scope = /core6/i.test(t) ? ' · 6 asosiy belgi' : /all inputs/i.test(t) ? ' · barcha belgilar' : ''
  return base + scope
}

function metricChips(m: unknown): { k: string; v: string }[] {
  const out: { k: string; v: string }[] = []
  const push = (k: string, v: unknown) => {
    if (typeof v === 'number') out.push({ k, v: fmtNum(v) })
    else if (typeof v === 'string' && v.length <= 24) out.push({ k, v })
    else if (typeof v === 'boolean') out.push({ k, v: v ? 'ha' : 'yoʻq' })
  }
  if (Array.isArray(m)) {
    for (const it of m) if (isObj(it)) push(text(first(it, ['name', 'label', 'metric', 'key'])), first(it, ['value', 'score']))
  } else if (isObj(m)) {
    for (const [k, v] of Object.entries(m)) {
      if (isObj(v)) for (const [k2, v2] of Object.entries(v)) push(`${k} ${k2}`, v2)
      else push(k, v)
    }
  }
  return out.filter((c) => c.k).slice(0, 8)
}

function Models({ items }: { items: ModelCard[] }) {
  return (
    <div className="col" style={{ gap: 20 }}>
      <p className="small muted">Modellar sinov rejimida ishlaydi: natijasi yoziladi, lekin signal bermaydi. Ogohlantirish faqat klinik qoidadan chiqadi.</p>
      <div className="pw-models">
        {items.map((m, i) => {
          const ds = m.dataset
          const dataset = isObj(ds) ? text(first(ds, ['name', 'label', 'title', 'source'])) : text(ds)
          const rows = first(m, ['rows', 'n_rows', 'samples']) ?? (isObj(ds) ? first(ds, ['rows', 'n_rows', 'samples']) : undefined)
          const status = String(m.status ?? '')
          const badge = !status || /shadow|sinov|test|pilot|experiment|research/i.test(status) ? 'sinov rejimi' : human(status).toLowerCase()
          const limits = list(first(m, ['caveat', 'caveats', 'warning', 'limitation', 'limitations']))
          const code = text(first(m, ['name', 'title', 'id']))
          const title = MODEL_NAME.find(([re]) => re.test(code))?.[1] ?? (code || 'Model')
          const about = text(first(m, ['description', 'purpose', 'task', 'summary']))
          const chips = metricChips(m.metrics)
          return (
            <section key={i} className="card col pw-model">
              <div className="row spread" style={{ alignItems: 'flex-start', gap: 10 }}>
                <div className="col" style={{ gap: 2, minWidth: 0 }}>
                  <h3>{title}</h3>
                  {(m.version != null || code) && <span className="tiny muted num" style={{ overflowWrap: 'anywhere' }}>{m.version != null ? `versiya ${text(m.version)}` : code}</span>}
                </div>
                <span className="badge">{badge}</span>
              </div>
              {about && <p className="small">{about}</p>}
              {(dataset || rows != null) && (
                <p className="small muted" style={{ overflowWrap: 'anywhere' }}>
                  {dataset && <>Maʼlumot toʻplami: <span style={{ color: 'var(--ink)' }}>{dataset}</span></>}
                  {dataset && rows != null && ' · '}
                  {rows != null && <span className="num">{text(rows)} qator</span>}
                </p>
              )}
              {chips.length > 0 && (
                <div className="pw-chips">{chips.map((c) => <span key={c.k} className="pw-chip"><b className="num">{c.v}</b><span>{metricWord(c.k)}</span></span>)}</div>
              )}
              {limits.length > 0 && (
                <div className="col" style={{ gap: 4 }}>
                  <span className="pw-kicker" style={{ fontSize: 12 }}>Cheklovlar</span>
                  {limits.length === 1 ? <p className="small muted">{limits[0]}</p>
                    : <ul className="pw-list small muted" style={{ gap: 4 }}>{limits.map((l, j) => <li key={j}><Icon name="minus" size={14} />{l}</li>)}</ul>}
                </div>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ Xavfsizlik (static summary)
const SEC: { title: string; body: string; icon: IconName }[] = [
  { title: 'Shifrlash', icon: 'lock', body: 'Ism va boshqa shaxsiy maydonlar bazada maydon darajasida shifrlangan holda saqlanadi.' },
  { title: 'Rollar', icon: 'users', body: 'Hamshira oʻz bemorlarini, mutaxassis oʻz tumanini, rahbar faqat umumlashtirilgan sonlarni koʻradi.' },
  { title: 'Audit zanjiri', icon: 'layers', body: 'Har bir amal jurnalga yoziladi va oldingi yozuv xeshi bilan bogʻlanadi. Oʻzgartirilsa, darhol bilinadi.' },
  { title: 'k-anonimlik', icon: 'eye-off', body: 'Guruhda 5 tadan kam odam boʻlsa, son yashiriladi. Kichik guruhdan odamni tanib boʻlmaydi.' },
  { title: 'Demo-kirish cheklovi', icon: 'shield', body: 'Parolsiz kirish faqat namoyish rejimida va faqat sunʼiy maʼlumot bilan ishlaydi. Ishchi muhitda oʻchirilgan.' },
]

function Security() {
  return (
    <div className="col" style={{ gap: 20 }}>
      <p className="small muted">Bemor maʼlumoti qanday himoyalanadi — besh qisqa qoida.</p>
      <div className="pw-sec">
        {SEC.map((s) => (
          <section key={s.title} className="card col hover">
            <span className="pw-sec-ic"><Icon name={s.icon} size={20} /></span>
            <h3>{s.title}</h3>
            <p className="small muted">{s.body}</p>
          </section>
        ))}
      </div>
      <p className="small muted">Texnik tafsilotlar: <a href="/api/docs" target="_blank" rel="noreferrer" style={{ color: 'var(--blue)' }}>/api/docs</a></p>
    </div>
  )
}

function ApiInfo() {
  const rows = [
    ['GET', '/api/v1/live/map', 'Barcha tumanlarning jonli holati'],
    ['GET', '/api/v1/live/stream', 'Hodisalar oqimi (SSE)'],
    ['POST', '/api/v1/live/visits', 'Tashrif natijasini saqlash va xavfni baholash'],
    ['POST', '/api/v1/live/voice/parse', 'Oʻzbekcha nutq matnidan qiymatlarni ajratish'],
    ['GET', '/api/v1/live/inbox', 'Javob kutayotgan signallar'],
    ['POST', '/api/v1/live/alerts/{id}/ack', 'Signalni qabul qilish'],
    ['GET', '/api/v1/indicators', 'SIAT va MICS koʻrsatkichlari (manbasi bilan)'],
  ]
  return (
    <div className="col">
      <p className="small muted">Toʻliq interaktiv hujjat: <a href="/api/docs" target="_blank" rel="noreferrer" style={{ color: 'var(--blue)' }}>/api/docs</a>. Maʼlumot modeli FHIR resurslariga moslashtirish uchun tayyorlangan; bemor maʼlumoti tashqi AI xizmatlariga yuborilmaydi.</p>
      <section className="card pw-rows">
        {rows.map((r, i) => (
          <div key={i} className="pw-row" style={{ alignItems: 'center' }}>
            <span className="kbd" style={{ width: 52, textAlign: 'center', flex: 'none' }}>{r[0]}</span>
            <code className="pw-path">{r[1]}</code>
            <span className="muted pw-grow">{r[2]}</span>
          </div>
        ))}
      </section>
    </div>
  )
}
