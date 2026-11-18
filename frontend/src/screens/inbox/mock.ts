// Sample data in the exact contract shapes. Used ONLY when the URL has ?mock=1 (see InboxScreen.tsx).
// All patients are invented.
import type { Inbox, InboxItem, NewbornDetail, PatientDetail } from '../../lib/api'

const T0 = Date.now()
const min = (m: number) => new Date(T0 + m * 60_000).toISOString()
const day = (d: number) => new Date(T0 - d * 86_400_000).toISOString()
const wait = <T,>(v: T, ms = 280) => new Promise<T>((r) => setTimeout(() => r(structuredClone(v)), ms))

type AlertRow = PatientDetail['alerts'][number]
const alertRow = (id: string, severity: AlertRow['severity'], createdMin: number, status = 'open', level = 0): AlertRow => ({
  id, severity, status, created_at: min(createdMin), acknowledged_at: status === 'open' ? null : min(createdMin + 6),
  resolved_at: null, resolution: null, escalation_level: level,
})

let items: InboxItem[] = [
  {
    alert_id: 'a-101', severity: 'red', status: 'open', subject_type: 'pregnancy', pregnancy_id: 'p-0412', newborn_id: null,
    patient: { name: 'Madina Tursunova', age: 27, weeks: 32, district_name: 'Chilonzor', code: 'M-0412' },
    summary: '32 hafta. Bosim 118 → 135 → 150. Bugun bosh ogʻrigʻi.',
    reasons: ['Yuqori bosim va bosh ogʻrigʻi'], created_at: min(-7), sla_due_at: min(23),
    waiting_minutes: 7, escalation_level: 0, from: 'Hamshira D. Karimova',
  },
  {
    alert_id: 'a-102', severity: 'red', status: 'open', subject_type: 'newborn', pregnancy_id: null, newborn_id: 'n-0031',
    patient: { name: 'Nilufar Rahimova chaqalogʻi', age: null, weeks: null, district_name: 'Sergeli', code: 'N-0031' },
    summary: '3 kunlik. Vazni 2150 g, emishi sust, harorat 36,1.',
    reasons: ['Kam vaznli chaqaloqda emish sustligi'], created_at: min(-32), sla_due_at: min(-2.2),
    waiting_minutes: 32, escalation_level: 1, from: 'Hamshira S. Qodirova',
  },
  {
    alert_id: 'a-103', severity: 'yellow', status: 'open', subject_type: 'pregnancy', pregnancy_id: 'p-0388', newborn_id: null,
    patient: { name: 'Gulnora Abdullayeva', age: 34, weeks: 28, district_name: 'Yunusobod', code: 'M-0388' },
    summary: '28 hafta. Gemoglobin 112 → 104 → 94. Holsizlik.',
    reasons: ['Gemoglobin pasayib boryapti'], created_at: min(-41), sla_due_at: min(79),
    waiting_minutes: 41, escalation_level: 0, from: 'Hamshira M. Saidova',
  },
  {
    alert_id: 'a-104', severity: 'yellow', status: 'acknowledged', subject_type: 'pregnancy', pregnancy_id: 'p-0297', newborn_id: null,
    patient: { name: 'Zarina Yoʻldosheva', age: 22, weeks: 36, district_name: 'Chilonzor', code: 'M-0297' },
    summary: '36 hafta. Bir haftada 2,4 kg vazn qoʻshilgan, oyoqlarda shish.',
    reasons: ['Tez vazn ortishi va shish'], created_at: min(-95), sla_due_at: min(25),
    waiting_minutes: 95, escalation_level: 0, from: 'Hamshira D. Karimova',
  },
]

