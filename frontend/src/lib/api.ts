// Typed client for the live layer. Shapes mirror docs/API_CONTRACT.md — do not change them here.
export type Zone = 'red' | 'yellow' | 'green' | 'grey'
export type Persona = 'nurse' | 'specialist' | 'leader'

export type Role = 'admin' | 'leader' | 'facility_admin' | 'specialist' | 'nurse' | 'pending'
export interface User {
  id: string; name: string; persona: Persona | 'facility_admin'; role_label: string
  district_code: string | null; district_name: string | null
  region_soato: string | null; region_name: string | null
  /** Present on account sessions; demo sessions may lack it — use `roleOf`. */
  role?: Role; email?: string | null; phone?: string | null; two_step?: boolean
}
export interface Reason { code: string; text: string; severity: Zone; reference?: string }
export interface ActionCard { title: string; steps: string[] }

// ---- leader
export interface MapTotals {
  pregnant: number; red: number; yellow: number; green: number; grey: number
  waiting_mothers: number; waiting_newborns: number; overdue: number
  median_response_min: number | null; answered_24h: number; newborns: number
}
export interface DistrictLive {
  code: string; name: string; region_soato: string
  pregnant: number; red: number; yellow: number; green: number; grey: number
  open_alerts: number; overdue_alerts: number; newborn_alerts: number; status: Zone
}
export interface RegionLive {
  soato: string; name: string; pregnant: number; red: number; yellow: number
  open_alerts: number; overdue_alerts: number; official_status: Zone
}
export interface MapData { generated_at: string; totals: MapTotals; districts: DistrictLive[]; regions: RegionLive[] }
export interface DistrictCase {
  alert_id: string; pregnancy_id: string | null; code: string; severity: Zone; status: string
  summary: string; waiting_minutes: number; sla_due_at: string; escalation_level: number; responsible: string
}
export interface DistrictDetail {
  district: { code: string; name: string; region_name: string }
  counts: { pregnant: number; red: number; yellow: number; green: number; grey: number; newborns: number }
  response: { open_alerts: number; overdue_alerts: number; median_response_min: number | null; answered_7d: number }
  cases: DistrictCase[]; nurses: number
  /** Optional SIAT block (latest values + short series). Shape may vary — normalise with `officialIndicators`. */
  official?: unknown
}
export interface OfficialIndicator {
  code: string; label: string; value: number | null; national: number | null; unit: string
  series: { year: number; value: number }[]
}
export interface RegionOfficial {
  region: { soato: string; name: string }; year: number; status: Zone; indicators: OfficialIndicator[]; source: string
}
export interface LiveEvent {
  id: number; type: 'visit' | 'alert' | 'ack' | 'escalation' | 'resolved' | 'newborn'
  at: string; zone: Zone; district_code: string; district_name: string; region_soato: string; text: string
}

// ---- nurse
export interface NurseTask {
  pregnancy_id: string; name: string; age: number; weeks: number; mahalla: string; zone: Zone
  reason: string; due: 'overdue' | 'today' | 'soon'; last_visit_days: number; trend_flag: boolean; newborn: boolean
}
export interface NurseToday {
  nurse: { name: string; district_name: string }; date: string; done_today: number; tasks: NurseTask[]
}
export interface VisitFields {
  systolic_bp: number | null; diastolic_bp: number | null; heart_rate: number | null
  temperature_c: number | null; weight_kg: number | null; hemoglobin_g_l: number | null
  glucose_mmol_l: number | null; proteinuria: string | null
  severe_headache: boolean; visual_disturbance: boolean; vaginal_bleeding: boolean
  reduced_fetal_movements: boolean; convulsions: boolean
}
export interface VoiceParse { fields: VisitFields; understood: string[]; confidence: 'high' | 'low' }
export interface VisitResult {
  zone: Zone; title: string; message: string; reasons: Reason[]; trend: string[]
  action_card: ActionCard | null
  alert: { id: string; sent_to: string; sla_minutes: number } | null
  recheck_minutes: number | null; next_visit_days: number | null
}

// ---- extended record (labs, diagnoses, mood) and JSHSHIR lookup
export interface CodeLabel { code: string; label: string }
export interface LabMeta { code: string; label: string; unit: string; min: number; max: number; normal_low: number | null; normal_high: number | null }
export interface RecordCatalog {
  labs: LabMeta[]; diagnoses: CodeLabel[]; complaints: CodeLabel[]; causes: CodeLabel[]
  epds: { questions: string[]; options: string[]; period: string }; stress: string[]
}
export interface DetailsBody {
  pregnancy_id: string; gestational_week?: number | null; labs: { code: string; value: number }[]
  diagnoses: string[]; epds: number[] | null; stress_level: number | null
  complaints: string[]; complaint_note?: string | null; causes: string[]; cause_note?: string | null
}
export interface LabValue { code: string; label: string; value: number; unit: string; flag: 'low' | 'high' | null }
export interface DetailsResult {
  ok: boolean; weeks: number | null; zone: Zone; alert: { id: string; severity: Zone; new: boolean } | null
  labs: LabValue[]; abnormal: LabValue[]; mood: { flag: 'ok' | 'watch' | 'urgent'; score: number | null; text: string } | null
}
export interface RecordHistory {
  pregnancies: { id: string; status: string; weeks: number | null; zone: Zone; edd: string | null; district_name: string; current: boolean }[]
  diagnoses: { code: string; label: string; first_at: string; district_name: string }[]
  labs: (LabValue & { at: string; series: { at: string; value: number }[] })[]
  mood: { at: string; flag: 'ok' | 'watch' | 'urgent'; score: number | null; text: string; stress: string | null }[]
  complaints: { at: string; items: string[]; note: string | null }[]
  causes: { at: string; items: string[]; note: string | null }[]
  visits: { at: string; week: number | null; sbp: number | null; dbp: number | null; hr: number | null; weight_kg: number | null; district_name: string }[]
  risk_history: string[]
}
export type LookupReason = 'emergency' | 'moved' | 'referral' | 'consultation'
export interface LookupResult extends RecordHistory {
  patient: { name: string; age: number | null; code: string; home_district: string; home_region: string; zone: Zone; weeks: number | null; open_alerts: number; synthetic: boolean }
  cross_district: boolean; reason: string; notice: string
}

