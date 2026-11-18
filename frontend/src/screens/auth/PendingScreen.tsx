// Shown to a registered account that has no role yet. Checks quietly whether an admin has approved it.
import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { account, getSession, homeFor, roleOf } from '../../lib/api'
import { BrandMark, ThemeToggle } from '../../ui/kit'
import { Icon } from '../../ui/icons'
import '../landing/base.css'
import './auth.css'

export default function PendingScreen() {
  const nav = useNavigate()
  const session = getSession()
  const [checking, setChecking] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const check = async (manual: boolean) => {
    if (manual) { setChecking(true); setNote(null) }
    try {
      const u = await account.me()
      const role = roleOf(u)
      if (role && role !== 'pending') { nav(homeFor(role), { replace: true }); return }
      if (manual) setNote('Hali rol berilmagan.')
    } catch (e) { if (manual) setNote((e as Error).message) } finally { if (manual) setChecking(false) }
  }
  useEffect(() => {
    if (!session) return
    const t = setInterval(() => { void check(false) }, 20000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!session) return <Navigate to="/kirish" replace />
  const role = roleOf(session.user)
  if (role !== 'pending') return <Navigate to={homeFor(role)} replace />
  const logout = async () => { await account.logout(); nav('/', { replace: true }) }

  return (
    <div className="au">
      <header className="ob-head">
        <Link to="/" className="brand" aria-label="Bosh sahifa"><BrandMark /><span>Tayanch</span></Link>
        <span className="grow" />
        <ThemeToggle />
      </header>
      <main className="au-main">
        <section className="au-card au-wait ob-glass strong sheen fade-in">
          <span className="au-wait-ic"><Icon name="clock" size={32} /></span>
          <h1>Arizangiz qabul qilindi.</h1>
          <p className="muted">Administrator rol bergach, tizim ochiladi.</p>
          {session.user.name && <span className="ob-chip"><Icon name="user" size={16} />{session.user.name}</span>}
          {note && <p className="small muted" role="status">{note}</p>}
          <div className="row" style={{ flexWrap: 'wrap', justifyContent: 'center' }}>
            <button className="btn soft" onClick={() => check(true)} disabled={checking}><Icon name="refresh" size={18} />{checking ? 'Tekshirilmoqda…' : 'Tekshirish'}</button>
            <button className="btn ghost" onClick={logout}><Icon name="logout" size={18} />Chiqish</button>
          </div>
          <Link to="/hisob" className="ob-link small">Hisob xavfsizligi</Link>
        </section>
      </main>
    </div>
  )
}
