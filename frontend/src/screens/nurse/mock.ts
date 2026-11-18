// Offline sample data for the nurse screen. Used ONLY when the URL has ?mock=1.
import { getSession, setSession, type NurseToday, type VisitFields, type VisitResult, type VoiceParse } from '../../lib/api'

export const IS_MOCK = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('mock') === '1'

// So /hamshira?mock=1 opens without a backend: the route guard only checks that a session exists.
if (IS_MOCK && !getSession()) {
  setSession({
    token: 'mock',
    user: {
      id: 'mock-nurse', name: 'Dilnoza Karimova', persona: 'nurse', role_label: 'Patronaj hamshira',
      district_code: '1726-GB012', district_name: 'Chilonzor', region_soato: '1726', region_name: 'Toshkent shahri',
    },
  })
}

const wait = <T,>(v: T, ms = 350) => new Promise<T>((r) => setTimeout(() => r(v), ms))

let tasks: NurseToday['tasks'] = [
  { pregnancy_id: 'p-1', name: 'Madina Tursunova', age: 27, weeks: 32, mahalla: 'Bogʻishamol', zone: 'red', reason: 'Bosim 3 tashrifda oshib boryapti', due: 'overdue', last_visit_days: 9, trend_flag: true, newborn: false },
  { pregnancy_id: 'p-2', name: 'Gulnora Abdullayeva', age: 34, weeks: 28, mahalla: 'Katta Chilonzor', zone: 'yellow', reason: 'Gemoglobin pasaygan (98)', due: 'today', last_visit_days: 14, trend_flag: false, newborn: false },
  { pregnancy_id: 'p-3', name: 'Shahnoza Ergasheva', age: 22, weeks: 36, mahalla: 'Novza', zone: 'yellow', reason: 'Vazn tez ortyapti', due: 'today', last_visit_days: 7, trend_flag: true, newborn: false },
  { pregnancy_id: 'p-4', name: 'Zilola Rahimova', age: 30, weeks: 19, mahalla: 'Bogʻishamol', zone: 'green', reason: 'Rejali tashrif', due: 'today', last_visit_days: 28, trend_flag: false, newborn: false },
  { pregnancy_id: 'p-5', name: 'Nodira Yoʻldosheva', age: 25, weeks: 24, mahalla: 'Lutfiy', zone: 'grey', reason: 'Birinchi tashrif — maʼlumot yoʻq', due: 'soon', last_visit_days: 0, trend_flag: false, newborn: false },
]
let done = 2
const borderlineSeen = new Set<string>()

const num = (t: string, re: RegExp): number | null => {
  const m = t.match(re)
  return m ? parseFloat(m[1].replace(',', '.')) : null
}