const lateItem: InboxItem = {
  alert_id: 'a-105', severity: 'red', status: 'open', subject_type: 'pregnancy', pregnancy_id: 'p-0455', newborn_id: null,
  patient: { name: 'Shahnoza Ergasheva', age: 30, weeks: 38, district_name: 'Yashnobod', code: 'M-0455' },
  summary: '38 hafta. Qon ketishi, puls 112, bosim 100/60.',
  reasons: ['Qon ketishi va yuqori shok indeksi'], created_at: '', sla_due_at: '',
  waiting_minutes: 0, escalation_level: 0, from: 'Hamshira N. Aliyeva',
}

const patients: Record<string, PatientDetail> = {
  'p-0412': {
    patient: {
      name: 'Madina Tursunova', age: 27, code: 'M-0412', weeks: 32, edd: '2027-01-12', gravida: 2, parity: 1,
      district_name: 'Chilonzor', mahalla: 'Bogʻishamol', nurse: 'D. Karimova', doctor: 'Dr. N. Yusupova',
      history: ['Oldingi kesarcha kesish (2023)', 'Onasida gipertoniya'],
    },
    zone: 'red', summary: '32 hafta. Bosim 118 → 135 → 150. Bugun bosh ogʻrigʻi.',
    reasons: [
      { code: 'M_PREECLAMPSIA_SEVERE_FEATURES', text: 'Yuqori bosim va bosh ogʻrigʻi', severity: 'red', reference: 'ACOG 2020' },
      { code: 'M_PROTEINURIA', text: 'Siydikda oqsil 1+', severity: 'yellow', reference: 'JSST 2016' },
    ],
    trend: {
      flags: [{ text: 'Bosim haftasiga ~5 mm ga oshyapti; shu surʼatda 2 haftada 160 dan oshadi', kind: 'bp' }],
      bp: [
        { at: day(56), week: 24, sbp: 118, dbp: 76 }, { at: day(28), week: 28, sbp: 126, dbp: 80 },
        { at: day(14), week: 30, sbp: 135, dbp: 86 }, { at: day(0), week: 32, sbp: 150, dbp: 95 },
      ],
      hb: [{ at: day(56), week: 24, value: 118 }, { at: day(28), week: 28, value: 114 }, { at: day(0), week: 32, value: 111 }],
      weight: [
        { at: day(56), week: 24, value: 64.5 }, { at: day(28), week: 28, value: 66.8 },
        { at: day(14), week: 30, value: 68.1 }, { at: day(0), week: 32, value: 70.4 },
      ],
    },
    shock_index: 0.59,
    visits: [
      { at: day(0), week: 32, sbp: 150, dbp: 95, hr: 88, proteinuria: '1+', symptoms: ['Bosh ogʻrigʻi'], zone: 'red', source: 'voice', by: 'Hamshira D. Karimova' },
      { at: day(14), week: 30, sbp: 135, dbp: 86, hr: 84, proteinuria: 'trace', symptoms: [], zone: 'yellow', source: 'voice', by: 'Hamshira D. Karimova' },
      { at: day(28), week: 28, sbp: 126, dbp: 80, hr: 80, proteinuria: 'neg', symptoms: [], zone: 'green', source: 'manual', by: 'Hamshira D. Karimova' },
      { at: day(56), week: 24, sbp: 118, dbp: 76, hr: 78, proteinuria: 'neg', symptoms: [], zone: 'green', source: 'manual', by: 'Hamshira D. Karimova' },
    ],
    alerts: [
      alertRow('a-101', 'red', -7),
      { ...alertRow('a-090', 'yellow', -14 * 1440, 'resolved'), resolved_at: min(-14 * 1440 + 20), resolution: 'recheck' },
    ],
    action_card: {
      title: 'Hozir nima qilish kerak',
      steps: ['Ayolni chap yonboshiga yotqizing', '15 daqiqadan soʻng bosimni qayta oʻlchang', 'Ayolning yonida qoling, mutaxassis javobini kuting'],
    },
    ml_shadow: { label: 'Yuqori xavf ehtimoli 0,71' },
  },
  'p-0388': {
    patient: {
      name: 'Gulnora Abdullayeva', age: 34, code: 'M-0388', weeks: 28, edd: '2027-02-09', gravida: 4, parity: 3,
      district_name: 'Yunusobod', mahalla: 'Bodomzor', nurse: 'M. Saidova', doctor: 'Dr. N. Yusupova',
      history: ['Tugʻruqlar orasidagi muddat 14 oy', 'Oldingi homiladorlikda kamqonlik'],
    },
    zone: 'yellow', summary: '28 hafta. Gemoglobin 112 → 104 → 94. Holsizlik.',
    reasons: [{ code: 'M_ANEMIA_MODERATE', text: 'Gemoglobin 100 dan past va pasayib boryapti', severity: 'yellow', reference: 'JSST 2016' }],
    trend: {
      flags: [{ text: 'Gemoglobin oyiga ~9 g/l ga pasayyapti', kind: 'hb' }],
      bp: [{ at: day(60), week: 20, sbp: 108, dbp: 68 }, { at: day(30), week: 24, sbp: 110, dbp: 70 }, { at: day(0), week: 28, sbp: 106, dbp: 66 }],
      hb: [{ at: day(60), week: 20, value: 112 }, { at: day(30), week: 24, value: 104 }, { at: day(0), week: 28, value: 94 }],
      weight: [{ at: day(60), week: 20, value: 58.2 }, { at: day(30), week: 24, value: 59.9 }, { at: day(0), week: 28, value: 61.3 }],
    },
    shock_index: 0.83,
    visits: [
      { at: day(0), week: 28, sbp: 106, dbp: 66, hr: 88, proteinuria: 'neg', symptoms: ['Holsizlik', 'Bosh aylanishi'], zone: 'yellow', source: 'voice', by: 'Hamshira M. Saidova' },
      { at: day(30), week: 24, sbp: 110, dbp: 70, hr: 82, proteinuria: 'neg', symptoms: [], zone: 'green', source: 'manual', by: 'Hamshira M. Saidova' },
      { at: day(60), week: 20, sbp: 108, dbp: 68, hr: 80, proteinuria: 'neg', symptoms: [], zone: 'green', source: 'manual', by: 'Hamshira M. Saidova' },
    ],
    alerts: [alertRow('a-103', 'yellow', -41)],
    action_card: { title: 'Hozir nima qilish kerak', steps: ['Temir preparati qabul qilinayotganini soʻrang', 'Ovqatlanish boʻyicha maslahat bering', '24 soat ichida shifokor koʻrigiga yozing'] },
    ml_shadow: { label: 'Oʻrtacha xavf ehtimoli 0,38' },
  },
  'p-0297': {
    patient: {
      name: 'Zarina Yoʻldosheva', age: 22, code: 'M-0297', weeks: 36, edd: '2026-12-16', gravida: 1, parity: 0,
      district_name: 'Chilonzor', mahalla: 'Katta Qozirobod', nurse: 'D. Karimova', doctor: 'Dr. N. Yusupova', history: [],
    },
    zone: 'yellow', summary: '36 hafta. Bir haftada 2,4 kg vazn qoʻshilgan, oyoqlarda shish.',
    reasons: [{ code: 'M_RAPID_WEIGHT_GAIN', text: 'Bir haftada 2 kg dan ortiq vazn ortishi', severity: 'yellow', reference: 'NICE NG133' }],
    trend: {
      flags: [],
      bp: [{ at: day(21), week: 33, sbp: 116, dbp: 74 }, { at: day(7), week: 35, sbp: 124, dbp: 80 }, { at: day(0), week: 36, sbp: 132, dbp: 84 }],
      hb: [{ at: day(21), week: 33, value: 121 }, { at: day(0), week: 36, value: 119 }],
      weight: [{ at: day(21), week: 33, value: 67.0 }, { at: day(7), week: 35, value: 68.2 }, { at: day(0), week: 36, value: 70.6 }],
    },
    shock_index: 0.64,
    visits: [
      { at: day(0), week: 36, sbp: 132, dbp: 84, hr: 84, proteinuria: 'trace', symptoms: ['Oyoqlarda shish'], zone: 'yellow', source: 'voice', by: 'Hamshira D. Karimova' },
      { at: day(7), week: 35, sbp: 124, dbp: 80, hr: 80, proteinuria: 'neg', symptoms: [], zone: 'green', source: 'voice', by: 'Hamshira D. Karimova' },
    ],
    alerts: [alertRow('a-104', 'yellow', -95, 'acknowledged')],
    action_card: { title: 'Hozir nima qilish kerak', steps: ['Bosimni ikki qoʻlda oʻlchang', 'Siydikda oqsilni tekshiring'] },
    ml_shadow: { label: 'Oʻrtacha xavf ehtimoli 0,44' },
  },
  'p-0455': {
    patient: {
      name: 'Shahnoza Ergasheva', age: 30, code: 'M-0455', weeks: 38, edd: '2026-12-02', gravida: 3, parity: 2,
      district_name: 'Yashnobod', mahalla: 'Tuzel', nurse: 'N. Aliyeva', doctor: 'Dr. N. Yusupova', history: ['Yoʻldoshning past joylashuvi (UTT, 30 hafta)'],
    },
    zone: 'red', summary: '38 hafta. Qon ketishi, puls 112, bosim 100/60.',
    reasons: [
      { code: 'M_ANTEPARTUM_HAEMORRHAGE', text: 'Homiladorlikning kech muddatida qon ketishi', severity: 'red', reference: 'RCOG GTG 63' },
      { code: 'M_SHOCK_INDEX', text: 'Shok indeksi 0,9 dan yuqori', severity: 'red', reference: 'JSST 2017' },
    ],
    trend: {
      flags: [{ text: 'Puls oshib, bosim pasaymoqda — qon yoʻqotish belgisi', kind: 'shock' }],
      bp: [{ at: day(14), week: 36, sbp: 116, dbp: 74 }, { at: day(7), week: 37, sbp: 114, dbp: 72 }, { at: day(0), week: 38, sbp: 100, dbp: 60 }],
      hb: [{ at: day(14), week: 36, value: 109 }, { at: day(0), week: 38, value: 98 }],
      weight: [{ at: day(14), week: 36, value: 72.1 }, { at: day(0), week: 38, value: 72.8 }],
    },
    shock_index: 1.12,
    visits: [
      { at: day(0), week: 38, sbp: 100, dbp: 60, hr: 112, proteinuria: null, symptoms: ['Qon ketishi'], zone: 'red', source: 'voice', by: 'Hamshira N. Aliyeva' },
      { at: day(7), week: 37, sbp: 114, dbp: 72, hr: 84, proteinuria: 'neg', symptoms: [], zone: 'green', source: 'manual', by: 'Hamshira N. Aliyeva' },
    ],
    alerts: [],
    action_card: { title: 'Hozir nima qilish kerak', steps: ['103 ga qoʻngʻiroq qiling', 'Ayolni yotqizib, oyoqlarini koʻtaring', 'Ayolni yolgʻiz qoldirmang'] },
    ml_shadow: { label: 'Yuqori xavf ehtimoli 0,88' },
  },
}

