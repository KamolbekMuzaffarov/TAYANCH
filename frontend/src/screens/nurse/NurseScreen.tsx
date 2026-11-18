import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, getSession, type NurseTask, type VisitFields, type VisitResult } from '../../lib/api'
import { ErrorNote, ZONE_WORD, ZoneIcon, ZoneOrb, usePoll, useTick } from '../../ui/kit'
import { Icon, type IconName } from '../../ui/icons'
import { TopBar } from '../../ui/TopBar'
import { IS_MOCK, mockApi } from './mock'
import './nurse.css'

const client = IS_MOCK ? mockApi : { nurseToday: api.nurseToday, voiceParse: api.voiceParse, saveVisit: api.saveVisit }

type Step = 'today' | 'speak' | 'check' | 'result'
type Source = 'voice' | 'manual'

const EMPTY: VisitFields = {
  systolic_bp: null, diastolic_bp: null, heart_rate: null, temperature_c: null, weight_kg: null,
  hemoglobin_g_l: null, glucose_mmol_l: null, proteinuria: null,
  severe_headache: false, visual_disturbance: false, vaginal_bleeding: false,
  reduced_fetal_movements: false, convulsions: false,
}

type NumKey = 'heart_rate' | 'temperature_c' | 'weight_kg' | 'hemoglobin_g_l' | 'glucose_mmol_l'
const NUMS: { key: NumKey; label: string; unit: string; min: number; max: number; decimal: boolean }[] = [
  { key: 'heart_rate', label: 'Puls', unit: 'daqiqada', min: 30, max: 220, decimal: false },
  { key: 'temperature_c', label: 'Harorat', unit: '°C', min: 34, max: 43, decimal: true },
  { key: 'weight_kg', label: 'Vazn', unit: 'kg', min: 30, max: 200, decimal: true },
  { key: 'hemoglobin_g_l', label: 'Gemoglobin', unit: 'g/l', min: 30, max: 200, decimal: false },
  { key: 'glucose_mmol_l', label: 'Qand', unit: 'mmol/l', min: 1, max: 35, decimal: true },
]
const PROTEIN: { value: string; label: string }[] = [
  { value: 'neg', label: 'yoʻq' }, { value: 'trace', label: 'iz' },
  { value: '1+', label: '1+' }, { value: '2+', label: '2+' }, { value: '3+', label: '3+' },
]

type SymKey = 'severe_headache' | 'visual_disturbance' | 'vaginal_bleeding' | 'reduced_fetal_movements' | 'convulsions'
const SYMPTOMS: { key: SymKey; label: string; icon: IconName }[] = [
  { key: 'severe_headache', label: 'Bosh ogʻrigʻi', icon: 'head' },
  { key: 'visual_disturbance', label: 'Koʻz xiralashishi', icon: 'eye-off' },
  { key: 'vaginal_bleeding', label: 'Qon ketishi', icon: 'drop' },
  { key: 'reduced_fetal_movements', label: 'Bola kam qimirlayapti', icon: 'baby' },
  { key: 'convulsions', label: 'Tutqanoq', icon: 'activity' },
]
const NUM_ICON: Record<NumKey, IconName> = { heart_rate: 'heart-pulse', temperature_c: 'thermometer', weight_kg: 'scale', hemoglobin_g_l: 'droplet', glucose_mmol_l: 'chart' }

const EXAMPLES = [
  'Bosim 150 ga 95, puls 88, boshi ogʻriyapti',
  'Bosim 118 ga 76, puls 74, shikoyati yoʻq',
  'Bosim 132 ga 86, gemoglobin 94',
]

const fmt = (v: number) => String(v).replace('.', ',')
const mmss = (sec: number) => {
  const a = Math.abs(Math.round(sec))
  const p2 = (n: number) => String(n).padStart(2, '0')
  // hours appear only for long waits (a 4-hour answer window must not read as "239:58")
  return a >= 3600 ? `${Math.floor(a / 3600)}:${p2(Math.floor((a % 3600) / 60))}:${p2(a % 60)}` : `${p2(Math.floor(a / 60))}:${p2(a % 60)}`
}
const firstName = (n?: string | null) => (n || '').trim().split(/\s+/)[0] || ''