// ---- specialist
export interface InboxItem {
  alert_id: string; severity: Zone; status: 'open' | 'acknowledged'; subject_type: 'pregnancy' | 'newborn'
  pregnancy_id: string | null; newborn_id: string | null
  patient: { name: string; age: number | null; weeks: number | null; district_name: string; code: string }
  summary: string; reasons: string[]; created_at: string; sla_due_at: string
  waiting_minutes: number; escalation_level: number; from: string
}
export interface Inbox { counts: { waiting: number; acknowledged: number; resolved_today: number }; items: InboxItem[] }
export interface PatientDetail {
  patient: {
    name: string; age: number | null; code: string; weeks: number | null; edd: string | null
    gravida: number | null; parity: number | null; district_name: string; mahalla: string | null
    nurse: string | null; doctor: string | null; history: string[]
  }
  zone: Zone; summary: string; reasons: Reason[]
  trend: {
    flags: { text: string; kind: string }[]
    bp: { at: string; week: number | null; sbp: number; dbp: number }[]
    hb: { at: string; week: number | null; value: number }[]
    weight: { at: string; week: number | null; value: number }[]
  }
  shock_index: number | null
  visits: {
    at: string; week: number | null; sbp: number | null; dbp: number | null; hr: number | null
    proteinuria: string | null; symptoms: string[]; zone: Zone; source: string; by: string
  }[]
  alerts: {
    id: string; severity: Zone; status: string; created_at: string; acknowledged_at: string | null
    resolved_at: string | null; resolution: string | null; escalation_level: number
  }[]
  action_card: ActionCard | null
  ml_shadow: { label: string } | null
}
export interface NewbornDetail {
  patient: {
    name: string; age_days: number; code: string; birth_weight_g: number | null
    gestational_age_weeks: number | null; apgar_5: number | null; district_name: string
  }
  zone: Zone; summary: string; reasons: Reason[]
  visits: Record<string, unknown>[]; alerts: PatientDetail['alerts']; action_card: ActionCard | null
}
export interface SearchResult { kind: 'patient' | 'district' | 'region'; id: string; label: string; sub: string }

// ---- phase 2: AI brief, privacy, models, district-level official statistics
export type BriefScope = 'national' | 'region' | 'district'
export interface AiBrief {
  text: string; source: 'claude-haiku' | 'local' | (string & {}); sent_fields: string[]; blocked: boolean; generated_at: string
}
/** Policy object; exact shape is not fixed — screens render it generically. */
export type AiPrivacy = Record<string, unknown>
export type ModelCard = Record<string, unknown>
export interface IndicatorRef { code: string; label: string; unit: string }
export interface OfficialDistrictRow {
  code: string; name: string; region_soato: string; value: number | null; national: number | null; status: Zone
  /** Births / denominator of the indicator when the backend sends one — used to weight region averages. */
  weight: number | null
}
export interface OfficialDistricts {
  indicator: IndicatorRef; year: number; available_indicators: IndicatorRef[]; years: number[]
  districts: OfficialDistrictRow[]; matched: number; unmatched: number; source: string; method: string
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const count = (v: unknown): number => (typeof v === 'number' ? v : Array.isArray(v) ? v.length : 0)
const ZONE_SET = new Set(['red', 'yellow', 'green', 'grey'])
/** Indicator names as people read them: no registry codes such as "(SIAT 2.01.01.0034)". */
const cleanLabel = (t: string) => t.replace(/\s*\((?:SIAT|kod|code)[^)]*\d[^)]*\)/gi, '').trim()
function indicatorRef(v: unknown): IndicatorRef | null {
  if (typeof v === 'string') return v ? { code: v, label: v, unit: '' } : null
  if (!isObj(v)) return null
  const code = String(v.code ?? v.id ?? v.label ?? '')
  return code ? { code, label: cleanLabel(String(v.label ?? v.name ?? code)), unit: String(v.unit ?? '') } : null
}
/** Tolerant parser: returns null when the payload is not usable, so the map keeps the region-level layer. */
function parseOfficialDistricts(raw: unknown): OfficialDistricts | null {
  if (!isObj(raw) || !Array.isArray(raw.districts)) return null
  const districts: OfficialDistrictRow[] = []
  for (const d of raw.districts) {
    if (!isObj(d) || d.code == null) continue
    const status = String(d.status ?? 'grey')
    districts.push({
      code: String(d.code), name: String(d.name ?? ''), region_soato: String(d.region_soato ?? ''),
      value: numOrNull(d.value), national: numOrNull(d.national), status: (ZONE_SET.has(status) ? status : 'grey') as Zone,
      weight: numOrNull(d.births) ?? numOrNull(d.denominator) ?? numOrNull(d.weight) ?? (isObj(d.detail) ? numOrNull(d.detail.births_pooled) : null),
    })
  }
  if (!districts.length) return null
  const available = (Array.isArray(raw.available_indicators) ? raw.available_indicators : [])
    .map(indicatorRef).filter((x): x is IndicatorRef => x !== null)
  const indicator = indicatorRef(raw.indicator) ?? available[0] ?? { code: '', label: 'Koʻrsatkich', unit: '' }
  const known = available.find((a) => a.code === indicator.code)
  if (known && indicator.label === indicator.code) { indicator.label = known.label; indicator.unit = indicator.unit || known.unit }
  const years = (Array.isArray(raw.years) ? raw.years : []).map(Number).filter(Number.isFinite).sort((a, b) => a - b)
  return {
    indicator, year: Number(raw.year) || years[years.length - 1] || 0, available_indicators: available, years,
    districts, matched: count(raw.matched), unmatched: count(raw.unmatched),
    source: String(raw.source ?? ''), method: String(raw.method ?? ''),
  }
}
/** Reads the optional `official` block of a district into indicator rows; [] when absent or unrecognised. */
export function officialIndicators(block: unknown): { year: number | null; source: string; items: OfficialIndicator[] } {
  const list = Array.isArray(block) ? block
    : isObj(block) ? (Array.isArray(block.indicators) ? block.indicators : Array.isArray(block.items) ? block.items : Array.isArray(block.latest) ? block.latest : [])
      : []
  const items: OfficialIndicator[] = []
  for (const it of list) {
    if (!isObj(it)) continue
    const label = cleanLabel(String(it.label ?? it.name ?? it.code ?? ''))
    if (!label) continue
    const series = (Array.isArray(it.series) ? it.series : [])
      .filter(isObj).map((p) => ({ year: Number(p.year), value: Number(p.value) }))
      .filter((p) => Number.isFinite(p.year) && Number.isFinite(p.value))
    items.push({ code: String(it.code ?? label), label, value: numOrNull(it.value), national: numOrNull(it.national), unit: String(it.unit ?? ''), series })
  }
  const meta = isObj(block) ? block : {}
  return { year: numOrNull(meta.year), source: typeof meta.source === 'string' ? meta.source : '', items }
}

