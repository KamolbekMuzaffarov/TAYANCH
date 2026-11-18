import { useEffect, useState, type ReactElement } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { getSession, homeFor, roleOf, type Role } from './lib/api'
import { CommandPalette } from './ui/CommandPalette'
import NurseScreen from './screens/nurse/NurseScreen'
import InboxScreen from './screens/inbox/InboxScreen'
import MapScreen from './screens/map/MapScreen'
import PowerScreen from './screens/PowerScreen'
import LandingScreen from './screens/landing/LandingScreen'
import AuthScreen from './screens/auth/AuthScreen'
import PendingScreen from './screens/auth/PendingScreen'
import AccountScreen from './screens/account/AccountScreen'
import AdminScreen from './screens/admin/AdminScreen'
import FacilityScreen from './screens/facility/FacilityScreen'

const ACTIVE: Role[] = ['admin', 'leader', 'specialist', 'nurse']

/** Role gate. No session → sign-in; a role without access → its own home (pending → the waiting page). */
function Guard({ allow, children }: { allow: Role[]; children: ReactElement }) {
  const s = getSession()
  if (!s) return <Navigate to="/kirish" replace />
  const role = roleOf(s.user)
  if (!role || !allow.includes(role)) return <Navigate to={homeFor(role)} replace />
  return children
}

export default function App() {
  const [palette, setPalette] = useState(false)
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        const role = roleOf(getSession()?.user)
        if (role && role !== 'pending') setPalette((v) => !v)
      }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])
  return (
    <>
      <Routes>
        <Route path="/" element={<LandingScreen />} />
        <Route path="/kirish" element={<AuthScreen />} />
        <Route path="/kutish" element={<PendingScreen />} />
        <Route path="/hisob" element={<Guard allow={[...ACTIVE, 'facility_admin', 'pending']}><AccountScreen /></Guard>} />
        <Route path="/muassasa" element={<Guard allow={['facility_admin']}><FacilityScreen /></Guard>} />
        <Route path="/admin/*" element={<Guard allow={['admin']}><AdminScreen /></Guard>} />
        <Route path="/hamshira/*" element={<Guard allow={['nurse']}><NurseScreen /></Guard>} />
        <Route path="/mutaxassis/*" element={<Guard allow={['specialist']}><InboxScreen /></Guard>} />
        <Route path="/rahbar/*" element={<Guard allow={['leader', 'admin']}><MapScreen /></Guard>} />
        <Route path="/batafsil" element={<Guard allow={ACTIVE}><PowerScreen /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </>
  )
}
