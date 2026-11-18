// Sign-in and sign-up: Google, e-mail or phone with a one-time code, passkey; optional second step.
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import {
  account, api, getSession, homeFor, roleOf,
  type AuthChannel, type AuthMode, type AuthResult, type Challenge, type Persona, type SecondMethod,
} from '../../lib/api'
import { BrandMark, ThemeToggle } from '../../ui/kit'
import { Icon, type IconName } from '../../ui/icons'
import { CodeInput, Note, Seg } from './parts'
import { getAssertion, PasskeyCancelled, passkeySupported } from './webauthn'
import '../landing/base.css'
import './auth.css'

type Step = 'methods' | 'identifier' | 'code' | 'second'

/** "901234567" → "90 123 45 67" */
function phoneMask(digits: string): string {
  const d = digits.slice(0, 9)
  return [d.slice(0, 2), d.slice(2, 5), d.slice(5, 7), d.slice(7, 9)].filter(Boolean).join(' ')
}
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/

// ---- Google Identity Services: the script is requested only when the server says Google sign-in is configured
interface GoogleId {
  initialize: (o: { client_id: string; callback: (r: { credential?: string }) => void; ux_mode?: string }) => void
  renderButton: (el: HTMLElement, o: Record<string, unknown>) => void
}
const gis = (): GoogleId | null => (window as unknown as { google?: { accounts?: { id?: GoogleId } } }).google?.accounts?.id ?? null
let gisLoading: Promise<void> | null = null
function loadGis(): Promise<void> {
  if (gis()) return Promise.resolve()
  if (!gisLoading) {
    gisLoading = new Promise<void>((resolve, reject) => {
      const s = document.createElement('script')
      s.src = 'https://accounts.google.com/gsi/client'
      s.async = true
      s.onload = () => resolve()
      s.onerror = () => { gisLoading = null; reject(new Error('gis')) }
      document.head.appendChild(s)
    })
  }
  return gisLoading
}

function GoogleRow({ onResult, onError, mode }: { onResult: (r: AuthResult) => void; onError: (m: string) => void; mode: AuthMode }) {
  const [state, setState] = useState<'loading' | 'off' | 'ready' | 'failed'>('loading')
  const [clientId, setClientId] = useState<string | null>(null)
  const slot = useRef<HTMLDivElement>(null)
  const cb = useRef({ onResult, onError }); cb.current = { onResult, onError }

  useEffect(() => {
    let alive = true
    account.googleConfig()
      .then((c) => { if (!alive) return; if (c.enabled && c.client_id) { setClientId(c.client_id) } else setState('off') })
      .catch(() => { if (alive) setState('off') })
    return () => { alive = false }
  }, [])
  useEffect(() => {
    if (!clientId) return
    let alive = true
    loadGis().then(() => {
      const g = gis()
      if (!alive || !g || !slot.current) return
      g.initialize({
        client_id: clientId,
        callback: (r) => {
          if (!r.credential) { cb.current.onError('Google orqali kirib boʻlmadi.'); return }
          account.googleVerify(r.credential).then((x) => cb.current.onResult(x)).catch((e: Error) => cb.current.onError(e.message))
        },
      })
      const dark = document.documentElement.getAttribute('data-theme') === 'dark'
      g.renderButton(slot.current, {
        type: 'standard', theme: dark ? 'filled_black' : 'outline', size: 'large', shape: 'pill', logo_alignment: 'left',
        text: mode === 'register' ? 'signup_with' : 'signin_with', locale: 'uz', width: Math.min(400, slot.current.clientWidth || 360),
      })
      setState('ready')
    }).catch(() => { if (alive) setState('failed') })
    return () => { alive = false }
  }, [clientId, mode])

  if (clientId && state !== 'failed') {
    return <div className="au-google" ref={slot} aria-busy={state !== 'ready'} />
  }
  return (
    <div className="au-row off" aria-disabled>
      <span className="au-row-ic"><Icon name="google" size={22} /></span>
      <span className="grow au-row-t">Google</span>
      <span className="tiny muted">{state === 'loading' ? '…' : state === 'failed' ? 'ulanmadi' : 'sozlanmagan'}</span>
    </div>
  )
}

function Row({ icon, title, sub, onClick, disabled }: { icon: IconName; title: string; sub?: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" className="au-row" onClick={onClick} disabled={disabled}>
      <span className="au-row-ic"><Icon name={icon} size={22} /></span>
      <span className="grow col" style={{ gap: 1 }}>
        <span className="au-row-t">{title}</span>
        {sub && <span className="tiny muted">{sub}</span>}
      </span>
      <Icon name="chevron-right" size={18} className="au-row-go" />
    </button>
  )
}

const DEMO: { key: Persona | 'admin' | 'facility_admin'; label: string; icon: IconName }[] = [
  { key: 'nurse', label: 'Hamshira', icon: 'nurse' },
  { key: 'specialist', label: 'Mutaxassis', icon: 'inbox' },
  { key: 'leader', label: 'Rahbar', icon: 'map' },
  { key: 'facility_admin', label: 'Muassasa admini', icon: 'building' },
  { key: 'admin', label: 'Admin', icon: 'settings' },
]

