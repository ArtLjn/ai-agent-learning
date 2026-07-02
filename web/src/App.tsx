import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppLayout } from '@/components/layout/AppLayout'
import { RequireAuth } from '@/components/RequireAuth'
import { RequireRole } from '@/components/RequireRole'
import { Dashboard } from '@/pages/Dashboard'
import { Tickets } from '@/pages/Tickets'
import { TicketDetail } from '@/pages/TicketDetail'
import { AgentMonitor } from '@/pages/AgentMonitor'
import { Knowledge } from '@/pages/Knowledge'
import { Settings } from '@/pages/Settings'
import { ReviewWorkbench } from '@/pages/ReviewWorkbench'
import { Login } from '@/pages/Login'
import { Register } from '@/pages/Register'
import { Profile } from '@/pages/Profile'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          {/* 全角色可见 */}
          <Route index element={<Dashboard />} />
          <Route path="tickets" element={<Tickets />} />
          <Route path="tickets/:id" element={<TicketDetail />} />
          <Route path="profile" element={<Profile />} />

          {/* 角色受限 */}
          <Route
            path="reviews"
            element={
              <RequireRole roles={['reviewer', 'admin']}>
                <ReviewWorkbench />
              </RequireRole>
            }
          />
          <Route
            path="knowledge"
            element={
              <RequireRole roles={['admin']}>
                <Knowledge />
              </RequireRole>
            }
          />
          <Route
            path="monitor"
            element={
              <RequireRole roles={['developer', 'admin']}>
                <AgentMonitor />
              </RequireRole>
            }
          />
          <Route
            path="settings"
            element={
              <RequireRole roles={['admin']}>
                <Settings />
              </RequireRole>
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
