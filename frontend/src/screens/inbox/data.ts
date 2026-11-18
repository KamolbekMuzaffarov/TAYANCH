// Data source for the inbox: the real API always, except when the URL has ?mock=1.
import { api, type InboxItem } from '../../lib/api'
import { mockApi } from './mock'

export const MOCK = new URLSearchParams(window.location.search).get('mock') === '1'

export const src = MOCK
  ? mockApi
  : { inbox: api.inbox, patient: api.patient, newborn: api.newborn, ack: api.ack, decide: api.decide }

export const LIST_PATH = `/mutaxassis${MOCK ? '?mock=1' : ''}`

export function itemPath(i: InboxItem): string {
  const base = i.subject_type === 'newborn' ? `/mutaxassis/chaqaloq/${i.newborn_id}` : `/mutaxassis/bemor/${i.pregnancy_id}`
  return `${base}?signal=${encodeURIComponent(i.alert_id)}${MOCK ? '&mock=1' : ''}`
}

/** True when the key event should be left alone (typing, shortcuts with modifiers, open dialogs). */
export function ignoreKey(e: KeyboardEvent): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey) return true
  const t = e.target as HTMLElement | null
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return true
  return !!document.querySelector('.cmdk-backdrop')
}
