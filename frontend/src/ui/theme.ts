// Theme: follows the system until the user picks one; the choice is kept in localStorage.
// index.html applies the same rule before first paint, so there is no flash.
import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'
const KEY = 'theme'
const listeners = new Set<() => void>()

function stored(): Theme | null {
  try { const t = localStorage.getItem(KEY); return t === 'dark' || t === 'light' ? t : null } catch { return null }
}
function system(): Theme {
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}
export function getTheme(): Theme {
  const a = document.documentElement.getAttribute('data-theme')
  return a === 'dark' || a === 'light' ? a : stored() ?? system()
}

let animTimer: ReturnType<typeof setTimeout> | null = null
function apply(t: Theme, animate: boolean) {
  const root = document.documentElement
  if (root.getAttribute('data-theme') === t) return
  if (animate) {
    root.classList.add('theme-anim')
    if (animTimer) clearTimeout(animTimer)
    animTimer = setTimeout(() => root.classList.remove('theme-anim'), 320)
  }
  root.setAttribute('data-theme', t)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t === 'dark' ? '#070708' : '#F3F2EF')
  listeners.forEach((l) => l())
}

export function setTheme(t: Theme) {
  try { localStorage.setItem(KEY, t) } catch { /* private mode: the choice lasts for this page only */ }
  apply(t, true)
}

if (typeof window !== 'undefined') {
  if (!document.documentElement.getAttribute('data-theme')) apply(stored() ?? system(), false)
  const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
  mq?.addEventListener?.('change', () => { if (!stored()) apply(system(), true) })
  window.addEventListener('storage', (e) => { if (e.key === KEY) apply(stored() ?? system(), true) })
}

const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }
export function useTheme(): Theme { return useSyncExternalStore(subscribe, getTheme, () => 'light' as Theme) }
