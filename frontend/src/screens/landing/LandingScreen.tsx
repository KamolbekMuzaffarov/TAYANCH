// Public landing page. Unregistered visitors see only official statistics here — never the live map.
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { account, getSession, homeFor, roleOf, type PublicMap, type PublicOverview, type Zone } from '../../lib/api'
import { BrandMark, ThemeToggle, ZONE_WORD } from '../../ui/kit'
import { Icon, type IconName } from '../../ui/icons'
import { loadGeo, H, W, type Geo } from '../map/geo'
import { fmtNum } from '../auth/parts'
import './base.css'
import './landing.css'

const NAV: { id: string; label: string }[] = [
  { id: 'muammo', label: 'Muammo' },
  { id: 'yechim', label: 'Yechim' },
  { id: 'qanday', label: 'Qanday ishlaydi' },
  { id: 'xavfsizlik', label: 'Xavfsizlik' },
]

function jump(id: string) {
  const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  document.getElementById(id)?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
}

/**
 * Scroll-reveal as progressive enhancement: everything is visible by default. Elements that start below the fold
 * play a short entrance when the IntersectionObserver reports them (an observer callback means the page is really
 * being rendered, so the animation will run). Above-the-fold content is never touched.
 */
function useReveal<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  useEffect(() => {
    const root = ref.current
    if (!root || typeof IntersectionObserver === 'undefined') return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target) }
    }, { threshold: 0 })
    const vh = window.innerHeight
    root.querySelectorAll('.ob-reveal').forEach((el) => { if (el.getBoundingClientRect().top >= vh) io.observe(el) })
    return () => io.disconnect()
  }, [])
  return ref
}

const R = ({ children, d = 0, className = '', as: Tag = 'div' }: { children: ReactNode; d?: number; className?: string; as?: 'div' | 'li' | 'article' }) => (
  <Tag className={`ob-reveal ${className}`} style={{ ['--d' as string]: `${d}ms` }}>{children}</Tag>
)

// ---------------------------------------------------------------- hero preview
const O_FILL: Record<Zone, string> = { red: 'var(--map-o-red)', yellow: 'var(--map-o-yellow)', green: 'var(--map-o-green)', grey: 'var(--map-grey)' }