export const mockApi = {
  nurseToday: (): Promise<NurseToday> =>
    wait({ nurse: { name: 'Dilnoza Karimova', district_name: 'Chilonzor' }, date: new Date().toISOString().slice(0, 10), done_today: done, tasks: [...tasks] }),

  voiceParse: (text: string): Promise<VoiceParse> => {
    const t = text.toLowerCase().replace(/[ʻʼ'`’‘]/g, "'")
    const bp = t.match(/bosim\D{0,4}(\d{2,3})\s*(?:ga|dan|\/|,)?\s*(\d{2,3})/)
    const prot = t.match(/oqsil\s*(yo'q|iz|[1-3]\s*\+?)/)
    const fields: VisitFields = {
      systolic_bp: bp ? +bp[1] : null, diastolic_bp: bp ? +bp[2] : null,
      heart_rate: num(t, /puls\D{0,4}(\d{2,3})/),
      temperature_c: num(t, /harorat\D{0,4}(\d{2}(?:[.,]\d)?)/),
      weight_kg: num(t, /vazn\D{0,4}(\d{2,3}(?:[.,]\d)?)/),
      hemoglobin_g_l: num(t, /gemoglobin\D{0,4}(\d{2,3})/),
      glucose_mmol_l: num(t, /qand\D{0,4}(\d{1,2}(?:[.,]\d)?)/),
      proteinuria: prot ? (prot[1] === "yo'q" ? 'neg' : prot[1] === 'iz' ? 'trace' : `${prot[1][0]}+`) : null,
      severe_headache: /bosh\w*\s+og'ri/.test(t), visual_disturbance: /ko'z\w*\s+xira|xiralash/.test(t),
      vaginal_bleeding: /qon\s+ket/.test(t), reduced_fetal_movements: /kam\s+qimirla|qimirlamay/.test(t),
      convulsions: /tutqanoq|talvasa/.test(t),
    }
    const understood: string[] = []
    if (bp) understood.push(`Bosim ${bp[1]}/${bp[2]}`)
    if (fields.heart_rate != null) understood.push(`Puls ${fields.heart_rate}`)
    if (fields.hemoglobin_g_l != null) understood.push(`Gemoglobin ${fields.hemoglobin_g_l}`)
    if (fields.severe_headache) understood.push('Bosh ogʻrigʻi')
    return wait({ fields, understood, confidence: bp ? 'high' : 'low' }, 500)
  },

  saveVisit: (body: { pregnancy_id: string; source: 'voice' | 'manual' } & Partial<VisitFields>): Promise<VisitResult> => {
    const s = body.systolic_bp ?? 0, d = body.diastolic_bp ?? 0
    const symptom = !!(body.severe_headache || body.visual_disturbance)
    const severe = s >= 160 || d >= 110 || body.convulsions || body.vaginal_bleeding || (symptom && (s >= 140 || d >= 90))
    const high = s >= 140 || d >= 90
    const borderline = !high && (s >= 130 || d >= 85)
    const lowHb = body.hemoglobin_g_l != null && body.hemoglobin_g_l < 100
    let r: VisitResult
    if (severe) {
      r = {
        zone: 'red', title: 'Shifokor chaqirildi',
        message: 'Ayolning yonida qoling. Mutaxassis 30 daqiqa ichida javob beradi.',
        reasons: [{
          code: 'M_SEVERE', severity: 'red', reference: 'ACOG 2020',
          text: body.convulsions ? 'Tutqanoq' : body.vaginal_bleeding ? 'Qon ketishi' : symptom ? 'Yuqori bosim va bosh ogʻrigʻi' : `Juda yuqori bosim (${s}/${d})`,
        }],
        trend: body.pregnancy_id === 'p-1' ? [`Bosim 3 tashrifda 120 → 135 → ${s || 150}`] : [],
        action_card: { title: 'Hozir nima qilish kerak', steps: ['Ayolni chap yonboshiga yotqizing', 'Yonida qoling, yolgʻiz qoldirmang', '15 daqiqadan keyin bosimni qayta oʻlchang', 'Shifokor qoʻngʻirogʻini kuting'] },
        alert: { id: 'a-mock', sent_to: 'Dr. N. Yusupova (OvaBMU)', sla_minutes: 30 },
        recheck_minutes: null, next_visit_days: 1,
      }
    } else if (high || (borderline && !borderlineSeen.has(body.pregnancy_id))) {
      const again = borderlineSeen.has(body.pregnancy_id)
      borderlineSeen.add(body.pregnancy_id)
      r = again
        ? {
          zone: 'yellow', title: 'Shifokorga xabar berildi', message: 'Bosim qayta oʻlchashda ham yuqori. Mutaxassis javob beradi.',
          reasons: [{ code: 'M_BP_ELEVATED', severity: 'yellow', text: `Bosim ikki marta yuqori chiqdi (${s}/${d})`, reference: 'JSST 2016' }],
          trend: [], action_card: { title: 'Hozir nima qilish kerak', steps: ['Ayol tinch oʻtirsin', 'Shifokor javobini kuting'] },
          alert: { id: 'a-mock-2', sent_to: 'Dr. N. Yusupova (OvaBMU)', sla_minutes: 120 }, recheck_minutes: null, next_visit_days: 3,
        }
        : {
          zone: 'yellow', title: 'Qayta oʻlchash kerak', message: 'Bosim biroz yuqori. Ayol 15 daqiqa tinch oʻtirsin.',
          reasons: [{ code: 'M_BP_BORDERLINE', severity: 'yellow', text: `Bosim chegarada (${s}/${d})`, reference: 'JSST 2016' }],
          trend: [], action_card: null, alert: null, recheck_minutes: 15, next_visit_days: null,
        }
    } else if (lowHb) {
      r = {
        zone: 'yellow', title: 'Shifokorga xabar berildi', message: 'Gemoglobin past. Shifokor bugun koʻrib chiqadi.',
        reasons: [{ code: 'M_ANEMIA', severity: 'yellow', text: `Gemoglobin past (${body.hemoglobin_g_l} g/l)`, reference: 'JSST 2016' }],
        trend: ['Gemoglobin 112 → 104 → ' + body.hemoglobin_g_l],
        action_card: { title: 'Hozir nima qilish kerak', steps: ['Temir dorisini ichayotganini soʻrang', 'Ovqatlanish boʻyicha maslahat bering'] },
        alert: { id: 'a-mock-3', sent_to: 'Dr. N. Yusupova (OvaBMU)', sla_minutes: 240 }, recheck_minutes: null, next_visit_days: 7,
      }
    } else {
      r = { zone: 'green', title: 'Hammasi yaxshi', message: 'Koʻrsatkichlar meʼyorda.', reasons: [], trend: [], action_card: null, alert: null, recheck_minutes: null, next_visit_days: 14 }
    }
    if (r.recheck_minutes == null) {
      const before = tasks.length
      tasks = tasks.filter((t) => t.pregnancy_id !== body.pregnancy_id)
      if (tasks.length < before) done += 1
    }
    return wait(r, 600)
  },
}