const newborns: Record<string, NewbornDetail> = {
  'n-0031': {
    patient: { name: 'Nilufar Rahimova chaqalogʻi', age_days: 3, code: 'N-0031', birth_weight_g: 2150, gestational_age_weeks: 35, apgar_5: 7, district_name: 'Sergeli' },
    zone: 'red', summary: '3 kunlik. Vazni 2150 g, emishi sust, harorat 36,1.',
    reasons: [
      { code: 'N_POOR_FEEDING', text: 'Emishi sust', severity: 'red', reference: 'JSST IMCI 2019' },
      { code: 'N_HYPOTHERMIA', text: 'Harorat 36,5 dan past', severity: 'yellow', reference: 'JSST IMCI 2019' },
      { code: 'N_LOW_BIRTH_WEIGHT', text: 'Tugʻilgandagi vazn 2500 g dan kam', severity: 'yellow', reference: 'JSST 2022' },
    ],
    visits: [{ at: day(0), weight_g: 2040, temperature_c: 36.1, feeding: 'sust', by: 'Hamshira S. Qodirova' }],
    alerts: [alertRow('a-102', 'red', -32, 'open', 1)],
    action_card: { title: 'Hozir nima qilish kerak', steps: ['Chaqaloqni onaning koʻkragiga teri-teriga qoʻying', 'Isitib, 30 daqiqadan soʻng haroratni qayta oʻlchang', 'Emizishga yordam bering'] },
  },
}