function Preview() {
  const [geo, setGeo] = useState<Geo | null>(null)
  const [map, setMap] = useState<PublicMap | null>(null)
  const [ov, setOv] = useState<PublicOverview | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    loadGeo().then((g) => { if (alive) setGeo(g) }).catch(() => {})
    account.officialMap().then((m) => { if (alive) setMap(m) }).catch(() => {})
    account.overview().then((o) => { if (alive) setOv(o) }).catch(() => {})
    return () => { alive = false }
  }, [])

  const bySoato = new Map((map?.regions ?? []).map((r) => [r.soato, r]))
  const hot = hover ? bySoato.get(hover) : null
  const hotGeo = hover ? geo?.regionBySoato.get(hover) : null
  const unit = map?.indicator.unit ?? ''
  const stats: { key: string; value: string; label: string }[] = []
  if (ov) {
    for (const s of ov.stats.slice(0, 3)) stats.push({ key: s.key, value: `${fmtNum(s.value, Number.isInteger(s.value) ? 0 : 1)}${s.unit && s.unit.length <= 2 ? s.unit : ''}`, label: s.label })
    if (ov.live.median_response_min !== null) stats.push({ key: 'resp', value: `${fmtNum(ov.live.median_response_min)} daq`, label: 'signalga oʻrtacha javob (demo)' })
    else if (ov.live.pregnant !== null) stats.push({ key: 'preg', value: fmtNum(ov.live.pregnant), label: 'kuzatuvdagi homilador (demo)' })
  }

  return (
    <div className="ln-preview ob-glass sheen">
      <div className="ln-preview-head">
        <span className="ob-chip"><Icon name="globe" size={16} />Rasmiy statistika (SIAT)</span>
        {map && map.indicator.label && (
          <span className="small muted ln-preview-ind">{map.indicator.label}{map.year ? ` · ${map.year}` : ''}</span>
        )}
      </div>
      <div className="ln-preview-body">
        <div className="ln-map">
          {geo ? (
            <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Oʻzbekiston hududlari boʻyicha rasmiy statistika xaritasi" onMouseLeave={() => setHover(null)}>
              {geo.regions.map((r) => {
                const row = bySoato.get(r.soato)
                return (
                  <path key={r.soato} d={r.d} className={`ln-reg${hover === r.soato ? ' hot' : ''}`}
                    fill={row ? O_FILL[row.status] : 'var(--map-grey)'} onMouseEnter={() => setHover(r.soato)} onClick={() => setHover(r.soato)} />
                )
              })}
            </svg>
          ) : <div className="skel ln-map-skel" />}
          <div className="ln-map-foot">
            {hotGeo ? (
              <span className="small"><b>{hotGeo.short}</b>{hot && hot.value !== null ? <span className="muted num"> · {fmtNum(hot.value, 1)}{unit ? ` ${unit}` : ''} · {ZONE_WORD[hot.status]}</span> : null}</span>
            ) : map ? (
              <span className="ln-legend small muted">
                <i style={{ background: 'var(--map-o-green)' }} />Yaxshi
                <i style={{ background: 'var(--map-o-yellow)' }} />Eʼtibor
                <i style={{ background: 'var(--map-o-red)' }} />Xavfli
                <span className="ln-legend-note">· respublika oʻrtachasiga nisbatan</span>
              </span>
            ) : (
              <span className="small muted">14 hudud · 199 tuman</span>
            )}
          </div>
        </div>
        {stats.length > 0 && (
          <ul className="ln-stats">
            {stats.map((s) => (
              <li key={s.key} className="ln-stat">
                <span className="ln-stat-v num">{s.value}</span>
                <span className="small muted">{s.label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- vignettes (stylised UI, plain HTML/CSS)
function NurseVignette() {
  return (
    <div className="ln-vig" aria-hidden>
      <div className="ln-v-orb"><Icon name="mic" size={26} /></div>
      <div className="ln-v-wave">{Array.from({ length: 17 }, (_, i) => <i key={i} style={{ ['--i' as string]: i }} />)}</div>
      <p className="ln-v-quote">«Bosim 150 ga 95, boshi ogʻriyapti»</p>
      <div className="ln-v-result"><span className="orb red flat" style={{ ['--s' as string]: '9px' }} />Xavfli · shifokorga yuborildi</div>
    </div>
  )
}
function InboxVignette() {
  const rows: { z: Zone; n: string; t: string }[] = [
    { z: 'red', n: 'Bosim 150/95, bosh ogʻrigʻi', t: '24:12' },
    { z: 'yellow', n: 'Gemoglobin pasaymoqda', t: '51:40' },
    { z: 'yellow', n: 'Vazn tez ortdi', t: '1:12:05' },
  ]
  return (
    <div className="ln-vig" aria-hidden>
      {rows.map((r, i) => (
        <div key={i} className={`ln-v-row${i === 0 ? ' first' : ''}`}>
          <span className={`orb ${r.z}`} style={{ ['--s' as string]: '10px' }} />
          <span className="grow ln-v-row-t">{r.n}</span>
          <span className="ob-mono ln-v-time">{r.t}</span>
        </div>
      ))}
      <div className="ln-v-actions"><span className="ln-v-btn on">Yoʻllanma</span><span className="ln-v-btn">Qayta tekshirish</span></div>
    </div>
  )
}
const CELLS = 'ggggyggggggggrgggygggggggggyggggggggggggygggggrggggggg'
function MapVignette() {
  return (
    <div className="ln-vig" aria-hidden>
      <div className="ln-v-grid">
        {CELLS.split('').map((c, i) => <i key={i} className={c === 'r' ? 'red' : c === 'y' ? 'yellow' : 'green'} />)}
      </div>
      <div className="ln-v-kpis">
        <span><b className="num">199</b> tuman</span>
        <span><b className="num">2</b> xavfli</span>
        <span><b className="num">11</b> daq javob</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- content
const BREAKS: { icon: IconName; title: string; text: string }[] = [
  { icon: 'file', title: 'Qogʻoz', text: 'Hamshira koʻrsatkichlarni qogʻozga yozadi. Maʼlumot daftarda qoladi.' },
  { icon: 'clock', title: 'Kech oʻqilgan tahlil', text: 'Tahlil natijasi keyingi tashrifgacha oʻqilmaydi.' },
  { icon: 'list', title: 'Haftalik Excel', text: 'Mutaxassis haftalik Excel oladi. Xavfli ayollar roʻyxati yoʻq.' },
  { icon: 'home', title: 'Uzilgan xabar', text: 'Tugʻruqdan keyin mahallaga xabar bormaydi.' },
]
const PEOPLE: { icon: IconName; who: string; line: string; text: string; vig: ReactNode }[] = [
  { icon: 'nurse', who: 'Hamshira', line: 'Gapiradi — rang chiqadi.', text: 'Koʻrsatkichlarni oʻzbekcha aytadi. Ekran yashil, sariq yoki qizil boʻladi.', vig: <NurseVignette /> },
  { icon: 'inbox', who: 'Mutaxassis', line: 'Kiruvchi quti. Sanoq. Qaror.', text: 'Javob kutayotganlar eng xavflisidan boshlab turadi. Vaqt sanaladi.', vig: <InboxVignette /> },
  { icon: 'map', who: 'Rahbar', line: '199 tuman — bitta jonli xarita.', text: 'Qaysi tumanda javob kechikayotgani bir qarashda koʻrinadi.', vig: <MapVignette /> },
]
const STEPS: { icon: IconName; title: string; text: string }[] = [
  { icon: 'mic', title: 'Maʼlumot ovoz bilan kiradi', text: 'Hamshira gapiradi, tizim koʻrsatkichlarni oʻzi ajratadi.' },
  { icon: 'trending-up', title: 'Qoidalar va trend', text: 'Ochiq klinik qoidalar va trend xavfni erta ogohlantiradi.' },
  { icon: 'bell', title: 'Signal va 30 daqiqa', text: 'Javob nazoratda. Javob boʻlmasa, signal yuqoriga koʻtariladi.' },
  { icon: 'map', title: 'Tuman va viloyat xaritasi', text: 'Rahbar holatni jonli xaritada koʻradi.' },
]
const EDGE: { icon: IconName; title: string; text: string }[] = [
  { icon: 'mic', title: 'Oʻzbekcha ovoz', text: 'Yozish emas, gapirish. Bir tashrif — bir daqiqa.' },
  { icon: 'check-circle', title: 'Signal oʻzi yopilmaydi', text: 'Javob tasdiqlanmaguncha signal ochiq turadi.' },
  { icon: 'activity', title: 'Trendni koʻradi', text: 'Chegaraga yetmasdan oldin ogohlantiradi.' },
  { icon: 'lock', title: 'Maʼlumot ichkarida qoladi', text: 'Bemor maʼlumoti tashqi AI ga chiqmaydi.' },
]
const SAFE: { icon: IconName; title: string; text: string }[] = [
  { icon: 'lock', title: 'Shifrlash', text: 'Shaxsiy maʼlumot shifrlangan holda saqlanadi.' },
  { icon: 'users', title: 'Rollar', text: 'Har kim faqat oʻz hududi va vazifasini koʻradi.' },
  { icon: 'layers', title: 'Audit zanjiri', text: 'Har bir harakat oʻzgartirib boʻlmaydigan jurnalga yoziladi.' },
  { icon: 'eye-off', title: 'k-anonimlik', text: 'Kichik guruhlar statistikada yashiriladi.' },
  { icon: 'fingerprint', title: '2 bosqichli kirish', text: 'Passkey yoki Authenticator kodi bilan.' },
]

export default function LandingScreen() {
  const nav = useNavigate()
  const ref = useReveal<HTMLDivElement>()
  const session = getSession()
  const enter = () => nav(session ? homeFor(roleOf(session.user)) : '/kirish')
  const enterLabel = session ? 'Tizimga oʻtish' : 'Kirish'
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 8)
    h()
    window.addEventListener('scroll', h, { passive: true })
    return () => window.removeEventListener('scroll', h)
  }, [])

  return (
    <div className="ln" ref={ref}>
      <header className={`ln-bar${scrolled ? ' on' : ''}`}>
        <div className="ln-bar-in">
          <button className="brand" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })} aria-label="Tayanch — sahifa boshiga">
            <BrandMark /><span>Tayanch</span>
          </button>
          <nav className="ln-nav" aria-label="Sahifa boʻlimlari">
            {NAV.map((n) => <button key={n.id} onClick={() => jump(n.id)}>{n.label}</button>)}
          </nav>
          <ThemeToggle />
          <button className="btn small" onClick={enter}>{enterLabel}</button>
        </div>
      </header>

      <main>
        {/* ---- hero */}
        <section className="ln-hero">
          <div className="ln-wrap ln-hero-in">
            <p className="ob-eyebrow fade-in">Homiladorlar va chaqaloqlar uchun xavf nazorati</p>
            <h1 className="ln-h1 fade-in">Har bir ona va chaqaloq oʻz vaqtida koʻrilsin.</h1>
            <p className="ln-lead fade-in">Hamshira gapiradi. Xavf bir daqiqada koʻrinadi. Javob nazoratda turadi.</p>
            <div className="ln-cta fade-in">
              <button className="btn" onClick={enter}>{enterLabel}<Icon name="arrow-right" size={20} /></button>
              <button className="btn ghost" onClick={() => jump('qanday')}>Qanday ishlaydi</button>
            </div>
            <div className="ln-preview-wrap fade-in"><Preview /></div>
          </div>
        </section>

        {/* ---- problem */}
        <section className="ln-sec" id="muammo">
          <div className="ln-wrap">
            <R className="ln-sec-head">
              <p className="ob-eyebrow">Muammo</p>
              <h2 className="ln-h2">Zanjir toʻrt joyda uziladi.</h2>
              <p className="ln-sub">Xavf bor. Lekin uni mutaxassis kech biladi.</p>
            </R>
            <div className="ln-grid4">
              {BREAKS.map((b, i) => (
                <R key={b.title} d={i * 80} as="article" className="ln-card ob-glass">
                  <div className="ln-card-top"><span className="ln-ic"><Icon name={b.icon} size={22} /></span><span className="ln-no num">0{i + 1}</span></div>
                  <h3>{b.title}</h3>
                  <p className="muted">{b.text}</p>
                </R>
              ))}
            </div>
          </div>
        </section>

        {/* ---- solution */}
        <section className="ln-sec" id="yechim">
          <div className="ln-wrap">
            <R className="ln-sec-head">
              <p className="ob-eyebrow">Yechim</p>
              <h2 className="ln-h2">3 odam. 3 ekran.</h2>
              <p className="ln-sub">Har kimga bitta ekran va bitta savol.</p>
            </R>
            <div className="ln-grid3">
              {PEOPLE.map((p, i) => (
                <R key={p.who} d={i * 100} as="article" className="ln-person ob-glass sheen">
                  {p.vig}
                  <div className="ln-person-txt">
                    <span className="ob-chip"><Icon name={p.icon} size={16} />{p.who}</span>
                    <h3 className="ln-h3">{p.line}</h3>
                    <p className="muted">{p.text}</p>
                  </div>
                </R>
              ))}
            </div>
          </div>
        </section>

        {/* ---- how it works */}
        <section className="ln-sec" id="qanday">
          <div className="ln-wrap">
            <R className="ln-sec-head">
              <p className="ob-eyebrow">Qanday ishlaydi</p>
              <h2 className="ln-h2">Ovozdan xaritagacha. Toʻrt qadam.</h2>
            </R>
            <ol className="ln-steps">
              {STEPS.map((s, i) => (
                <R key={s.title} d={i * 90} as="li" className="ln-step">
                  <div className="ln-step-mark"><span className="ln-step-ic ob-glass"><Icon name={s.icon} size={24} /></span><span className="ln-step-line" /></div>
                  <span className="ln-no num">0{i + 1}</span>
                  <h3>{s.title}</h3>
                  <p className="muted">{s.text}</p>
                </R>
              ))}
            </ol>
          </div>
        </section>

        {/* ---- difference */}
        <section className="ln-sec" id="farqimiz">
          <div className="ln-wrap">
            <R className="ln-sec-head">
              <p className="ob-eyebrow">Farqimiz</p>
              <h2 className="ln-h2">Signal emas. Javob.</h2>
            </R>
            <div className="ln-edge ob-glass">
              {EDGE.map((e, i) => (
                <R key={e.title} d={i * 70} className="ln-edge-it">
                  <span className="ln-ic"><Icon name={e.icon} size={22} /></span>
                  <div><h3>{e.title}</h3><p className="muted">{e.text}</p></div>
                </R>
              ))}
            </div>
          </div>
        </section>

        {/* ---- security */}
        <section className="ln-sec" id="xavfsizlik">
          <div className="ln-wrap">
            <R className="ln-sec-head">
              <p className="ob-eyebrow">Xavfsizlik</p>
              <h2 className="ln-h2">Ishonch birinchi oʻrinda.</h2>
            </R>
            <div className="ln-safe">
              {SAFE.map((s, i) => (
                <R key={s.title} d={i * 60} as="article" className="ln-safe-it ob-glass">
                  <span className="ln-ic"><Icon name={s.icon} size={22} /></span>
                  <h3>{s.title}</h3>
                  <p className="muted small">{s.text}</p>
                </R>
              ))}
            </div>
            <R className="ln-honest">
              <Icon name="info" size={18} />
              <p>Demo: barcha bemorlar sunʼiy. Rasmiy statistika haqiqiy (SIAT). Tizim tashxis qoʻymaydi — qarorni shifokor qabul qiladi.</p>
            </R>
          </div>
        </section>

        {/* ---- final call */}
        <section className="ln-sec ln-final">
          <div className="ln-wrap">
            <R className="ln-final-in ob-glass strong sheen">
              <h2 className="ln-h2">Vaqtida koʻrilgan xavf. Saqlangan hayot.</h2>
              <p className="ln-sub">Tizimni oʻz rolingiz bilan sinab koʻring.</p>
              <div className="ln-cta"><button className="btn" onClick={enter}>{enterLabel}<Icon name="arrow-right" size={20} /></button></div>
            </R>
          </div>
        </section>
      </main>

      <footer className="ln-foot">
        <div className="ln-wrap ln-foot-in">
          <div className="brand"><BrandMark /><span>Tayanch</span></div>
          <span className="small muted grow">Rasmiy statistika manbasi: SIAT, Milliy statistika qoʻmitasi.</span>
          <nav className="ln-foot-nav small" aria-label="Quyi havolalar">
            {NAV.map((n) => <button key={n.id} onClick={() => jump(n.id)}>{n.label}</button>)}
            <Link to="/kirish">Kirish</Link>
          </nav>
        </div>
      </footer>
    </div>
  )
}
