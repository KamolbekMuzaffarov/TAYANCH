// Facility type codes → Uzbek words (shared by the admin panel and the facility page).
const TYPES: Record<string, string> = {
  polyclinic: 'Oilaviy poliklinika', maternity: 'Tugʻruq kompleksi', ovabmu: 'OvaBMU markazi',
  perinatal_center: 'Perinatal markaz', emergency: 'Tez tibbiy yordam', lab: 'Laboratoriya',
}
export const facilityType = (code: string): string => TYPES[code] ?? 'Tibbiyot muassasasi'
export const FACILITY_TYPES = Object.keys(TYPES)
/** "ism@pochta.uz" or "+998 90 123 45 67" — just enough of a check to keep the button honest; the server decides. */
export const looksLikeContact = (v: string): boolean => {
  const t = v.trim()
  return t.includes('@') ? /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(t) : /^(998)?\d{9}$/.test(t.replace(/[\s\-().+]/g, ''))
}