// ---------------------------------------------------------------- session
const KEY = 'onalik.session'
export interface Session { token: string; user: User }

export function getSession(): Session | null {
  try { return JSON.parse(localStorage.getItem(KEY) || 'null') } catch { return null }
}
export function setSession(s: Session | null) {
  try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY) } catch { /* private mode */ }
}

const BASE = '/api/v1/live'

/** Error with the HTTP status (0 = no connection). `message` is always a calm, human Uzbek line. */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.name = 'ApiError'; this.status = status }
}
export const OFFLINE = 'Server bilan aloqa yoʻq'
const CALM: Record<number, string> = {
  400: 'Kiritilgan maʼlumotni tekshiring.',
  403: 'Bu amal sizning rolingiz uchun yopiq.',
  404: 'Maʼlumot topilmadi.',
  409: 'Bu amal allaqachon bajarilgan.',
  422: 'Kiritilgan maʼlumotni tekshiring.',
  429: 'Juda koʻp soʻrov. Birozdan soʻng urinib koʻring.',
}
// backend texts that are technical or English never reach the screen
const TECHNICAL = /\b(not found|forbidden|failed|required|invalid|error|denied|not allowed|permission|unauthori[sz]ed|internal|exception|validation|traceback|null|undefined)\b/i
function calmMessage(status: number, raw: unknown): string {
  const fallback = CALM[status] ?? (status >= 500 ? 'Serverda xatolik. Birozdan soʻng urinib koʻring.' : 'Amal bajarilmadi. Qayta urinib koʻring.')
  if (typeof raw !== 'string') return fallback
  const text = raw.trim()
  if (!text || text.length > 180 || TECHNICAL.test(text)) return fallback
  return text
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const s = getSession()
  let res: Response
  try {
    res = await fetch(BASE + path, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(s ? { Authorization: `Bearer ${s.token}` } : {}),
        ...(init?.headers || {}),
      },
    })
  } catch { throw new ApiError(0, OFFLINE) }
  if (res.status === 401) { setSession(null); window.location.href = '/'; throw new ApiError(401, 'Qayta kiring.') }
  if (!res.ok) {
    let raw: unknown = null
    try { const j = await res.json(); raw = j?.error?.message ?? j?.detail ?? null } catch { /* not JSON */ }
    throw new ApiError(res.status, calmMessage(res.status, raw))
  }
  try { return (await res.json()) as T } catch { throw new ApiError(res.status, 'Serverdan tushunarsiz javob keldi.') }
}
const post = <T,>(path: string, body?: unknown) => req<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

