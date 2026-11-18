import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { account, getSession, roleOf } from '../lib/api'
import { BrandMark, DemoBadge, ThemeToggle, initials } from './kit'
import { Icon } from './icons'

/** Avatar → small glass menu: account, admin panel (admins only), sign out. */
function UserMenu() {
  const nav = useNavigate()
  const s = getSession()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', key)
    return () => { window.removeEventListener('keydown', key) }
  }, [open])
  if (!s) return null
  const go = (path: string) => { setOpen(false); nav(path) }
  const out = () => { setOpen(false); void account.logout(); nav('/') }
  return (
    <div className="menu-wrap" ref={wrap}>
      <button className="avatar" title={s.user.name} aria-label="Hisob menyusi" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {initials(s.user.name)}
      </button>
      {open && createPortal(
        <>
          <div className="pop-scrim" onClick={() => setOpen(false)} aria-hidden />
        <div className="menu menu-fixed pop-in" role="menu">
          <div className="menu-head">
            <b>{s.user.name}</b>
            <span className="tiny muted">{s.user.role_label}</span>
          </div>
          <button role="menuitem" className="menu-item" onClick={() => go('/hisob')}><Icon name="shield" size={18} />Hisob va xavfsizlik</button>
          {roleOf(s.user) === 'facility_admin' && (
            <button role="menuitem" className="menu-item" onClick={() => go('/muassasa')}><Icon name="users" size={18} />Muassasa xodimlari</button>
          )}
          {roleOf(s.user) === 'admin' && (
            <button role="menuitem" className="menu-item" onClick={() => go('/admin')}><Icon name="settings" size={18} />Admin paneli</button>
          )}
          <hr className="divider" />
          <button role="menuitem" className="menu-item" onClick={out}><Icon name="logout" size={18} />Chiqish</button>
        </div>
        </>, document.body)}
    </div>
  )
}

/** Shared glass top bar. `children` renders in the middle (screen-specific, keep it minimal). */
export function TopBar({ children, compact = false }: { children?: ReactNode; compact?: boolean }) {
  return (
    <header className="topbar">
      <div className="brand"><BrandMark />{!compact && <span>Tayanch</span>}</div>
      <div className="topbar-mid">{children}</div>
      <DemoBadge />
      {!compact && (
        <button className="glass-chip cmdk-btn" title="Qidirish va buyruqlar" aria-label="Qidirish va buyruqlar"
          onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}>
          <span>Qidirish</span><span className="kbd">⌘K</span>
        </button>
      )}
      <ThemeToggle />
      <UserMenu />
    </header>
  )
}