const ROLE_SUB: Record<string, string> = {
  nurse: 'Tashriflarni kiritish', specialist: 'Signallarga javob berish', leader: 'Jonli xarita va hisobot',
  facility_admin: 'Xodimlarni boshqarish', admin: 'Tizim boshqaruvi',
}

export default function AuthScreen() {
  const nav = useNavigate()
  const [mode, setMode] = useState<AuthMode>('login')
  const [step, setStep] = useState<Step>('methods')
  const [channel, setChannel] = useState<AuthChannel>('email')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [fullName, setFullName] = useState('')
  const [challenge, setChallenge] = useState<Challenge | null>(null)
  const [code, setCode] = useState('')
  const [second, setSecond] = useState<{ ticket: string; methods: SecondMethod[] } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [resendAt, setResendAt] = useState(0)
  const [, tick] = useState(0)
  const startedSignedIn = useRef(getSession() !== null)

  useEffect(() => {
    if (step !== 'code') return
    const t = setInterval(() => tick((n) => n + 1), 500)
    return () => clearInterval(t)
  }, [step])

  if (startedSignedIn.current) return <Navigate to={homeFor(roleOf(getSession()?.user))} replace />

  const finish = (r: AuthResult) => {
    if (r.kind === 'second') { setSecond({ ticket: r.ticket, methods: r.methods }); setCode(''); setErr(null); setStep('second'); return }
    nav(homeFor(roleOf(r.session.user)), { replace: true })
  }
  const run = async (key: string, fn: () => Promise<void>) => {
    if (busy) return
    setBusy(key); setErr(null)
    try { await fn() } catch (e) { if (!(e instanceof PasskeyCancelled)) setErr((e as Error).message || 'Amal bajarilmadi. Qayta urinib koʻring.') } finally { setBusy(null) }
  }

  const identifier = channel === 'email' ? email.trim().toLowerCase() : `+998${phone}`
  const idValid = channel === 'email' ? EMAIL.test(email.trim()) : phone.length === 9
  const nameValid = mode === 'login' || fullName.trim().length >= 3

  const sendCode = () => run('start', async () => {
    const c = await account.start({ mode, channel, identifier, ...(mode === 'register' ? { full_name: fullName.trim() } : {}) })
    setChallenge(c); setCode(''); setResendAt(Date.now() + 60000); setStep('code')
  })
  const submitId = (e: FormEvent) => { e.preventDefault(); if (idValid && nameValid) void sendCode() }
  const verify = (v: string) => run('verify', async () => {
    if (!challenge) return
    try { finish(await account.verify(challenge.challenge_id, v)) } catch (e) { setCode(''); throw e }
  })
  const verifySecond = (v: string) => run('second', async () => {
    if (!second) return
    try { finish(await account.second(second.ticket, v)) } catch (e) { setCode(''); throw e }
  })
  const passkey = (ticket: string | null) => run('passkey', async () => {
    const o = await account.passkeyLoginOptions(null, ticket)
    const assertion = await getAssertion(o.publicKey)
    finish(await account.passkeyLoginVerify({ state: o.state, ...assertion }))
  })
  const demo = (p: Persona | 'admin' | 'facility_admin') => run(`demo-${p}`, async () => {
    const s = await api.login(p)
    nav(homeFor(p === 'admin' ? 'admin' : roleOf(s.user)), { replace: true })
  })

  const pick = (c: AuthChannel) => { setChannel(c); setErr(null); setStep('identifier') }
  const back = () => { setErr(null); setCode(''); setStep(step === 'code' ? 'identifier' : 'methods') }
  const switchMode = (m: AuthMode) => { setMode(m); setErr(null); if (step !== 'identifier') setStep('methods') }
  const wait = Math.max(0, Math.ceil((resendAt - Date.now()) / 1000))

  let title: string, sub: ReactNode
  if (step === 'code') { title = 'Kodni kiriting'; sub = <>6 xonali kod <b>{challenge?.sent_to || identifier}</b> manziliga yuborildi.</> }
  else if (step === 'second') { title = 'Ikkinchi bosqich'; sub = 'Hisobingiz 2 bosqichli kirish bilan himoyalangan.' }
  else if (mode === 'register') { title = 'Roʻyxatdan oʻtish'; sub = 'Administrator rol bergach, tizim ochiladi.' }
  else if (step === 'identifier') { title = 'Xush kelibsiz'; sub = 'Bir martalik kod yuboramiz.' }
  else { title = 'Xush kelibsiz'; sub = 'Rolingizni tanlang.' }

  return (
    <div className="au">
      <header className="ob-head">
        <Link to="/" className="brand" aria-label="Bosh sahifa"><BrandMark /><span>Tayanch</span></Link>
        <span className="grow" />
        <ThemeToggle />
      </header>

      <main className="au-main">
        <section className="au-card ob-glass strong sheen fade-in" aria-labelledby="au-title">

          <div className="au-title">
            {step !== 'methods' && (
              <button type="button" className="ob-icon-btn" onClick={back} aria-label="Orqaga"><Icon name="arrow-left" size={18} /></button>
            )}
            <div className="grow">
              <h1 id="au-title">{title}</h1>
              <p className="muted">{sub}</p>
            </div>
          </div>

          {step === 'methods' && (
            <div className="au-rows fade-in">
              {DEMO.map((d) => (
                <Row key={d.key} icon={d.icon} title={d.label} sub={ROLE_SUB[d.key]} onClick={() => demo(d.key)} disabled={busy !== null} />
              ))}
            </div>
          )}

          {step === 'identifier' && (
            <form className="au-form fade-in" onSubmit={submitId} noValidate key={`i-${channel}`}>
              {mode === 'register' && (
                <label className="ob-field">
                  <span className="ob-label">Ism va familiya</span>
                  <input className="ob-input" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" placeholder="Masalan: Dilnoza Karimova" autoFocus />
                </label>
              )}
              {channel === 'email' ? (
                <label className="ob-field">
                  <span className="ob-label">Email</span>
                  <input className="ob-input" type="email" inputMode="email" value={email} onChange={(e) => setEmail(e.target.value)}
                    autoComplete="email" placeholder="ism@muassasa.uz" autoFocus={mode === 'login'} autoCapitalize="none" spellCheck={false} />
                </label>
              ) : (
                <label className="ob-field">
                  <span className="ob-label">Telefon raqam</span>
                  <span className="au-phone">
                    <span className="au-phone-pre ob-mono">+998</span>
                    <input className="ob-input ob-mono" type="tel" inputMode="numeric" value={phoneMask(phone)} autoFocus={mode === 'login'}
                      onChange={(e) => { let d = e.target.value.replace(/\D/g, ''); if (d.length > 9 && d.startsWith('998')) d = d.slice(3); setPhone(d.slice(0, 9)) }}
                      autoComplete="tel-national" placeholder="90 123 45 67" aria-label="Telefon raqam, +998 dan keyingi 9 ta raqam" />
                  </span>
                </label>
              )}
              <button className="btn block" type="submit" disabled={!idValid || !nameValid || busy !== null}>
                {busy === 'start' ? 'Yuborilmoqda…' : 'Kod yuborish'}
              </button>
            </form>
          )}

          {step === 'code' && challenge && (
            <div className="au-form fade-in">
              {challenge.dev_code && (
                <Note kind="demo" icon="info">Demo rejimi: kod — <b className="ob-mono">{challenge.dev_code}</b> (xabar yuborish xizmati ulanmagan)</Note>
              )}
              <CodeInput value={code} onChange={(v) => { setCode(v); if (err) setErr(null) }} onComplete={verify} disabled={busy === 'verify'} />
              <button className="btn block" onClick={() => verify(code)} disabled={code.length !== 6 || busy !== null}>
                {busy === 'verify' ? 'Tekshirilmoqda…' : 'Tasdiqlash'}
              </button>
              <p className="small center muted">
                Kod kelmadimi?{' '}
                <button type="button" className="ob-link" disabled={wait > 0 || busy !== null} onClick={sendCode}>
                  {wait > 0 ? <span className="num">Qayta yuborish · {Math.floor(wait / 60)}:{String(wait % 60).padStart(2, '0')}</span> : 'Qayta yuborish'}
                </button>
              </p>
            </div>
          )}

          {step === 'second' && second && (
            <div className="au-form fade-in">
              {second.methods.includes('totp') && (
                <>
                  <p className="ob-label">Authenticator ilovasidagi 6 xonali kod</p>
                  <CodeInput value={code} onChange={(v) => { setCode(v); if (err) setErr(null) }} onComplete={verifySecond} disabled={busy === 'second'} label="Authenticator kodi" />
                  <button className="btn block" onClick={() => verifySecond(code)} disabled={code.length !== 6 || busy !== null}>
                    {busy === 'second' ? 'Tekshirilmoqda…' : 'Kirish'}
                  </button>
                </>
              )}
              {second.methods.includes('passkey') && passkeySupported() && (
                <button className={`btn block ${second.methods.includes('totp') ? 'ghost' : ''}`} onClick={() => passkey(second.ticket)} disabled={busy !== null}>
                  <Icon name="fingerprint" size={20} />Passkey bilan tasdiqlash
                </button>
              )}
              {second.methods.includes('passkey') && !second.methods.includes('totp') && !passkeySupported() && (
                <Note kind="err">Bu brauzer passkeyni qoʻllamaydi. Passkey qoʻshilgan qurilmadan kiring.</Note>
              )}
            </div>
          )}

          {err && <Note kind="err">{err}</Note>}

        </section>
        <p className="tiny muted center au-foot">Tizim tashxis qoʻymaydi. Qarorni shifokor qabul qiladi.</p>
      </main>
    </div>
  )
}
