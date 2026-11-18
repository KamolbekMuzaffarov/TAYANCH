// One icon set for the whole app. 24 × 24 grid, stroke 1.75, round caps and joins, `currentColor`.
// Usage: <Icon name="mic" size={20} />   — decorative by default; pass `title` to expose a label.
import type { CSSProperties, ReactNode } from 'react'

const P: Record<string, ReactNode> = {
  // ---- actions
  mic: <><rect x="9" y="2.5" width="6" height="11.5" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.500v4M8.500 21.500h7" /></>,
  stop: <rect x="6" y="6" width="12" height="12" rx="3" />,
  play: <path d="M7.500 4.800v14.400a.6.6 0 0 0 .9.500l11.500-7.200a.6.6 0 0 0 0-1L8.400 4.300a.6.6 0 0 0-.9.500Z" />,
  pause: <path d="M8 5v14M16 5v14" />,
  check: <path d="m4.500 12.500 5 5L19.500 6.500" />,
  'check-circle': <><circle cx="12" cy="12" r="9" /><path d="m8 12.300 2.800 2.800L16 9.500" /></>,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  'x-circle': <><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></>,
  'chevron-right': <path d="m9 5 7 7-7 7" />,
  'chevron-left': <path d="m15 5-7 7 7 7" />,
  'chevron-down': <path d="m5 9 7 7 7-7" />,
  'chevron-up': <path d="m5 15 7-7 7 7" />,
  'arrow-left': <path d="M20 12H4.500M11 5.500 4.500 12l6.500 6.500" />,
  'arrow-right': <path d="M4 12h15.500M13 5.500l6.500 6.500-6.500 6.500" />,
  'arrow-up': <path d="M12 20V4.500M5.500 11 12 4.500l6.500 6.500" />,
  'arrow-down': <path d="M12 4v15.500M5.500 13l6.500 6.500 6.500-6.500" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  search: <><circle cx="11" cy="11" r="6.500" /><path d="m16 16 4.500 4.500" /></>,
  filter: <path d="M3.500 5.500h17l-6.500 7.800v5.200l-4 2v-7.200L3.500 5.500Z" />,
  download: <path d="M12 3.500v12M7 11l5 5 5-5M4.500 20.500h15" />,
  send: <path d="M20.500 3.500 10.500 13.500M20.500 3.500l-6.300 17-3.700-7-7-3.700 17-6.300Z" />,
  refresh: <path d="M20 12a8 8 0 0 1-14.300 4.900M4 12a8 8 0 0 1 14.300-4.900M18.500 3v4.300h-4.300M5.500 21v-4.300h4.300" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v4.500a1.500 1.500 0 0 1-1.500 1.500h-11A1.500 1.500 0 0 1 4 18.500v-11A1.500 1.500 0 0 1 5.500 6H10" />,
  copy: <><rect x="8.500" y="8.500" width="11.500" height="11.500" rx="2.500" /><path d="M15.500 5.500v-.5A1.500 1.500 0 0 0 14 3.500H5.500A1.500 1.500 0 0 0 4 5v8.500A1.500 1.500 0 0 0 5.500 15H6" /></>,
  edit: <path d="M4 20h4L19.500 8.500a2.100 2.100 0 0 0-3-3L5 17l-1 3ZM14.500 7.500l3 3" />,
  trash: <path d="M4 6.500h16M9.500 6.500V4.800a1.300 1.300 0 0 1 1.300-1.300h2.400a1.300 1.300 0 0 1 1.300 1.300v1.700M6.500 6.500l.8 12.100a2 2 0 0 0 2 1.900h5.400a2 2 0 0 0 2-1.900l.8-12.100M10 10.500v6M14 10.500v6" />,
  backspace: <path d="M9 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6.500-7L9 5ZM12 9.500l5 5M17 9.500l-5 5" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  more: <><circle cx="5.500" cy="12" r=".6" /><circle cx="12" cy="12" r=".6" /><circle cx="18.500" cy="12" r=".6" /></>,
  command: <path d="M9 9V6.500A2.500 2.500 0 1 0 6.500 9H9Zm0 0h6m-6 0v6m6-6V6.500A2.500 2.500 0 1 1 17.500 9H15Zm0 0v6m0 0H9m6 0v2.500a2.500 2.500 0 1 0 2.500-2.500H15Zm-6 0v2.500A2.500 2.500 0 1 1 6.500 15H9Z" />,

  // ---- theme
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2.500v2M12 19.500v2M2.500 12h2M19.500 12h2M5.300 5.300l1.400 1.400M17.300 17.300l1.400 1.400M5.300 18.700l1.400-1.400M17.300 6.700l1.400-1.400" /></>,
  moon: <path d="M20 14.200A8.200 8.200 0 0 1 9.800 4a.5.5 0 0 0-.7-.5 8.800 8.800 0 1 0 11.400 11.400.5.5 0 0 0-.5-.7Z" />,

  // ---- places / navigation
  map: <path d="M3.500 6.800 9 4.500l6 2.500 5.500-2.300v12.500L15 19.500 9 17l-5.500 2.300V6.800ZM9 4.500V17M15 7v12.500" />,
  pin: <><path d="M12 21.500s6.500-6 6.500-11.300a6.500 6.500 0 0 0-13 0c0 5.300 6.500 11.300 6.500 11.300Z" /><circle cx="12" cy="10.200" r="2.300" /></>,
  home: <path d="M4 10.500 12 4l8 6.500V19a1.500 1.500 0 0 1-1.500 1.500H14.500v-6h-5v6H5.500A1.500 1.500 0 0 1 4 19v-8.500Z" />,
  inbox: <path d="M3.500 13.500 6.300 5.600A1.700 1.700 0 0 1 7.900 4.500h8.200a1.700 1.700 0 0 1 1.600 1.100l2.800 7.900v4.300a1.700 1.700 0 0 1-1.700 1.700H5.200a1.700 1.700 0 0 1-1.700-1.700v-4.300Zm0 0H8.500l1.200 2.500h4.600l1.200-2.500h5" />,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.600 2.500 3.900 5.500 3.900 9s-1.300 6.500-3.900 9c-2.600-2.500-3.900-5.500-3.900-9S9.400 5.500 12 3Z" /></>,
  building: <path d="M5 20.500V5a1.500 1.500 0 0 1 1.500-1.500h7A1.500 1.500 0 0 1 15 5v15.500M15 9.500h3A1.500 1.500 0 0 1 19.500 11v9.500M3 20.500h18M8.500 7.500h3M8.500 11h3M8.500 14.500h3M9 20.500v-2.800h2v2.800" />,
  layers: <path d="m12 3.500 9 4.700-9 4.700-9-4.700 9-4.700ZM3.800 12.300 12 16.600l8.200-4.300M3.800 16l8.200 4.300 8.200-4.300" />,
  grid: <><rect x="4" y="4" width="6.500" height="6.500" rx="1.500" /><rect x="13.500" y="4" width="6.500" height="6.500" rx="1.500" /><rect x="4" y="13.500" width="6.500" height="6.500" rx="1.500" /><rect x="13.500" y="13.500" width="6.500" height="6.500" rx="1.500" /></>,
  list: <path d="M8.500 6.500H20M8.500 12H20M8.500 17.500H20M4.200 6.500h.1M4.200 12h.1M4.200 17.500h.1" />,
  file: <path d="M13.500 3.500H7A1.500 1.500 0 0 0 5.500 5v14A1.500 1.500 0 0 0 7 20.500h10a1.500 1.500 0 0 0 1.500-1.500V8.500l-5-5Zm0 0v5h5M9 13h6M9 16.500h6" />,

  // ---- people
  user: <><circle cx="12" cy="8" r="4" /><path d="M4.500 20.500a7.500 7.500 0 0 1 15 0" /></>,
  users: <><circle cx="9" cy="8.500" r="3.500" /><path d="M2.500 20a6.500 6.500 0 0 1 13 0M15.500 5.300a3.500 3.500 0 0 1 0 6.400M18 14.300a6.500 6.500 0 0 1 3.500 5.700" /></>,
  nurse: <><path d="M7.500 9.500V6.200L12 3.500l4.500 2.700v3.300" /><path d="M12 5.600v2.400M10.800 6.800h2.400" /><path d="M7.500 9.500a4.500 4.500 0 0 0 9 0Z" /><path d="M4.500 20.500a7.500 7.500 0 0 1 15 0" /></>,
  baby: <><circle cx="12" cy="12" r="8.500" /><path d="M9.300 11h.1M14.600 11h.1M9.300 14.800a3.600 3.600 0 0 0 5.400 0M12 3.500c-1.600.600-2 2.400-.5 3.500" /></>,
  stethoscope: <><path d="M6 3.500v6a4.500 4.500 0 0 0 9 0v-6M4.500 3.500h3M13.500 3.500h3M10.500 14v2.300a4.200 4.200 0 0 0 8.400 0V14.500" /><circle cx="18.900" cy="12.300" r="2.200" /></>,

  // ---- health
  'heart-pulse': <path d="M3.300 11.500C2 7.500 4.300 4.500 7.500 4.500c1.900 0 3.500 1 4.500 2.700 1-1.700 2.600-2.700 4.500-2.700 3.200 0 5.500 3 4.200 7M2.500 14h5l1.800-3.500 3 6.500 2.200-4.500 1 1.500h6M7.500 17.500 12 21l4.500-3.500" />,
  heart: <path d="M12 20.500S3.500 15.600 3.500 9.500a4.700 4.700 0 0 1 8.500-2.800 4.700 4.700 0 0 1 8.500 2.800c0 6.100-8.500 11-8.500 11Z" />,
  activity: <path d="M2.500 12h4.200l2.800-7.500 5 15 2.800-7.500h4.200" />,
  'trending-up': <path d="m3 17 6-6 4 4 8-8.500M15 6.500h6v6" />,
  'trending-down': <path d="m3 7 6 6 4-4 8 8.500M15 17.500h6v-6" />,
  drop: <path d="M12 3s6.500 7 6.500 11.500a6.500 6.500 0 0 1-13 0C5.500 10 12 3 12 3Z" />,
  droplet: <><path d="M12 3s6.500 7 6.500 11.500a6.500 6.500 0 0 1-13 0C5.500 10 12 3 12 3Z" /><path d="M9 14.500a3 3 0 0 0 3 3" /></>,
  thermometer: <><path d="M14 13.800V5a2 2 0 0 0-4 0v8.800a4 4 0 1 0 4 0Z" /><path d="M12 9v8.200" /></>,
  scale: <><rect x="4" y="4" width="16" height="16" rx="4" /><path d="M8 9.500a5.700 5.700 0 0 1 8 0M12 12.500l1.800-3" /></>,
  head: <path d="M9.500 20.500v-3.300A6.800 6.800 0 1 1 18.800 11l1.400 2.600-1.700.9v1.700a1.800 1.800 0 0 1-1.800 1.800H15v2.500M11.500 6.500l-1.500 2.500h3l-1.500 2.500" />,
  zap: <path d="M13 2.500 4.500 13.500H11l-1 8L18.500 10.500H12l1-8Z" />,
  eye: <><path d="M2.500 12S6 5.500 12 5.500 21.500 12 21.500 12 18 18.500 12 18.500 2.500 12 2.500 12Z" /><circle cx="12" cy="12" r="2.800" /></>,
  'eye-off': <path d="M4 4l16 16M10 5.800c.600-.200 1.300-.300 2-.300 6 0 9.500 6.500 9.500 6.500a17 17 0 0 1-2.700 3.500M6.300 7.400A16.500 16.500 0 0 0 2.500 12S6 18.500 12 18.500c1.600 0 3-.4 4.200-1.100M9.900 10.100a2.800 2.800 0 0 0 4 4" />,

  // ---- security / account
  shield: <path d="M12 3 4.500 5.800v5.700c0 4.600 3 8 7.500 9.500 4.500-1.500 7.500-4.900 7.500-9.500V5.800L12 3Zm-3 9 2.200 2.200L15.200 10" />,
  lock: <><rect x="4.500" y="10.500" width="15" height="10" rx="2.500" /><path d="M8 10.500V7.500a4 4 0 0 1 8 0v3M12 14.500v2" /></>,
  key: <><circle cx="8" cy="15.500" r="4.500" /><path d="m11.300 12.300 8.200-8.300M16 7.500l2.500 2.500M13.500 10l2 2" /></>,
  fingerprint: <path d="M5 9.500a7.500 7.500 0 0 1 14 0M3.800 14a8.500 8.500 0 0 1 .7-2M19.500 12.500c.400 2.500.200 5-.6 7M8.500 20.500c-.8-2.300-1-4.800-.5-7.800a4 4 0 0 1 8 .3c.200 2.800 0 5.300-.8 7.500M12 12.500c.300 3 0 5.800-1 8.500M5 17.500c.2-1.500.200-3 .2-4.500" />,
  mail: <><rect x="3" y="5" width="18" height="14" rx="3" /><path d="m4 7.500 8 6 8-6" /></>,
  phone: <path d="M5.500 3.500h3l1.500 4.300-2 1.500a12 12 0 0 0 6.700 6.700l1.500-2 4.300 1.500v3a2 2 0 0 1-2.200 2A17.500 17.500 0 0 1 3.500 5.700a2 2 0 0 1 2-2.200Z" />,
  google: <path d="M20.300 12.200H12.200M20.300 12.200a8.300 8.300 0 1 1-2.500-6" />,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2.800l1.600 2.500 2.900-.5.9 2.800 2.700 1.200-.8 2.900 1.900 2.200-1.900 2.200.8 2.900-2.700 1.200-.9 2.800-2.900-.5L12 21.200l-1.600-2.500-2.900.5-.9-2.800-2.700-1.200.8-2.900L2.800 12l1.900-2.200-.8-2.900 2.700-1.200.9-2.800 2.900.5L12 2.800Z" transform="translate(1.200 1.200) scale(.9)" /></>,
  logout: <path d="M14.500 4H7A2 2 0 0 0 5 6v12a2 2 0 0 0 2 2h7.500M10.500 12H21M17.500 8.500 21 12l-3.500 3.500" />,
  bell: <path d="M6 16.500V11a6 6 0 0 1 12 0v5.500l1.500 2h-15l1.500-2ZM10 20.500a2.200 2.200 0 0 0 4 0" />,

  // ---- time / data
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5.300l3.300 2" /></>,
  calendar: <><rect x="3.500" y="5" width="17" height="15.500" rx="3" /><path d="M3.500 10h17M8 3v4M16 3v4" /></>,
  chart: <path d="M4 20.500h16M7 17v-5M12 17V6.500M17 17v-8" />,
  sparkles: <path d="M10 4.500 11.600 9a2 2 0 0 0 1.300 1.300l4.600 1.700-4.600 1.600a2 2 0 0 0-1.300 1.300L10 19.500l-1.600-4.600a2 2 0 0 0-1.300-1.300L2.500 12l4.600-1.700A2 2 0 0 0 8.400 9L10 4.500ZM18.500 3v4M16.500 5h4M19 16.500v3M17.500 18h3" />,
  alert: <><path d="M12 3.500 2.800 19.500h18.400L12 3.500Z" /><path d="M12 10v4.500M12 17.200h.01" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5.500M12 7.700h.01" /></>,
  star: <path d="m12 3.500 2.600 5.400 5.900.8-4.300 4.100 1.100 5.900L12 16.900l-5.300 2.800 1.100-5.900-4.300-4.100 5.900-.8L12 3.500Z" />,
  'wifi-off': <path d="M3 3l18 18M2.500 9.300A14.500 14.500 0 0 1 7 6.500M11 5.300a14.500 14.500 0 0 1 10.500 4M6 13a9 9 0 0 1 4-2.300M14.500 11a9 9 0 0 1 3.500 2M9.300 16.500a4.500 4.500 0 0 1 5.400 0M12 20h.01" />,
}

