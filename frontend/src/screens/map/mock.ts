// Synthetic data in the exact contract shapes. Used ONLY when the URL has ?mock=1.
import type { DistrictCase, DistrictDetail, DistrictLive, LiveEvent, MapData, RegionOfficial, Zone } from '../../lib/api'
import { loadGeo } from './geo'

function rng(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
const hash = (s: string) => { let h = 2166136261; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619); return h >>> 0 }

interface State {
  districts: DistrictLive[]; byCode: Map<string, DistrictLive>; regionName: Map<string, string>
  answered24: number; events: LiveEvent[]; nextId: number; t0: number
}
let state: Promise<State> | null = null
const rand = rng(20261118)
const pick = <T,>(xs: T[]): T => xs[Math.floor(rand() * xs.length)]

const OFFICIAL: Record<string, Zone> = {
  '1703': 'green', '1706': 'green', '1708': 'yellow', '1710': 'yellow', '1712': 'green', '1714': 'green', '1718': 'yellow',
  '1722': 'red', '1724': 'yellow', '1726': 'green', '1727': 'green', '1730': 'green', '1733': 'yellow', '1735': 'red',
}
const SIGNALS = ['bosim 165/110', 'bosim 150/95, bosh ogʻrigʻi', 'qon ketishi', 'homila harakati kamaygan', 'gemoglobin 78 g/l']
const NEWBORN_SIGNALS = ['chaqaloq: harorat 38,4', 'chaqaloq: emmayapti', 'chaqaloq: nafas tezlashgan']
const DOCTORS = ['Dr. N. Yusupova', 'Dr. Sh. Rahimova', 'Dr. M. Abdullayeva', 'Dr. G. Toʻxtayeva', 'Dr. Z. Ergasheva']

function restatus(d: DistrictLive) {
  d.status = d.overdue_alerts > 0 ? 'red' : d.open_alerts > 0 ? 'yellow' : d.pregnant > 0 ? 'green' : 'grey'
}

function init(): Promise<State> {
  if (!state) {
    state = loadGeo().then((geo) => {
      const districts: DistrictLive[] = geo.districts.map((g) => {
        const r = rng(hash(g.code))
        const pregnant = r() < 0.04 ? 0 : 8 + Math.floor(r() * 52)
        const red = pregnant ? (r() < 0.3 ? 1 + Math.floor(r() * 2) : 0) : 0
        const yellow = Math.round(pregnant * (0.05 + r() * 0.1))
        const grey = pregnant ? Math.floor(r() * 3) : 0
        return {
          code: g.code, name: g.name, region_soato: g.soato, pregnant, red, yellow,
          green: Math.max(0, pregnant - red - yellow - grey), grey,
          open_alerts: 0, overdue_alerts: 0, newborn_alerts: 0, status: 'green' as Zone,
        }
      })
      const live = districts.filter((d) => d.pregnant > 0)
      const r = rng(77)
      for (let i = 0; i < 11; i++) {
        const d = live[Math.floor(r() * live.length)]
        d.open_alerts += 1 + (r() < 0.3 ? 1 : 0)
        d.red = Math.max(d.red, 1)
        if (i < 3) d.overdue_alerts = 1
        if (i % 4 === 1) d.newborn_alerts = 1
      }
      districts.forEach(restatus)
      const s: State = {
        districts, byCode: new Map(districts.map((d) => [d.code, d])),
        regionName: new Map(geo.regions.map((x) => [x.soato, x.name])),
        answered24: 137, events: [], nextId: 1000, t0: Date.now(),
      }
      for (let i = 30; i > 0; i--) {
        const d = live[Math.floor(r() * live.length)]
        const kind = r()
        push(s, d, kind < 0.6 ? 'visit' : kind < 0.85 ? 'ack' : 'resolved', 'green',
          kind < 0.6 ? 'tashrif kiritildi, koʻrsatkichlar meʼyorda' : kind < 0.85 ? `signalga javob berildi (${4 + Math.floor(r() * 12)} daqiqa)` : 'bemor perinatal markazga yoʻnaltirildi',
          Date.now() - i * 95_000)
      }
      return s
    })
    state.catch(() => { state = null })
  }
  return state
}

