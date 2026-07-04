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
import { UserManagement } from '@/pages/admin/UserManagement'
import { AuditLog } from '@/pages/admin/AuditLog'
import SpanTreeView from '@/pages/dev/SpanTreeView'
import TokenDashboard from '@/pages/dev/TokenDashboard'

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
              <RequireRole roles={['admin']}>
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
          <Route
            path="admin/users"
            element={
              <RequireRole roles={['admin']}>
                <UserManagement />
              </RequireRole>
            }
          />
          <Route
            path="admin/audit-logs"
            element={
              <RequireRole roles={['admin']}>
                <AuditLog />
              </RequireRole>
            }
          />

          {/* D-01 / D-04 开发人员工作台 */}
          <Route
            path="dev/traces"
            element={
              <RequireRole roles={['developer', 'admin']}>
                <SpanTreeView />
              </RequireRole>
            }
          />
          <Route
            path="dev/tokens"
            element={
              <RequireRole roles={['developer', 'admin']}>
                <TokenDashboard />
              </RequireRole>
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