export const api = {
  async login(persona: Persona | 'admin' | 'facility_admin'): Promise<Session> {
    const r = await post<{ access_token: string; user: User }>('/login', { persona })
    const s = { token: r.access_token, user: r.user }
    setSession(s)
    return s
  },
  logout() { setSession(null) },

  map: () => req<MapData>('/map'),
  district: (code: string) => req<DistrictDetail>(`/districts/${encodeURIComponent(code)}`),
  regionOfficial: (soato: string) => req<RegionOfficial>(`/regions/${soato}/official`),
  events: (limit = 30) => req<LiveEvent[]>(`/events?limit=${limit}`),

  nurseToday: () => req<NurseToday>('/nurse/today'),
  voiceParse: (text: string) => post<VoiceParse>('/voice/parse', { text }),
  saveVisit: (body: { pregnancy_id: string; source: 'voice' | 'manual' } & Partial<VisitFields>) =>
    post<VisitResult>('/visits', body),

  recordCatalog: () => req<RecordCatalog>('/records/catalog'),
  saveDetails: (body: DetailsBody) => post<DetailsResult>('/records/details', body),
  record: (pregnancyId: string) => req<RecordHistory>(`/records/patients/${pregnancyId}`),
  lookup: (pinfl: string, reason: LookupReason) => post<LookupResult>('/records/lookup', { pinfl, reason }),

  inbox: () => req<Inbox>('/inbox'),
  patient: (id: string) => req<PatientDetail>(`/patients/${id}`),
  newborn: (id: string) => req<NewbornDetail>(`/newborns/${id}`),
  ack: (alertId: string) => post<{ ok: boolean; status: string; response_minutes: number }>(`/alerts/${alertId}/ack`),
  decide: (alertId: string, decision: 'refer' | 'recheck' | 'no_risk', note?: string) =>
    post<{ ok: boolean; status: string; message: string }>(`/alerts/${alertId}/decide`, { decision, note: note ?? null }),

  search: (q: string) => req<{ results: SearchResult[] }>(`/search?q=${encodeURIComponent(q)}`),
  rules: () => req<{ items: { rule_id: string; subject_type: string; severity: Zone; label: string; reference: string; approval_status: string }[] }>('/rules'),
  audit: (limit = 50) => req<{ items: { at: string; actor: string; action: string; resource: string }[]; chain_ok: boolean }>(`/audit?limit=${limit}`),
  scenario: (name: string) => post<{ ok: boolean }>('/demo/scenario', { name }),

  // phase 2 — these may be absent on older backends; callers hide the feature on any error
  aiBrief: (scope: BriefScope, id: string | null) => post<AiBrief>('/ai/brief', { scope, id }),
  aiPrivacy: () => req<AiPrivacy>('/ai/privacy'),
  models: async (): Promise<ModelCard[]> => {
    const r = await req<unknown>('/models')
    const items = Array.isArray(r) ? r : isObj(r) && Array.isArray(r.items) ? r.items : []
    return items.filter(isObj)
  },
  officialDistricts: async (indicator?: string | null, year?: number | null): Promise<OfficialDistricts> => {
    const q = new URLSearchParams()
    if (indicator) q.set('indicator', indicator)
    if (year) q.set('year', String(year))
    const qs = q.toString()
    const parsed = parseOfficialDistricts(await req<unknown>(`/official/districts${qs ? `?${qs}` : ''}`))
    if (!parsed) throw new Error('Tuman kesimida maʼlumot yoʻq')
    return parsed
  },
  streamTicket: () => post<{ ticket: string }>('/stream-ticket'),
}

/**
 * Subscribe to the live event stream. Returns an unsubscribe function.
 * Connects with a short-lived `?ticket=` (so the bearer token stays out of URLs and logs) and falls back
 * to `?token=` when tickets are unavailable. A ticket cannot be reused, so every reconnect asks for a fresh one.
 */