function push(s: State, d: DistrictLive, type: LiveEvent['type'], zone: Zone, text: string, at = Date.now()): LiveEvent {
  const e: LiveEvent = {
    id: s.nextId++, type, at: new Date(at).toISOString(), zone,
    district_code: d.code, district_name: d.name, region_soato: d.region_soato, text: `${d.name}: ${text}`,
  }
  s.events.unshift(e)
  if (s.events.length > 60) s.events.length = 60
  return e
}

/** One synthetic step: a visit, a new signal, an answer or an escalation. */
function step(s: State): LiveEvent {
  const live = s.districts.filter((d) => d.pregnant > 0)
  const open = s.districts.filter((d) => d.open_alerts > 0)
  const totalOpen = open.reduce((n, d) => n + d.open_alerts, 0)
  const x = rand()
  let e: LiveEvent
  if (open.length && (totalOpen > 16 || x < 0.3)) {
    const d = [...open].sort((a, b) => b.overdue_alerts - a.overdue_alerts)[rand() < 0.6 ? 0 : Math.floor(rand() * open.length)]
    d.open_alerts -= 1
    d.overdue_alerts = Math.min(d.overdue_alerts, d.open_alerts)
    d.newborn_alerts = Math.min(d.newborn_alerts, d.open_alerts)
    s.answered24 += 1
    e = push(s, d, 'ack', 'green', `signalga javob berildi (${3 + Math.floor(rand() * 14)} daqiqa)`)
  } else if (x < 0.55 && totalOpen < 16) {
    const d = pick(live)
    const newborn = rand() < 0.25
    d.open_alerts += 1
    if (newborn) d.newborn_alerts += 1; else d.red = Math.max(1, d.red)
    e = newborn
      ? push(s, d, 'newborn', 'red', `yangi signal — ${pick(NEWBORN_SIGNALS)}`)
      : push(s, d, 'alert', rand() < 0.6 ? 'red' : 'yellow', `yangi signal — ${pick(SIGNALS)}`)
  } else if (x < 0.68 && open.some((d) => d.open_alerts > d.overdue_alerts)) {
    const d = pick(open.filter((q) => q.open_alerts > q.overdue_alerts))
    d.overdue_alerts += 1
    e = push(s, d, 'escalation', 'red', 'signal muddati oʻtdi — yuqoriga uzatildi')
  } else {
    e = push(s, pick(live), 'visit', 'green', 'tashrif kiritildi, koʻrsatkichlar meʼyorda')
  }
  s.districts.forEach(restatus)
  return e
}

function buildMap(s: State): MapData {
  const sum = (f: (d: DistrictLive) => number, xs = s.districts) => xs.reduce((n, d) => n + f(d), 0)
  const soatos = [...s.regionName.keys()]
  return {
    generated_at: new Date().toISOString(),
    totals: {
      pregnant: sum((d) => d.pregnant), red: sum((d) => d.red), yellow: sum((d) => d.yellow), green: sum((d) => d.green), grey: sum((d) => d.grey),
      waiting_mothers: sum((d) => d.open_alerts - d.newborn_alerts), waiting_newborns: sum((d) => d.newborn_alerts),
      overdue: sum((d) => d.overdue_alerts), median_response_min: 11, answered_24h: s.answered24, newborns: 320,
    },
    districts: s.districts.map((d) => ({ ...d })),
    regions: soatos.map((soato) => {
      const xs = s.districts.filter((d) => d.region_soato === soato)
      return {
        soato, name: s.regionName.get(soato) ?? soato, pregnant: sum((d) => d.pregnant, xs), red: sum((d) => d.red, xs),
        yellow: sum((d) => d.yellow, xs), open_alerts: sum((d) => d.open_alerts, xs), overdue_alerts: sum((d) => d.overdue_alerts, xs),
        official_status: OFFICIAL[soato] ?? 'grey',
      }
    }),
  }
}

const wait = <T,>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), 180))