function bpProblem(s: number | null, d: number | null): string | null {
  if (s != null && (s < 60 || s > 260)) return 'Yuqori bosim 60 dan 260 gacha boʻladi. Qayta tekshiring.'
  if (d != null && (d < 30 || d > 160)) return 'Pastki bosim 30 dan 160 gacha boʻladi. Qayta tekshiring.'
  if (s != null && d != null && s <= d) return 'Yuqori bosim pastkisidan katta boʻlishi kerak.'
  return null
}

// ------------------------------------------------------------------ shell
const WIDE = '(min-width: 900px)'
function useWide() {
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches)
  useEffect(() => {
    const mq = window.matchMedia(WIDE)
    const on = () => setWide(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return wide
}

function Header({ title, sub, onBack }: { title: string; sub?: string; onBack: () => void }) {
  return (
    <header className="nz-head">
      <button className="nz-back" aria-label="Orqaga" onClick={onBack}><Icon name="chevron-left" size={24} stroke={2} /></button>
      <span className="nz-head-title"><b>{title}</b>{sub && <small>{sub}</small>}</span>
      <span className="nz-back ghost" aria-hidden />
    </header>
  )
}

const taskMeta = (t: NurseTask) => [
  t.newborn ? 'Chaqaloq' : t.weeks != null ? `${t.weeks} hafta` : null,
  t.mahalla ? (/mahalla/i.test(t.mahalla) ? t.mahalla : `${t.mahalla} mahallasi`) : null,
].filter(Boolean).join(' · ')

export default function NurseScreen() {
  const wide = useWide()
  const [step, setStep] = useState<Step>('today')
  const [dir, setDir] = useState<'fwd' | 'back'>('fwd')
  const [task, setTask] = useState<NurseTask | null>(null)
  const [fields, setFields] = useState<VisitFields>(EMPTY)
  const [source, setSource] = useState<Source>('manual')
  const [saved, setSaved] = useState<{ result: VisitResult; at: number } | null>(null)
  const today = usePoll(client.nurseToday, 20000)
  const panel = useRef<HTMLDivElement>(null)

  const go = (s: Step, d: 'fwd' | 'back' = 'fwd') => { setDir(d); setStep(s); window.scrollTo(0, 0); panel.current?.scrollTo(0, 0) }
  const home = () => { go('today', 'back'); today.refresh() }
  const pick = (t: NurseTask) => { setTask(t); go('speak') }

  let flow: ReactNode = null
  if (step === 'speak' && task) {
    flow = (
      <Speak task={task} onBack={() => go('today', 'back')}
        onParsed={(f) => { setFields(f); setSource('voice'); go('check') }}
        onManual={() => { setFields(EMPTY); setSource('manual'); go('check') }} />
    )
  } else if (step === 'check' && task) {
    flow = (
      <Check task={task} initial={fields} source={source} onBack={() => go('speak', 'back')}
        onSaved={(r) => { setSaved({ result: r, at: Date.now() }); go('result') }} />
    )
  } else if (step === 'result' && task && saved) {
    flow = <Result task={task} result={saved.result} at={saved.at} onBack={home} onNext={home} onRecheck={() => go('speak', 'back')} />
  }
  const list = (
    <Today data={today.data} loading={today.loading} error={today.error} onRetry={today.refresh}
      onPick={pick} selected={wide && flow ? task?.pregnancy_id ?? null : null} />
  )

  // desktop: the list stays on the left, the selected woman's flow lives in a glass panel on the right
  if (wide) {
    const tasks = today.data?.tasks ?? []
    return (
      <div className="nz wide">
        <TopBar />
        <div className="nz-desk">
          <aside className="nz-side" aria-label="Bugungi tashriflar">{list}</aside>
          <section className={`nz-panel glass${step === 'result' && flow ? ` tint ${saved?.result.zone}` : ''}`} aria-live="polite">
            <div className="nz-panel-scroll" ref={panel}>
              {flow ? <div key={`${step}:${task?.pregnancy_id}`} className={`nz-step ${dir}`}>{flow}</div> : (
                <div className="nz-blank fade-in">
                  <span className="nz-blank-ic"><Icon name={today.data && tasks.length === 0 ? 'check' : 'mic'} size={34} /></span>
                  <h2>{today.data && tasks.length === 0 ? 'Bugun tashrif yoʻq' : 'Roʻyxatdan ayolni tanlang'}</h2>
                  <p className="muted">
                    {today.data && tasks.length === 0 ? 'Yangi tashrif paydo boʻlsa, chapdagi roʻyxatda koʻrinadi.' : 'Tanlagach, oʻlchovlarni ovoz bilan ayting yoki qoʻlda kiriting.'}
                  </p>
                  {tasks.length > 0 && (
                    <button className="btn" onClick={() => pick(tasks[0])}>Birinchisidan boshlash<Icon name="arrow-right" size={18} /></button>
                  )}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    )
  }
  return (
    <div className="nz">
      <div key={step} className={`nz-step ${dir}`}>
        {flow ?? <><TopBar compact />{list}</>}
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ 1. TODAY
const DUE: Record<NurseTask['due'], string> = { overdue: 'Kechikkan', today: 'Bugun', soon: 'Yaqinda' }

function Today({ data, loading, error, onRetry, onPick, selected }: {
  data: Awaited<ReturnType<typeof client.nurseToday>> | null; loading: boolean; error: string | null
  onRetry: () => void; onPick: (t: NurseTask) => void; selected: string | null
}) {
  const name = firstName(data?.nurse.name ?? getSession()?.user.name)
  const tasks = data?.tasks ?? []
  return (
    <main className="nz-col nz-today">
      <div className="nz-greet">
        <h1>Assalomu alaykum{name ? `, ${name}` : ''}</h1>
        {data && tasks.length > 0 && (
          <p className="nz-sub">
            Bugun <b className="num">{tasks.length}</b> ta tashrif
            {data.done_today > 0 && <span className="muted"> · <span className="num">{data.done_today}</span> ta bajarildi</span>}
          </p>
        )}
      </div>

      {!data && loading && (
        <div className="nz-list" aria-busy="true" aria-label="Yuklanmoqda">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skel nz-skel" />)}
        </div>
      )}
      {!data && !loading && error && <ErrorNote message={error} onRetry={onRetry} />}

      {data && tasks.length === 0 && (
        <div className="nz-done fade-in">
          <ZoneOrb zone="green" size={84} />
          <h2>Bugungi tashriflar tugadi</h2>
          {data.done_today > 0 && <p className="muted">Bugun {data.done_today} ta tashrif bajarildi. Rahmat!</p>}
        </div>
      )}

      {tasks.length > 0 && (
        <ul className="nz-list stagger">
          {tasks.map((t) => (
            <li key={t.pregnancy_id}>
              <button className={`nz-task${selected === t.pregnancy_id ? ' sel' : ''}`} onClick={() => onPick(t)}
                aria-current={selected === t.pregnancy_id ? 'true' : undefined} aria-label={`${t.name}, ${ZONE_WORD[t.zone]}. ${t.reason}`}>
                <span className="nz-task-body">
                  <span className="nz-name"><ZoneIcon zone={t.zone} size={20} /><span>{t.name}</span></span>
                  <span className="nz-meta">{taskMeta(t)}</span>
                  <span className="nz-reason">{t.reason}</span>
                  <span className="nz-tags">
                    <span className={`badge${t.due === 'overdue' ? ' ink' : ''}`}>{t.due !== 'soon' && <Icon name="clock" size={12} stroke={2} />}{DUE[t.due]}</span>
                    {t.trend_flag && <span className="badge yellow"><Icon name="trending-up" size={12} stroke={2} />Erta ogohlantirish</span>}
                  </span>
                </span>
                <Icon name="chevron-right" size={20} className="nz-chev" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}

// ------------------------------------------------------------------ 2. SPEAK
/* eslint-disable @typescript-eslint/no-explicit-any */
function getRecognition(): (new () => any) | null {
  const w = window as any
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

function Speak({ task, onBack, onParsed, onManual }: {
  task: NurseTask; onBack: () => void; onParsed: (f: VisitFields) => void; onManual: () => void
}) {
  const Rec = useRef(getRecognition()).current
  const [state, setState] = useState<'idle' | 'listening' | 'parsing'>('idle')
  const [text, setText] = useState('')
  const [hint, setHint] = useState<string | null>(null)
  const [help, setHelp] = useState(!Rec || IS_MOCK)
  const [parseErr, setParseErr] = useState<string | null>(null)
  const rec = useRef<any>(null)
  const heard = useRef('')
  const failed = useRef(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      const r = rec.current
      if (r) { r.onresult = r.onerror = r.onend = null; try { r.abort() } catch { /* ignore */ } }
    }
  }, [])

  const submit = async (t: string) => {
    setText(t); setState('parsing'); setParseErr(null); setHint(null)
    try {
      const r = await client.voiceParse(t)
      if (alive.current) onParsed({ ...EMPTY, ...r.fields })
    } catch (e) {
      if (alive.current) { setParseErr((e as Error).message); setState('idle') }
    }
  }

  const fallback = (msg: string) => { setHelp(true); setHint(msg); setState('idle') }

  const toggle = () => {
    if (state === 'parsing') return
    if (state === 'listening') { try { rec.current?.stop() } catch { /* ignore */ } return }
    if (!Rec) { fallback('Bu telefonda ovoz ishlamaydi. Pastdan birini tanlang.'); return }
    try {
      const r = new Rec()
      r.lang = 'uz-UZ'; r.interimResults = true; r.continuous = true
      heard.current = ''; failed.current = false
      r.onresult = (ev: any) => {
        let s = ''
        for (let i = 0; i < ev.results.length; i++) s += ev.results[i][0].transcript + ' '
        heard.current = s.trim(); setText(heard.current)
      }
      r.onerror = (ev: any) => { if (ev?.error !== 'aborted' && ev?.error !== 'no-speech') failed.current = true }
      r.onend = () => {
        rec.current = null
        if (heard.current) submit(heard.current)
        else fallback(failed.current ? 'Ovozni yozib boʻlmadi. Pastdan birini tanlang.' : 'Eshitilmadi. Yana bir marta bosing.')
      }
      rec.current = r
      setText(''); setHint(null); setParseErr(null); setState('listening')
      r.start()
    } catch {
      rec.current = null
      fallback('Ovozni yozib boʻlmadi. Pastdan birini tanlang.')
    }
  }

  const label = state === 'listening' ? 'Eshityapman…' : state === 'parsing' ? 'Tushunyapman…' : 'Gapiring'
  return (
    <>
      <Header title={task.name} sub={taskMeta(task)} onBack={onBack} />
      <main className="nz-col nz-speak">
        <div className={`nz-mic-wrap ${state}`}>
          <span className="nz-ring" aria-hidden /><span className="nz-ring two" aria-hidden />
          <button className="nz-mic" onClick={toggle} disabled={state === 'parsing'}
            aria-label={state === 'listening' ? 'Toʻxtatish' : 'Gapirishni boshlash'} aria-pressed={state === 'listening'}>
            {state === 'listening'
              ? <Icon name="stop" size={52} style={{ fill: 'currentColor' }} />
              : <Icon name="mic" size={64} stroke={1.6} />}
          </button>
        </div>
        <h1 className="nz-speak-label" aria-live="polite">{label}</h1>

        <div className="nz-transcript" aria-live="polite">
          {text
            ? <p className="nz-heard">«{text}»</p>
            : state === 'listening'
              ? <p className="muted">Toʻxtatish uchun yana bosing</p>
              : <button className="nz-example" onClick={() => setHelp(true)}>Masalan: «Bosim 120 ga 80, puls 76, shikoyati yoʻq»</button>}
          {hint && state === 'idle' && <p className="nz-hint">{hint}</p>}
        </div>

        {parseErr && <ErrorNote message={parseErr} onRetry={() => submit(text)} />}

        {state !== 'listening' && (
          <div className="nz-speak-foot">
            {help && state === 'idle' && (
              <div className="nz-examples fade-in">
                <p className="small muted">Yoki birini tanlang</p>
                {EXAMPLES.map((p) => (
                  <button key={p} className="nz-phrase" onClick={() => submit(p)}><span className="grow">«{p}»</span><Icon name="arrow-right" size={16} /></button>
                ))}
              </div>
            )}
            <button className="btn ghost block" onClick={onManual} disabled={state === 'parsing'}><Icon name="edit" size={18} />Qoʻlda kiritish</button>
          </div>
        )}
      </main>
    </>
  )
}

// ------------------------------------------------------------------ 3. CHECK
type PadKey = 'sys' | 'dia' | NumKey

function Check({ task, initial, source, onBack, onSaved }: {
  task: NurseTask; initial: VisitFields; source: Source; onBack: () => void; onSaved: (r: VisitResult) => void
}) {
  const [f, setF] = useState<VisitFields>(initial)
  const [more, setMore] = useState(false)
  const [pad, setPad] = useState<PadKey | null>(null)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const missedBp = useRef(source === 'voice' && initial.systolic_bp == null).current

  const bpFilled = f.systolic_bp != null && f.diastolic_bp != null
  const bpMsg = bpProblem(f.systolic_bp, f.diastolic_bp)
  const ready = bpFilled && !bpMsg
  const hiddenCount = NUMS.filter((n) => f[n.key] == null).length + (f.proteinuria ? 0 : 1)

  const padDone = (raw: string): string | null => {
    if (!pad) return null
    if (raw === '' || raw === ',') {
      if (pad === 'sys' || pad === 'dia') setF({ ...f, systolic_bp: pad === 'sys' ? null : f.systolic_bp, diastolic_bp: null })
      else setF({ ...f, [pad]: null })
      setPad(null); return null
    }
    const v = parseFloat(raw.replace(',', '.'))
    if (Number.isNaN(v)) return 'Raqam kiriting.'
    if (pad === 'sys') {
      const m = bpProblem(v, null); if (m) return m
      setF({ ...f, systolic_bp: v }); setPad('dia'); return null
    }
    if (pad === 'dia') {
      const m = bpProblem(f.systolic_bp, v); if (m) return m
      setF({ ...f, diastolic_bp: v }); setPad(null); return null
    }
    const def = NUMS.find((n) => n.key === pad)!
    if (v < def.min || v > def.max) return `${def.label} ${fmt(def.min)} dan ${fmt(def.max)} gacha boʻladi. Qayta tekshiring.`
    setF({ ...f, [pad]: v }); setPad(null); return null
  }

  const save = async () => {
    if (!ready || saving) return
    setSaving(true); setErr(null)
    try { onSaved(await client.saveVisit({ pregnancy_id: task.pregnancy_id, source, ...f })) }
    catch (e) { setErr((e as Error).message); setSaving(false) }
  }

  const padDef = pad && pad !== 'sys' && pad !== 'dia' ? NUMS.find((n) => n.key === pad)! : null
  return (
    <>
      <Header title={task.name} sub={taskMeta(task)} onBack={onBack} />
      <main className="nz-col nz-check">
        <h1>{source === 'voice' && !missedBp ? 'Toʻgʻri tushundimmi?' : 'Oʻlchovlarni kiriting'}</h1>
        {missedBp && <p className="nz-hint">Bosimni eshita olmadim. Uni qoʻlda kiriting.</p>}

        <div className="nz-check-cols">
          <div className="nz-check-a">
            <button className={`nz-tile bp${bpFilled ? '' : ' empty'}`} onClick={() => setPad('sys')} aria-label="Bosimni kiritish">
              <span className="nz-tile-label"><Icon name="activity" size={16} />Bosim</span>
              {f.systolic_bp != null
                ? <span className="nz-bp num">{f.systolic_bp}<i>/</i>{f.diastolic_bp ?? '—'}<small>mm</small></span>
                : <span className="nz-tile-empty">Kiritish uchun bosing</span>}
            </button>
            {bpMsg && <p className="nz-hint" role="status">{bpMsg}</p>}

            <div className="nz-grid">
              {NUMS.filter((n) => more || f[n.key] != null).map((n) => (
                <button key={n.key} className={`nz-tile${f[n.key] == null ? ' empty' : ''}`} onClick={() => setPad(n.key)} aria-label={`${n.label}ni kiritish`}>
                  <span className="nz-tile-label"><Icon name={NUM_ICON[n.key]} size={16} />{n.label}</span>
                  {f[n.key] != null
                    ? <span className="nz-val num">{fmt(f[n.key] as number)}<small>{n.unit}</small></span>
                    : <span className="nz-tile-empty"><Icon name="plus" size={22} /></span>}
                </button>
              ))}
            </div>

            {(more || f.proteinuria) && (
              <div className="nz-block">
                <span className="nz-tile-label"><Icon name="droplet" size={16} />Siydikda oqsil</span>
                <div className="segmented big block" role="group" aria-label="Siydikda oqsil">
                  {PROTEIN.map((p) => (
                    <button key={p.value} aria-pressed={f.proteinuria === p.value}
                      onClick={() => setF({ ...f, proteinuria: f.proteinuria === p.value ? null : p.value })}>{p.label}</button>
                  ))}
                </div>
              </div>
            )}

            {!more && hiddenCount > 0 && (
              <button className="nz-more" onClick={() => setMore(true)}>
                <span className="nz-plus" aria-hidden><Icon name="plus" size={18} stroke={2} /></span>Yana qoʻshish
              </button>
            )}
          </div>

          <div className="nz-check-b">
            <h3 className="nz-section">Shikoyatlar</h3>
            <div className="nz-syms">
              {SYMPTOMS.map((s) => (
                <button key={s.key} className={`nz-sym${f[s.key] ? ' on' : ''}`} aria-pressed={f[s.key]} onClick={() => setF({ ...f, [s.key]: !f[s.key] })}>
                  <Icon name={s.icon} size={26} stroke={1.6} /><span className="grow">{s.label}</span>
                  <span className="nz-tick" aria-hidden>{f[s.key] && <Icon name="check" size={16} stroke={2.6} />}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {err && <ErrorNote message={err} onRetry={save} />}
      </main>

      <div className="nz-foot"><div className="nz-col">
        <button className="btn big block" disabled={!ready || saving} onClick={save}>{saving ? 'Saqlanmoqda…' : <><Icon name="check" size={22} stroke={2.2} />Toʻgʻri, saqlash</>}</button>
      </div></div>

      {pad && (
        <NumPad key={pad}
          title={pad === 'sys' ? 'Yuqori bosim' : pad === 'dia' ? 'Pastki bosim' : padDef!.label}
          unit={padDef ? padDef.unit : 'mm'}
          initial={pad === 'sys' ? f.systolic_bp : pad === 'dia' ? f.diastolic_bp : f[padDef!.key]}
          decimal={padDef ? padDef.decimal : false}
          doneLabel={pad === 'sys' ? 'Keyingi' : 'Tayyor'}
          onDone={padDone} onClose={() => setPad(null)} />
      )}
    </>
  )
}

function NumPad({ title, unit, initial, decimal, doneLabel, onDone, onClose }: {
  title: string; unit: string; initial: number | null; decimal: boolean; doneLabel: string
  onDone: (raw: string) => string | null; onClose: () => void
}) {
  const [v, setV] = useState(initial != null ? fmt(initial) : '')
  const [fresh, setFresh] = useState(true) // first key replaces the old value
  const [msg, setMsg] = useState<string | null>(null)

  const press = (k: string) => {
    setMsg(null); setFresh(false)
    const cur = fresh ? '' : v
    if (k === 'del') setV(fresh ? '' : v.slice(0, -1))
    else if (k === ',') { if (decimal && !cur.includes(',')) setV(cur === '' ? '0,' : cur + ',') }
    else if (cur.length < 5) setV(cur + k)
  }
  const done = () => { const e = onDone(v); if (e) setMsg(e) }

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (/^\d$/.test(e.key)) press(e.key)
      else if (e.key === ',' || e.key === '.') press(',')
      else if (e.key === 'Backspace') press('del')
      else if (e.key === 'Enter') { e.preventDefault(); done() }
      else if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  })

  return (
    <div className="nz-pad-back" onClick={onClose}>
      <div className="nz-pad" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="nz-pad-grab" aria-hidden />
        <p className="nz-pad-title">{title}</p>
        <p className="nz-pad-value num" aria-live="polite">{v || <span className="nz-pad-zero">0</span>}<small>{unit}</small></p>
        <p className="nz-pad-msg" role="status">{msg}</p>
        <div className="nz-keys">
          {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((k) => <button key={k} className="nz-key" onClick={() => press(k)}>{k}</button>)}
          <button className="nz-key" onClick={() => press(',')} disabled={!decimal} aria-label="Vergul">,</button>
          <button className="nz-key" onClick={() => press('0')}>0</button>
          <button className="nz-key" onClick={() => press('del')} aria-label="Oʻchirish">
            <Icon name="backspace" size={28} />
          </button>
        </div>
        <button className="btn big block" onClick={done}>{doneLabel}</button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ 4. RESULT
function Result({ task, result, at, onBack, onNext, onRecheck }: {
  task: NurseTask; result: VisitResult; at: number; onBack: () => void; onNext: () => void; onRecheck: () => void
}) {
  useTick(1000)
  useEffect(() => {
    if (result.zone === 'red') { try { navigator.vibrate?.(200) } catch { /* unsupported */ } }
  }, [result.zone])

  const elapsed = (Date.now() - at) / 1000
  const slaLeft = result.alert ? result.alert.sla_minutes * 60 - elapsed : 0
  const recheckLeft = result.recheck_minutes != null ? result.recheck_minutes * 60 - elapsed : 0
  const z = result.zone

  return (
    <div className={`nz-result ${z}`}>
      <div className="nz-band">
        <div className="nz-col">
          <Header title={task.name} sub={taskMeta(task)} onBack={onBack} />
          <div className="nz-verdict" role="status">
            <span className="nz-verdict-icon"><ZoneOrb zone={z} size={96} /></span>
            <span className="nz-verdict-word">{ZONE_WORD[z]}</span>
            <h1>{result.title}</h1>
            <p>{result.message}</p>
          </div>
        </div>
      </div>

      <main className="nz-col nz-result-body">
        {result.alert && (
          <section className="nz-card">
            <div className="nz-sent">
              <span className="nz-sent-ic"><Icon name="send" size={22} /></span>
              <div className="grow">
                <p className="nz-card-strong">Xabar yuborildi: {result.alert.sent_to}</p>
                <p className="muted">
                  {slaLeft >= 0 ? 'Javob kutilmoqda ' : 'Javob hali kelmadi +'}
                  <span className="num nz-clock">{mmss(slaLeft)}</span>
                </p>
              </div>
            </div>
          </section>
        )}

        {result.recheck_minutes != null && (
          <section className="nz-card nz-recheck">
            <p className="nz-card-strong">
              {recheckLeft > 0 ? `${result.recheck_minutes} daqiqadan keyin qayta oʻlchang` : 'Endi qayta oʻlchang'}
            </p>
            {recheckLeft > 0 && <p className="nz-timer num" aria-label="Qolgan vaqt">{mmss(recheckLeft)}</p>}
            <button className="btn soft block" onClick={onRecheck}><Icon name="refresh" size={18} />Qayta oʻlchash</button>
          </section>
        )}

        {result.action_card && result.action_card.steps.length > 0 && (
          <section className="nz-card">
            <h3>{result.action_card.title || 'Hozir nima qilish kerak'}</h3>
            <ol className="nz-steps">
              {result.action_card.steps.map((s, i) => (<li key={i}><span className="nz-step-n num" aria-hidden>{i + 1}</span><span>{s}</span></li>))}
            </ol>
          </section>
        )}

        {result.reasons.length > 0 && (
          <section className="nz-card">
            <h3>Sabablar</h3>
            <ul className="nz-lines">
              {result.reasons.map((r) => (
                <li key={r.code + r.text}><span>{r.text}</span>{r.reference && <span className="tiny muted">{r.reference}</span>}</li>
              ))}
            </ul>
          </section>
        )}

        {result.trend.length > 0 && (
          <section className="nz-card">
            <h3>Oʻzgarish</h3>
            <ul className="nz-lines">{result.trend.map((t) => <li key={t}><span>{t}</span></li>)}</ul>
          </section>
        )}

        {(z === 'green' || result.next_visit_days != null) && (
          <section className="nz-next">
            {z === 'green' && <p>Xavotirga oʻrin yoʻq. Ayolga shuni ayting.</p>}
            {result.next_visit_days != null && (
              <p className="nz-card-strong">Keyingi tashrif: {result.next_visit_days <= 1 ? 'ertaga' : `${result.next_visit_days} kundan keyin`}</p>
            )}
          </section>
        )}
      </main>

      <div className="nz-foot"><div className="nz-col">
        <button className="btn big block" onClick={onNext}>Keyingi ayol<Icon name="arrow-right" size={22} /></button>
      </div></div>
    </div>
  )
}