let resolvedToday = 9
let lateScheduled = false

function findAlert(alertId: string): AlertRow | undefined {
  for (const d of [...Object.values(patients), ...Object.values(newborns)]) {
    const a = d.alerts.find((x) => x.id === alertId)
    if (a) return a
  }
}

const MESSAGES = {
  refer: 'Bemor perinatal markazga yoʻnaltirildi',
  recheck: 'Hamshiraga 24 soat ichida qayta koʻrik topshirildi',
  no_risk: 'Signal yopildi: xavf aniqlanmadi',
} as const

export const mockApi = {
  inbox(): Promise<Inbox> {
    if (!lateScheduled) {
      lateScheduled = true
      setTimeout(() => {
        const now = Date.now()
        const created = new Date(now).toISOString()
        items = [{ ...lateItem, created_at: created, sla_due_at: new Date(now + 30 * 60_000).toISOString() }, ...items]
        patients['p-0455'].alerts.unshift({ id: 'a-105', severity: 'red', status: 'open', created_at: created, acknowledged_at: null, resolved_at: null, resolution: null, escalation_level: 0 })
      }, 20_000)
    }
    const live = items.map((i) => ({ ...i, waiting_minutes: Math.max(0, Math.round((Date.now() - new Date(i.created_at).getTime()) / 60_000)) }))
    return wait({
      counts: { waiting: live.filter((i) => i.status === 'open').length, acknowledged: live.filter((i) => i.status === 'acknowledged').length, resolved_today: resolvedToday },
      items: live,
    })
  },
  patient(id: string): Promise<PatientDetail> {
    const d = patients[id]
    return d ? wait(d, 380) : Promise.reject(new Error('Bemor topilmadi'))
  },
  newborn(id: string): Promise<NewbornDetail> {
    const d = newborns[id]
    return d ? wait(d, 380) : Promise.reject(new Error('Chaqaloq topilmadi'))
  },
  ack(alertId: string): Promise<{ ok: boolean; status: string; response_minutes: number }> {
    const it = items.find((i) => i.alert_id === alertId)
    if (!it) return Promise.reject(new Error('Signal topilmadi'))
    it.status = 'acknowledged'
    const a = findAlert(alertId)
    if (a) { a.status = 'acknowledged'; a.acknowledged_at = new Date().toISOString() }
    return wait({ ok: true, status: 'acknowledged', response_minutes: Math.max(1, Math.round((Date.now() - new Date(it.created_at).getTime()) / 60_000)) })
  },
  decide(alertId: string, decision: 'refer' | 'recheck' | 'no_risk'): Promise<{ ok: boolean; status: string; message: string }> {
    items = items.filter((i) => i.alert_id !== alertId)
    resolvedToday += 1
    const a = findAlert(alertId)
    if (a) { a.status = 'resolved'; a.resolved_at = new Date().toISOString(); a.resolution = decision }
    return wait({ ok: true, status: 'resolved', message: MESSAGES[decision] })
  },
}
