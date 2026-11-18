// Account security: one- or two-step sign-in, passkeys, authenticator app, linked methods, sessions.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { account, getSession, homeFor, roleOf, type Role, type SecurityState } from '../../lib/api'
import { BrandMark, ThemeToggle, initials } from '../../ui/kit'
import { Icon, type IconName } from '../../ui/icons'
import { CodeInput, Note, Seg, fmtDateTime } from '../auth/parts'
import { createAttestation, PasskeyCancelled, passkeySupported } from '../auth/webauthn'
import { qrMatrix, qrPath } from './qr'
import '../landing/base.css'
import './account.css'

export const ROLE_WORD: Record<Role, string> = { admin: 'Administrator', leader: 'Rahbar', facility_admin: 'Muassasa administratori', specialist: 'Mutaxassis', nurse: 'Hamshira', pending: 'Rol kutilmoqda' }

function Card({ icon, title, sub, children, aside }: { icon: IconName; title: string; sub?: string; children?: ReactNode; aside?: ReactNode }) {
  return (
    <section className="ac-card ob-glass">
      <header className="ac-card-head">
        <span className="ac-ic"><Icon name={icon} size={22} /></span>
        <div className="grow"><h2>{title}</h2>{sub && <p className="muted small">{sub}</p>}</div>
        {aside}
      </header>
      {children}
    </section>
  )
}

function Qr({ text }: { text: string }) {
  const qr = useMemo(() => { const m = qrMatrix(text); return m ? qrPath(m, 3) : null }, [text])
  if (!qr) return null
  // a QR code is always dark on white, in both themes, so every scanner reads it
  return (
    <svg className="ac-qr" viewBox={`0 0 ${qr.size} ${qr.size}`} role="img" aria-label="Authenticator uchun QR kod" shapeRendering="crispEdges">
      <rect width={qr.size} height={qr.size} fill="#fff" />
      <path d={qr.d} fill="#000" />
    </svg>
  )
}

async function copy(text: string): Promise<boolean> {
  try { await navigator.clipboard.writeText(text); return true } catch { return false }
}
function CopyBtn({ text, label = 'Nusxa olish' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button type="button" className="btn small soft" onClick={async () => { if (await copy(text)) { setDone(true); setTimeout(() => setDone(false), 1600) } }}>
      <Icon name={done ? 'check' : 'copy'} size={16} />{done ? 'Nusxa olindi' : label}
    </button>
  )
}