export type IconName =
  | 'mic' | 'stop' | 'play' | 'pause' | 'check' | 'check-circle' | 'x' | 'x-circle'
  | 'chevron-right' | 'chevron-left' | 'chevron-down' | 'chevron-up'
  | 'arrow-left' | 'arrow-right' | 'arrow-up' | 'arrow-down' | 'plus' | 'minus' | 'search' | 'filter'
  | 'download' | 'send' | 'refresh' | 'external' | 'copy' | 'edit' | 'trash' | 'backspace' | 'menu' | 'more' | 'command'
  | 'sun' | 'moon' | 'map' | 'pin' | 'home' | 'inbox' | 'globe' | 'building' | 'layers' | 'grid' | 'list' | 'file'
  | 'user' | 'users' | 'nurse' | 'baby' | 'stethoscope'
  | 'heart-pulse' | 'heart' | 'activity' | 'trending-up' | 'trending-down' | 'drop' | 'droplet' | 'thermometer' | 'scale'
  | 'head' | 'zap' | 'eye' | 'eye-off'
  | 'shield' | 'lock' | 'key' | 'fingerprint' | 'mail' | 'phone' | 'google' | 'settings' | 'logout' | 'bell'
  | 'clock' | 'calendar' | 'chart' | 'sparkles' | 'alert' | 'info' | 'star' | 'wifi-off'

export const ICON_NAMES = Object.keys(P) as IconName[]

export function Icon({ name, size = 20, stroke = 1.75, className, style, title }: {
  name: IconName; size?: number; stroke?: number; className?: string; style?: CSSProperties; title?: string
}) {
  return (
    <svg
      className={className ? `icon ${className}` : 'icon'} style={style}
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round"
      role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true} focusable="false"
    >
      {title ? <title>{title}</title> : null}
      {P[name]}
    </svg>
  )
}