export function subscribeEvents(onEvent: (e: LiveEvent) => void): () => void {
  if (!getSession()) return () => {}
  let closed = false
  let es: EventSource | null = null
  let timer: ReturnType<typeof setTimeout> | null = null
  let wait = 1500

  const connect = async () => {
    timer = null
    const s = getSession()
    if (closed || !s) return
    let qs = `token=${encodeURIComponent(s.token)}`
    try {
      const r = await fetch(`${BASE}/stream-ticket`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${s.token}` }, body: '{}',
      })
      const j = r.ok ? await r.json() : null
      if (j && typeof j.ticket === 'string' && j.ticket) qs = `ticket=${encodeURIComponent(j.ticket)}`
    } catch { /* tickets unavailable — keep the token */ }
    if (closed) return
    const src = new EventSource(`${BASE}/stream?${qs}`)
    es = src
    src.onopen = () => { wait = 1500 }
    src.onmessage = (m) => { try { onEvent(JSON.parse(m.data)) } catch { /* ignore malformed */ } }
    src.onerror = () => {
      src.close()
      if (es === src) es = null
      if (closed || timer) return
      timer = setTimeout(connect, wait)
      wait = Math.min(wait * 2, 20000)
    }
  }
  void connect()
  return () => { closed = true; if (timer) clearTimeout(timer); es?.close() }
}

// ================================================================ accounts, public pages, admin
// Contract: docs/AUTH_ADMIN_CONTRACT.md. These endpoints live next to `/live`, under `/api/v1`.
const ROOT = '/api/v1'
const ROLES = new Set<Role>(['admin', 'leader', 'facility_admin', 'specialist', 'nurse', 'pending'])
export const NOT_READY = 'Bu boʻlim hali ulanmagan. Birozdan soʻng urinib koʻring.'

/** Role of a session user; demo sessions without `role` fall back to their persona. */
export function roleOf(u: User | null | undefined): Role | null {
  if (!u) return null
  return u.role && ROLES.has(u.role) ? u.role : u.persona
}
export function homeFor(role: Role | null): string {
  return role === 'admin' ? '/admin' : role === 'facility_admin' ? '/muassasa' : role === 'leader' ? '/rahbar' : role === 'specialist' ? '/mutaxassis'
    : role === 'nurse' ? '/hamshira' : role === 'pending' ? '/kutish' : '/kirish'
}

/** Like `req`, but for `/api/v1/*`: no hard redirect, so sign-in forms can show a wrong-code message. */
async function call<T>(path: string, opts: { method?: string; body?: unknown; auth?: boolean; raw?: boolean } = {}): Promise<T> {
  const s = opts.auth ? getSession() : null
  let res: Response
  try {
    res = await fetch(ROOT + path, {
      method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
      headers: {
        ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(s ? { Authorization: `Bearer ${s.token}` } : {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    })
  } catch { throw new ApiError(0, OFFLINE) }
  if (opts.auth && res.status === 401) { setSession(null); window.location.href = '/kirish'; throw new ApiError(401, 'Qayta kiring.') }
  if (!res.ok) {
    let raw: unknown = null
    try { const j = await res.json(); raw = j?.error?.message ?? (typeof j?.detail === 'string' ? j.detail : null) } catch { /* not JSON */ }
    if ((res.status === 404 || res.status === 405) && (raw === null || TECHNICAL.test(String(raw)))) throw new ApiError(res.status, NOT_READY)
    if (res.status === 401) throw new ApiError(401, calmMessage(401, raw) === 'Amal bajarilmadi. Qayta urinib koʻring.' ? 'Kod notoʻgʻri yoki muddati oʻtgan.' : calmMessage(401, raw))
    throw new ApiError(res.status, calmMessage(res.status, raw))
  }
  if (opts.raw) return res as unknown as T
  if (res.status === 204) return undefined as T
  try { return (await res.json()) as T } catch { throw new ApiError(res.status, 'Serverdan tushunarsiz javob keldi.') }
}

// ---- public
export interface PublicStat { key: string; label: string; value: number; unit: string }
export interface PublicOverview {
  stats: PublicStat[]
  live: { pregnant: number | null; waiting: number | null; median_response_min: number | null; answered_24h: number | null }
  source: string; demo: boolean
}
export interface PublicRegion { soato: string; name: string; value: number | null; national: number | null; status: Zone }
export interface PublicMap { indicator: IndicatorRef; year: number; regions: PublicRegion[]; source: string }

const TOTAL_LABEL: Record<string, string> = {
  regions: 'hudud', districts: 'tuman', births: 'tugʻilish', population: 'aholi',
}
function parseOverview(raw: unknown): PublicOverview {
  const o = isObj(raw) ? raw : {}
  const t = isObj(o.totals) ? o.totals : {}
  const year = numOrNull(t.births_year) ?? numOrNull(t.year)
  const stats: PublicStat[] = []
  for (const [key, v] of Object.entries(t)) {
    if (/_year$|^year$/.test(key)) continue
    if (typeof v === 'number' && Number.isFinite(v)) {
      const label = TOTAL_LABEL[key]
      if (label) stats.push({ key, label: key === 'births' && year ? `${label}, ${year}` : label, value: v, unit: '' })
    } else if (isObj(v) && numOrNull(v.value) !== null && (v.label || TOTAL_LABEL[key])) {
      const y = numOrNull(v.year)
      const label = cleanLabel(String(v.label ?? TOTAL_LABEL[key]))
      stats.push({ key, label: y ? `${label}, ${y}` : label, value: v.value as number, unit: String(v.unit ?? '') })
    }
  }
  const l = isObj(o.live) ? o.live : {}
  return {
    stats,
    live: {
      pregnant: numOrNull(l.pregnant), waiting: numOrNull(l.waiting),
      median_response_min: numOrNull(l.median_response_min), answered_24h: numOrNull(l.answered_24h),
    },
    source: typeof o.source === 'string' ? o.source : 'SIAT', demo: o.demo !== false,
  }
}
function parsePublicMap(raw: unknown): PublicMap {
  if (!isObj(raw) || !Array.isArray(raw.regions)) throw new ApiError(0, NOT_READY)
  const regions: PublicRegion[] = []
  for (const r of raw.regions) {
    if (!isObj(r) || r.soato == null) continue
    const status = String(r.status ?? 'grey')
    regions.push({
      soato: String(r.soato), name: String(r.name ?? ''), value: numOrNull(r.value), national: numOrNull(r.national),
      status: (ZONE_SET.has(status) ? status : 'grey') as Zone,
    })
  }
  if (!regions.length) throw new ApiError(0, NOT_READY)
  const available = (Array.isArray(raw.available_indicators) ? raw.available_indicators : []).map(indicatorRef).filter((x): x is IndicatorRef => x !== null)
  const indicator = indicatorRef(raw.indicator) ?? available[0] ?? { code: '', label: '', unit: '' }
  const known = available.find((a) => a.code === indicator.code)
  if (known && (indicator.label === indicator.code || !indicator.label)) { indicator.label = known.label; indicator.unit = indicator.unit || known.unit }
  return { indicator, year: Number(raw.year) || 0, regions, source: String(raw.source ?? '') }
}

// ---- account
export type AuthMode = 'register' | 'login'
export type AuthChannel = 'email' | 'phone'
export interface Challenge { challenge_id: string; expires_in: number; sent_to: string; dev_code: string | null }
export type SecondMethod = 'totp' | 'passkey'
export interface SecondFactor { second_factor_required: true; ticket: string; methods: SecondMethod[] }
export type AuthResult = { kind: 'session'; session: Session } | { kind: 'second'; ticket: string; methods: SecondMethod[] }
export interface PasskeyInfo { id: string; label: string; created_at: string }
export interface SecurityState {
  two_step: boolean
  methods: { email: string | null; phone: string | null; google: boolean; totp: boolean; passkeys: PasskeyInfo[] }
  sessions: { id: string; created_at: string; current: boolean }[]
}
/** WebAuthn payloads travel as base64url JSON; `lib`-free helpers live in screens/auth/webauthn.ts. */
export type Json = Record<string, unknown>

function authResult(raw: unknown): AuthResult {
  if (isObj(raw) && raw.second_factor_required === true && typeof raw.ticket === 'string') {
    const methods = (Array.isArray(raw.methods) ? raw.methods : []).filter((m): m is SecondMethod => m === 'totp' || m === 'passkey')
    return { kind: 'second', ticket: raw.ticket, methods: methods.length ? methods : ['totp'] }
  }
  if (isObj(raw) && typeof raw.access_token === 'string' && isObj(raw.user)) {
    const session: Session = { token: raw.access_token, user: raw.user as unknown as User }
    setSession(session)
    return { kind: 'session', session }
  }
  throw new ApiError(0, 'Serverdan tushunarsiz javob keldi.')
}
function securityState(raw: unknown): SecurityState {
  const o = isObj(raw) ? raw : {}
  const m = isObj(o.methods) ? o.methods : {}
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null)
  return {
    two_step: o.two_step === true,
    methods: {
      email: str(m.email), phone: str(m.phone), google: m.google === true, totp: m.totp === true,
      passkeys: (Array.isArray(m.passkeys) ? m.passkeys : []).filter(isObj).map((p) => ({ id: String(p.id), label: String(p.label ?? 'Passkey'), created_at: String(p.created_at ?? '') })),
    },
    sessions: (Array.isArray(o.sessions) ? o.sessions : []).filter(isObj).map((x) => ({ id: String(x.id), created_at: String(x.created_at ?? ''), current: x.current === true })),
  }
}

export const account = {
  overview: async () => parseOverview(await call<unknown>('/public/overview')),
  officialMap: async (indicator?: string | null, year?: number | null) => {
    const q = new URLSearchParams()
    if (indicator) q.set('indicator', indicator)
    if (year) q.set('year', String(year))
    const qs = q.toString()
    return parsePublicMap(await call<unknown>(`/public/official-map${qs ? `?${qs}` : ''}`))
  },

  start: (body: { mode: AuthMode; channel: AuthChannel; identifier: string; full_name?: string }) => call<Challenge>('/account/start', { body }),
  verify: async (challenge_id: string, code: string) => authResult(await call<unknown>('/account/verify', { body: { challenge_id, code } })),
  second: async (ticket: string, code: string) => authResult(await call<unknown>('/account/second', { body: { ticket, method: 'totp', code } })),

  googleConfig: async (): Promise<{ enabled: boolean; client_id: string | null }> => {
    const r = await call<unknown>('/account/google/config')
    const o = isObj(r) ? r : {}
    const id = typeof o.client_id === 'string' && o.client_id ? o.client_id : null
    return { enabled: o.enabled === true && id !== null, client_id: id }
  },
  googleVerify: async (credential: string) => authResult(await call<unknown>('/account/google/verify', { body: { credential } })),

  passkeyLoginOptions: (identifier: string | null, ticket: string | null) =>
    call<{ publicKey: Json; state: string }>('/account/passkey/login/options', { body: { identifier, ticket } }),
  passkeyLoginVerify: async (body: Json) => authResult(await call<unknown>('/account/passkey/login/verify', { body })),
  passkeyRegisterOptions: () => call<{ publicKey: Json }>('/account/passkey/register/options', { body: {}, auth: true }),
  passkeyRegisterVerify: (body: Json) => call<{ ok: boolean; passkeys: PasskeyInfo[] }>('/account/passkey/register/verify', { body, auth: true }),
  passkeyDelete: (id: string) => call<unknown>(`/account/passkey/${encodeURIComponent(id)}`, { method: 'DELETE', auth: true }),

  totpSetup: () => call<{ secret: string; otpauth_uri: string }>('/account/totp/setup', { body: {}, auth: true }),
  totpEnable: (code: string) => call<{ ok: boolean; recovery_codes: string[] }>('/account/totp/enable', { body: { code }, auth: true }),
  totpDisable: (code: string) => call<{ ok: boolean }>('/account/totp/disable', { body: { code }, auth: true }),

  security: async () => securityState(await call<unknown>('/account/security', { auth: true })),
  setTwoStep: async (two_step: boolean) => {
    const st = securityState(await call<unknown>('/account/security', { body: { two_step }, auth: true }))
    const s = getSession()
    if (s) setSession({ ...s, user: { ...s.user, two_step: st.two_step } })
    return st
  },
  /** Refreshes the stored user (e.g. after an admin gave a pending account its role). */
  me: async (): Promise<User> => {
    const u = await call<User>('/account/me', { auth: true })
    const s = getSession()
    if (s && isObj(u)) setSession({ ...s, user: u })
    return u
  },
  /** Ends the server session when there is one; the local session is always cleared. */
  logout: async () => {
    const s = getSession()
    setSession(null)
    if (!s) return
    try { await fetch(`${ROOT}/account/logout`, { method: 'POST', headers: { Authorization: `Bearer ${s.token}` } }) } catch { /* offline: local sign-out is enough */ }
  },
}

// ---- admin (aggregates only)
export interface AdminKpis {
  pregnant: number; newborns: number; red: number; yellow: number; green: number; grey: number
  open_alerts: number; overdue_alerts: number; median_response_min: number | null; p90_response_min: number | null
  answered_in_sla_pct: number | null; visits_7d: number; voice_share_pct: number | null; users: number; pending_users: number
}
export interface AdminRegionRow {
  soato: string; name: string; pregnant: number; red: number; yellow: number; open_alerts: number; overdue_alerts: number
  median_response_min: number | null; answered_in_sla_pct: number | null
}
export interface AdminCount { code: string; label: string; count: number }
export interface AdminOverview {
  generated_at: string; kpis: AdminKpis
  series: {
    alerts_by_day: { date: string; red: number; yellow: number; resolved: number }[]
    response_by_day: { date: string; median_min: number | null }[]
    visits_by_day: { date: string; count: number }[]
  }
  by_region: AdminRegionRow[]; top_reasons: AdminCount[]; resolutions: AdminCount[]
}
export type UserStatus = 'active' | 'pending' | 'blocked'
export interface AdminUser {
  id: string; name: string; email: string | null; phone: string | null; role: Role; status: UserStatus; two_step: boolean
  created_at: string | null; last_login_at: string | null; region_name: string | null; district_name: string | null
  facility_name: string | null; demo: boolean
}
export interface AuditData { items: { at: string; actor: string; action: string; resource: string }[]; chain_ok: boolean }

const n0 = (v: unknown) => numOrNull(v) ?? 0
const arr = (v: unknown) => (Array.isArray(v) ? v.filter(isObj) : [])
function adminOverview(raw: unknown): AdminOverview {
  if (!isObj(raw) || !isObj(raw.kpis)) throw new ApiError(0, NOT_READY)
  const k = raw.kpis
  const s = isObj(raw.series) ? raw.series : {}
  const counts = (v: unknown): AdminCount[] => arr(v).map((x) => ({ code: String(x.code ?? x.label ?? ''), label: String(x.label ?? x.code ?? ''), count: n0(x.count) }))
  return {
    generated_at: String(raw.generated_at ?? ''),
    kpis: {
      pregnant: n0(k.pregnant), newborns: n0(k.newborns), red: n0(k.red), yellow: n0(k.yellow), green: n0(k.green), grey: n0(k.grey),
      open_alerts: n0(k.open_alerts), overdue_alerts: n0(k.overdue_alerts),
      median_response_min: numOrNull(k.median_response_min), p90_response_min: numOrNull(k.p90_response_min),
      answered_in_sla_pct: numOrNull(k.answered_in_sla_pct), visits_7d: n0(k.visits_7d), voice_share_pct: numOrNull(k.voice_share_pct),
      users: n0(k.users), pending_users: n0(k.pending_users),
    },
    series: {
      alerts_by_day: arr(s.alerts_by_day).map((x) => ({ date: String(x.date), red: n0(x.red), yellow: n0(x.yellow), resolved: n0(x.resolved) })),
      response_by_day: arr(s.response_by_day).map((x) => ({ date: String(x.date), median_min: numOrNull(x.median_min) })),
      visits_by_day: arr(s.visits_by_day).map((x) => ({ date: String(x.date), count: n0(x.count) })),
    },
    by_region: arr(raw.by_region).map((x) => ({
      soato: String(x.soato), name: String(x.name ?? ''), pregnant: n0(x.pregnant), red: n0(x.red), yellow: n0(x.yellow),
      open_alerts: n0(x.open_alerts), overdue_alerts: n0(x.overdue_alerts),
      median_response_min: numOrNull(x.median_response_min), answered_in_sla_pct: numOrNull(x.answered_in_sla_pct),
    })),
    top_reasons: counts(raw.top_reasons), resolutions: counts(raw.resolutions),
  }
}
function adminUser(x: Record<string, unknown>): AdminUser {
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null)
  const role = String(x.role ?? 'pending') as Role
  const status = String(x.status ?? 'pending')
  return {
    id: String(x.id), name: String(x.name ?? ''), email: str(x.email), phone: str(x.phone),
    role: ROLES.has(role) ? role : 'pending', status: (status === 'active' || status === 'blocked' ? status : 'pending') as UserStatus,
    two_step: x.two_step === true, created_at: str(x.created_at), last_login_at: str(x.last_login_at),
    region_name: str(x.region_name), district_name: str(x.district_name),
    facility_name: str(x.facility_name), demo: x.demo === true,
  }
}

export interface FacilityAdminBrief { id: string; name: string; email: string | null; phone: string | null; demo: boolean }
export interface AdminFacility {
  id: string; name: string; type: string; region: string | null; district: string | null
  facility_admin: FacilityAdminBrief | null; staff_count: number
}
const sOrNull = (v: unknown) => (typeof v === 'string' && v ? v : null)
function adminFacility(x: Record<string, unknown>): AdminFacility {
  const a = isObj(x.facility_admin) ? x.facility_admin : null
  return {
    id: String(x.id), name: String(x.name ?? ''), type: String(x.type ?? ''), region: sOrNull(x.region), district: sOrNull(x.district),
    facility_admin: a ? { id: String(a.id), name: String(a.name ?? ''), email: sOrNull(a.email), phone: sOrNull(a.phone), demo: a.demo === true } : null,
    staff_count: n0(x.staff_count),
  }
}

export const admin = {
  overview: async (days: number) => adminOverview(await call<unknown>(`/admin/overview?days=${days}`, { auth: true })),
  users: async (status?: UserStatus | null): Promise<AdminUser[]> => {
    const r = await call<unknown>(`/admin/users${status ? `?status=${status}` : ''}`, { auth: true })
    return arr(isObj(r) ? r.items : r).map(adminUser)
  },
  /** The system admin gives non-clinical roles only; nurses and doctors are added by their facility's administrator. */
  updateUser: async (id: string, body: { role?: 'admin' | 'leader' | 'pending'; status: 'active' | 'blocked'; region_soato?: string | null }) => {
    const r = await call<unknown>(`/admin/users/${encodeURIComponent(id)}`, { body, auth: true })
    return adminUser(isObj(r) ? r : { id })
  },
  audit: async (limit = 100): Promise<AuditData> => {
    const r = await call<unknown>(`/admin/audit?limit=${limit}`, { auth: true })
    const o = isObj(r) ? r : {}
    return {
      items: arr(o.items).map((x) => ({ at: String(x.at ?? ''), actor: String(x.actor ?? ''), action: String(x.action ?? ''), resource: String(x.resource ?? '') })),
      chain_ok: o.chain_ok !== false,
    }
  },
  facilities: async (): Promise<AdminFacility[]> => {
    const r = await call<unknown>('/admin/facilities', { auth: true })
    return arr(isObj(r) ? r.items : r).map(adminFacility)
  },
  appointFacilityAdmin: async (facilityId: string, body: { user_id: string } | { identifier: string; full_name: string }) => {
    const r = await call<unknown>(`/admin/facilities/${encodeURIComponent(facilityId)}/admin`, { body, auth: true })
    return adminFacility(isObj(r) ? r : { id: facilityId })
  },
  removeFacilityAdmin: async (facilityId: string) => {
    const r = await call<unknown>(`/admin/facilities/${encodeURIComponent(facilityId)}/admin`, { method: 'DELETE', auth: true })
    return adminFacility(isObj(r) ? r : { id: facilityId })
  },
  /** CSV is fetched with the bearer header (never a token in the URL) and handed back as a Blob. */
  exportCsv: async (kind: 'regions' | 'districts' | 'alerts'): Promise<Blob> => {
    const res = await call<Response>(`/admin/export.csv?kind=${kind}`, { auth: true, raw: true })
    return res.blob()
  },
}

// ================================================================ facility administrator (/muassasa)
// One administrator per facility; only that person adds nurses and doctors, and only for their own facility.
export type StaffRole = 'nurse' | 'specialist'
export interface FacilityStaff {
  id: string; name: string; role: StaffRole; contact: string | null; status: 'active' | 'blocked'
  district_code: string | null; district_name: string | null; last_login_at: string | null; demo: boolean
}
export interface FacilityInfo {
  facility: { id: string; name: string; type: string; region_name: string | null; district_code: string | null; district_name: string | null }
  counts: { nurses: number; specialists: number; blocked: number }
  districts: { code: string; name: string }[]
}
function staffItem(x: Record<string, unknown>): FacilityStaff {
  return {
    id: String(x.id), name: String(x.name ?? ''), role: x.role === 'specialist' ? 'specialist' : 'nurse', contact: sOrNull(x.contact),
    status: x.status === 'blocked' ? 'blocked' : 'active', district_code: sOrNull(x.district_code), district_name: sOrNull(x.district_name),
    last_login_at: sOrNull(x.last_login_at), demo: x.demo === true,
  }
}
export const facility = {
  me: async (): Promise<FacilityInfo> => {
    const r = await call<unknown>('/facility/me', { auth: true })
    if (!isObj(r) || !isObj(r.facility)) throw new ApiError(0, NOT_READY)
    const f = r.facility, c = isObj(r.counts) ? r.counts : {}
    return {
      facility: { id: String(f.id), name: String(f.name ?? ''), type: String(f.type ?? ''), region_name: sOrNull(f.region_name),
        district_code: sOrNull(f.district_code), district_name: sOrNull(f.district_name) },
      counts: { nurses: n0(c.nurses), specialists: n0(c.specialists), blocked: n0(c.blocked) },
      districts: arr(r.districts).map((d) => ({ code: String(d.code), name: String(d.name ?? '') })),
    }
  },
  staff: async (): Promise<FacilityStaff[]> => {
    const r = await call<unknown>('/facility/staff', { auth: true })
    return arr(isObj(r) ? r.items : r).map(staffItem)
  },
  add: async (body: { full_name: string; identifier: string; role: StaffRole; district_code: string | null }) => {
    const r = await call<unknown>('/facility/staff', { body, auth: true })
    return staffItem(isObj(r) && isObj(r.item) ? r.item : {})
  },
  update: async (id: string, body: { status?: 'active' | 'blocked'; role?: StaffRole; district_code?: string | null }) => {
    const r = await call<unknown>(`/facility/staff/${encodeURIComponent(id)}`, { body, auth: true })
    return staffItem(isObj(r) ? r : { id })
  },
  remove: (id: string) => call<unknown>(`/facility/staff/${encodeURIComponent(id)}`, { method: 'DELETE', auth: true }),
}