export default function AccountScreen() {
  const nav = useNavigate()
  const session = getSession()
  const [st, setSt] = useState<SecurityState | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ where: string; kind: 'err' | 'ok'; text: string } | null>(null)
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null)
  const [code, setCode] = useState('')
  const [recovery, setRecovery] = useState<string[] | null>(null)
  const [disabling, setDisabling] = useState(false)

  const load = useCallback(async () => {
    try { setSt(await account.security()); setLoadErr(null) } catch (e) { setLoadErr((e as Error).message) }
  }, [])
  useEffect(() => { if (session) void load() }, [load, session !== null]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!session) return <Navigate to="/kirish" replace />
  const role = roleOf(session.user)
  const home = homeFor(role)

  const run = async (key: string, where: string, fn: () => Promise<string | void>) => {
    if (busy) return
    setBusy(key); setMsg(null)
    try { const ok = await fn(); if (ok) setMsg({ where, kind: 'ok', text: ok }) }
    catch (e) { if (!(e instanceof PasskeyCancelled)) setMsg({ where, kind: 'err', text: (e as Error).message || 'Amal bajarilmadi.' }) }
    finally { setBusy(null) }
  }
  const says = (where: string) => (msg && msg.where === where ? <Note kind={msg.kind}>{msg.text}</Note> : null)

  const hasSecond = !!st && (st.methods.totp || st.methods.passkeys.length > 0)
  const setTwo = (on: boolean) => run('two', 'two', async () => {
    if (!st || st.two_step === on) return
    if (on && !hasSecond) throw new Error('Avval Passkey yoki Authenticator qoʻshing — ikkinchi bosqich shu bilan tasdiqlanadi.')
    setSt(await account.setTwoStep(on))
    return on ? '2 bosqichli kirish yoqildi.' : '1 bosqichli kirish tanlandi.'
  })
  const addPasskey = () => run('pk-add', 'pk', async () => {
    const o = await account.passkeyRegisterOptions()
    const att = await createAttestation(o.publicKey)
    const r = await account.passkeyRegisterVerify(att)
    setSt((s) => (s ? { ...s, methods: { ...s.methods, passkeys: r.passkeys ?? s.methods.passkeys } } : s))
    void load()
    return 'Passkey qoʻshildi.'
  })
  const delPasskey = (id: string) => run(`pk-${id}`, 'pk', async () => {
    await account.passkeyDelete(id)
    await load()
    return 'Passkey olib tashlandi.'
  })
  const startTotp = () => run('totp-setup', 'totp', async () => { setRecovery(null); setCode(''); setSetup(await account.totpSetup()) })
  const enableTotp = (v: string) => run('totp-enable', 'totp', async () => {
    try {
      const r = await account.totpEnable(v)
      setRecovery(Array.isArray(r.recovery_codes) ? r.recovery_codes : [])
      setSetup(null); setCode('')
      await load()
      return 'Authenticator ulandi.'
    } catch (e) { setCode(''); throw e }
  })
  const disableTotp = (v: string) => run('totp-disable', 'totp', async () => {
    try { await account.totpDisable(v); setDisabling(false); setCode(''); setRecovery(null); await load(); return 'Authenticator oʻchirildi.' } catch (e) { setCode(''); throw e }
  })
  const logout = async () => { setBusy('out'); await account.logout(); nav('/', { replace: true }) }

  const u = session.user
  const secretGroups = setup ? (setup.secret.match(/.{1,4}/g) ?? []).join(' ') : ''

  return (
    <div className="ac">
      <header className="ob-head">
        <Link to={home} className="ob-icon-btn" aria-label="Orqaga"><Icon name="arrow-left" size={18} /></Link>
        <Link to="/" className="brand" aria-label="Bosh sahifa"><BrandMark /><span>Tayanch</span></Link>
        <span className="grow" />
        <ThemeToggle />
      </header>

      <main className="ac-main fade-in">
        <div className="ac-hero">
          <span className="ac-avatar">{initials(u.name || 'F') || 'F'}</span>
          <div className="grow">
            <h1>{u.name || 'Foydalanuvchi'}</h1>
            <p className="muted">{u.role_label || (role ? ROLE_WORD[role] : '')}{u.region_name ? ` · ${u.region_name}` : ''}{u.district_name ? ` · ${u.district_name}` : ''}</p>
          </div>
        </div>

        {loadErr && !st && (
          <Note kind="info">
            {loadErr} <button type="button" className="ob-link" onClick={load}>Qayta urinish</button>
            {!u.role && <><br />Namoyish rejimidagi hisobda xavfsizlik sozlamalari boʻlmaydi — ular roʻyxatdan oʻtgan hisoblar uchun.</>}
          </Note>
        )}
        {!st && !loadErr && <div className="col"><div className="skel" style={{ height: 150 }} /><div className="skel" style={{ height: 150 }} /></div>}

        {st && (
          <>
            <Card icon="shield" title="Kirish usuli" sub={st.two_step ? 'Kod yoki Googledan keyin ikkinchi tasdiq soʻraladi.' : 'Bitta tasdiq bilan kirasiz.'}>
              <Seg block label="Kirish bosqichlari" value={st.two_step ? 2 : 1} onChange={(v) => setTwo(v === 2)}
                options={[{ value: 1, label: '1 bosqichli' }, { value: 2, label: '2 bosqichli' }]} />
              {!hasSecond && !st.two_step && <p className="small muted">2 bosqichli kirish uchun avval Passkey yoki Authenticator qoʻshing.</p>}
              {says('two')}
            </Card>

            <Card icon="fingerprint" title="Passkey" sub="Touch ID, barmoq izi yoki qurilma PIN-kodi."
              aside={passkeySupported() ? (
                <button className="btn small soft" onClick={addPasskey} disabled={busy !== null}><Icon name="plus" size={16} />{busy === 'pk-add' ? 'Kutilmoqda…' : 'Qoʻshish'}</button>
              ) : undefined}>
              {!passkeySupported() && <p className="small muted">Bu brauzer passkeyni qoʻllamaydi.</p>}
              {st.methods.passkeys.length > 0 ? (
                <ul className="ac-list">
                  {st.methods.passkeys.map((p) => (
                    <li key={p.id}>
                      <Icon name="key" size={18} />
                      <span className="grow col" style={{ gap: 0 }}><span className="ac-li-t">{p.label}</span><span className="tiny muted">{fmtDateTime(p.created_at)}</span></span>
                      <button className="ob-icon-btn" onClick={() => delPasskey(p.id)} disabled={busy !== null} aria-label={`${p.label} — olib tashlash`} title="Olib tashlash"><Icon name="trash" size={16} /></button>
                    </li>
                  ))}
                </ul>
              ) : <p className="small muted">Hali passkey qoʻshilmagan.</p>}
              {says('pk')}
            </Card>

            <Card icon="key" title="Authenticator" sub="Google Authenticator, Microsoft Authenticator yoki shunga oʻxshash ilova."
              aside={st.methods.totp ? <span className="ob-status green"><Icon name="check" size={14} />Ulangan</span>
                : !setup ? <button className="btn small soft" onClick={startTotp} disabled={busy !== null}>{busy === 'totp-setup' ? 'Tayyorlanmoqda…' : 'Sozlash'}</button> : undefined}>
              {setup && !st.methods.totp && (
                <div className="ac-totp">
                  <div className="ac-qr-box"><Qr text={setup.otpauth_uri} /></div>
                  <div className="col grow" style={{ gap: 14, minWidth: 0 }}>
                    <p className="small"><b>1.</b> Ilovada QR kodni skanerlang yoki kalitni qoʻlda kiriting.</p>
                    <div className="ac-secret">
                      <code className="ob-mono">{secretGroups}</code>
                      <div className="row" style={{ flexWrap: 'wrap', gap: 8 }}>
                        <CopyBtn text={setup.secret} label="Kalitdan nusxa" />
                        <a className="btn small ghost" href={setup.otpauth_uri}><Icon name="external" size={16} />Ilovada ochish</a>
                      </div>
                    </div>
                    <p className="small"><b>2.</b> Ilova koʻrsatgan 6 xonali kodni kiriting.</p>
                    <CodeInput value={code} onChange={setCode} onComplete={enableTotp} disabled={busy === 'totp-enable'} autoFocus={false} label="Authenticator kodi" />
                    <div className="row">
                      <button className="btn small" onClick={() => enableTotp(code)} disabled={code.length !== 6 || busy !== null}>{busy === 'totp-enable' ? 'Tekshirilmoqda…' : 'Tasdiqlash'}</button>
                      <button className="btn small ghost" onClick={() => { setSetup(null); setCode('') }}>Bekor qilish</button>
                    </div>
                  </div>
                </div>
              )}
              {recovery && recovery.length > 0 && (
                <div className="ac-recovery">
                  <Note kind="demo" icon="key"><b>Zaxira kodlari faqat bir marta koʻrsatiladi.</b> Ularni xavfsiz joyga yozib qoʻying — telefon yoʻqolsa, shu kodlar bilan kirasiz.</Note>
                  <ul className="ac-codes ob-mono">{recovery.map((c) => <li key={c}>{c}</li>)}</ul>
                  <div className="row"><CopyBtn text={recovery.join('\n')} /><button className="btn small ghost" onClick={() => setRecovery(null)}>Yozib oldim</button></div>
                </div>
              )}
              {st.methods.totp && !disabling && (
                <button className="ob-link small ac-left" onClick={() => { setDisabling(true); setCode(''); setMsg(null) }}>Authenticatorni oʻchirish</button>
              )}
              {st.methods.totp && disabling && (
                <div className="col" style={{ gap: 12 }}>
                  <p className="small">Oʻchirish uchun ilovadagi kodni kiriting.</p>
                  <CodeInput value={code} onChange={setCode} onComplete={disableTotp} disabled={busy === 'totp-disable'} label="Authenticator kodi" />
                  <div className="row">
                    <button className="btn small red" onClick={() => disableTotp(code)} disabled={code.length !== 6 || busy !== null}>Oʻchirish</button>
                    <button className="btn small ghost" onClick={() => { setDisabling(false); setCode('') }}>Bekor qilish</button>
                  </div>
                </div>
              )}
              {says('totp')}
            </Card>

            <Card icon="user" title="Bogʻlangan usullar" sub="Shu usullar bilan hisobingizga kirasiz.">
              <ul className="ac-list">
                {([
                  { icon: 'mail', label: 'Email', value: st.methods.email },
                  { icon: 'phone', label: 'Telefon raqam', value: st.methods.phone },
                  { icon: 'google', label: 'Google', value: st.methods.google ? 'Ulangan' : null },
                ] as { icon: IconName; label: string; value: string | null }[]).map((m) => (
                  <li key={m.label}>
                    <Icon name={m.icon} size={18} />
                    <span className="grow ac-li-t">{m.label}</span>
                    <span className={m.value ? 'small ob-mono ac-val' : 'small muted'}>{m.value ?? 'Ulanmagan'}</span>
                  </li>
                ))}
              </ul>
            </Card>

            <Card icon="clock" title="Faol seanslar" sub="Hisobingiz ochiq turgan qurilmalar.">
              {st.sessions.length > 0 ? (
                <ul className="ac-list">
                  {st.sessions.map((x) => (
                    <li key={x.id}>
                      <Icon name="globe" size={18} />
                      <span className="grow ac-li-t num">{fmtDateTime(x.created_at) || 'Seans'}</span>
                      {x.current && <span className="ob-status green">Shu qurilma</span>}
                    </li>
                  ))}
                </ul>
              ) : <p className="small muted">Faol seanslar yoʻq.</p>}
            </Card>
          </>
        )}

        <div className="ac-out">
          <button className="btn ghost" onClick={logout} disabled={busy === 'out'}><Icon name="logout" size={18} />Chiqish</button>
        </div>
      </main>
    </div>
  )
}