export const mockApi = {
  map: async (): Promise<MapData> => wait(buildMap(await init())),

  events: async (limit = 30): Promise<LiveEvent[]> => wait((await init()).events.slice(0, limit)),

  district: async (code: string): Promise<DistrictDetail> => {
    const s = await init()
    const d = s.byCode.get(code)
    if (!d) throw new Error('Tuman topilmadi')
    const r = rng(hash(code) + 5)
    const cases: DistrictCase[] = Array.from({ length: d.open_alerts }, (_, i) => {
      const overdue = i < d.overdue_alerts
      const newborn = i >= d.open_alerts - d.newborn_alerts
      const n = 100 + Math.floor(r() * 880)
      const due = overdue ? s.t0 - (3 + i * 4) * 60_000 : s.t0 + (11 + i * 7) * 60_000
      return {
        alert_id: `${code}-a${i}`, pregnancy_id: newborn ? null : `${code}-p${i}`, code: `${newborn ? 'N' : 'M'}-0${n}`,
        severity: (overdue || newborn || i === 0 ? 'red' : 'yellow') as Zone, status: 'open',
        summary: newborn ? `${2 + (n % 9)} kunlik. ${['Harorat 38,4, emmayapti.', 'Nafas tezlashgan.', 'Sariqlik kuchaygan.'][n % 3]}`
          : `${24 + (n % 14)} hafta. ${['Bosim 150/95, bosh ogʻrigʻi.', 'Bosim 165/110, koʻrish xiralashgan.', 'Gemoglobin 78 g/l, holsizlik.', 'Homila harakati kamaygan.'][n % 4]}`,
        waiting_minutes: Math.max(1, Math.round((Date.now() - (due - 30 * 60_000)) / 60_000)),
        sla_due_at: new Date(due).toISOString(), escalation_level: overdue ? 1 : 0, responsible: DOCTORS[n % DOCTORS.length],
      }
    })
    return wait({
      district: { code, name: d.name, region_name: s.regionName.get(d.region_soato) ?? '' },
      counts: { pregnant: d.pregnant, red: d.red, yellow: d.yellow, green: d.green, grey: d.grey, newborns: Math.round(d.pregnant / 9) },
      response: { open_alerts: d.open_alerts, overdue_alerts: d.overdue_alerts, median_response_min: d.pregnant ? 6 + (hash(code) % 11) : null, answered_7d: d.pregnant ? 5 + (hash(code) % 17) : 0 },
      cases, nurses: d.pregnant ? 2 + (hash(code) % 5) : 0,
    })
  },

  regionOfficial: async (soato: string): Promise<RegionOfficial> => {
    const s = await init()
    const r = rng(hash(soato))
    const status = OFFICIAL[soato] ?? 'grey'
    const k = status === 'red' ? 1.45 : status === 'yellow' ? 1.15 : 0.9
    const mk = (code: string, label: string, national: number, unit: string, slope: number) => {
      const end = +(national * k * (0.92 + r() * 0.16)).toFixed(1)
      const series = Array.from({ length: 10 }, (_, i) => ({ year: 2016 + i, value: +(end * (1 + slope * (9 - i) / 9) * (0.97 + r() * 0.06)).toFixed(1) }))
      series[9].value = end
      return { code, label, value: end, national, unit, series }
    }
    return wait({
      region: { soato, name: s.regionName.get(soato) ?? soato }, year: 2025, status,
      indicators: [
        mk('infant_mortality_rate', 'Goʻdaklar oʻlimi (1000 tirik tugʻilishga)', 9.5, '‰', 0.35),
        mk('maternal_mortality_ratio', 'Onalar oʻlimi (100 000 tirik tugʻilishga)', 14.8, '', 0.25),
        mk('perinatal_mortality_rate', 'Perinatal oʻlim (1000 tugʻilishga)', 8.9, '‰', 0.2),
        mk('birth_rate', 'Tugʻilish koeffitsiyenti (1000 aholiga)', 26.4, '‰', -0.12),
      ],
      source: 'SIAT / Milliy statistika qoʻmitasi',
    })
  },
}

/** Emits a synthetic event every few seconds. Returns an unsubscribe function. */
export function subscribeMockEvents(onEvent: (e: LiveEvent) => void): () => void {
  let alive = true
  const t = setInterval(() => { init().then((s) => { if (alive) onEvent(step(s)) }).catch(() => {}) }, 3600)
  return () => { alive = false; clearInterval(t) }
}
