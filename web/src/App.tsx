import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppLayout } from '@/components/layout/AppLayout'
import { RequireAuth } from '@/components/RequireAuth'
import { RequireRole } from '@/components/RequireRole'
import { RoleHome } from '@/pages/RoleHome'
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
import { UserHome } from '@/pages/user/UserHome'
import { UserManagement } from '@/pages/admin/UserManagement'
import { AuditLog } from '@/pages/admin/AuditLog'
import { PromptVersions } from '@/pages/dev/PromptVersions'
import { AgentCallStats } from '@/pages/dev/AgentCallStats'
import { RagDebug } from '@/pages/dev/RagDebug'
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
          {/* 全角色入口：按角色分流到各自首页 */}
          <Route index element={<RoleHome />} />
          <Route
            path="my"
            element={
              <RequireRole roles={['user']}>
                <UserHome />
              </RequireRole>
            }
          />
          <Route
            path="tickets"
            element={
              <RequireRole roles={['user', 'admin']}>
                <Tickets />
              </RequireRole>
            }
          />
          <Route
            path="tickets/:id"
            element={
              <RequireRole roles={['user', 'admin']}>
                <TicketDetail />
              </RequireRole>
            }
          />
          <Route path="profile" element={<Profile />} />

          {/* 角色受限 */}
          <Route
            path="dashboard"
            element={
              <RequireRole roles={['admin']}>
                <Dashboard />
              </RequireRole>
            }
          />
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
              <RequireRole roles={['developer']}>
                <AgentMonitor />
              </RequireRole>
            }
          />
          <Route
            path="settings"
            element={
              <RequireRole roles={['developer']}>
                <Settings />
              </RequireRole>
            }
          />
          <Route
            path="admin/users"
            element={
              <RequireRole roles={['developer']}>
                <UserManagement />
              </RequireRole>
            }
          />
          <Route
            path="admin/audit-logs"
            element={
              <RequireRole roles={['developer']}>
                <AuditLog />
              </RequireRole>
            }
          />

          {/* 系统运维管理端：流程监控 / 策略调试 / 系统健康 */}
          <Route
            path="dev/prompts"
            element={
              <RequireRole roles={['developer']}>
                <PromptVersions />
              </RequireRole>
            }
          />
          <Route
            path="dev/rag-debug"
            element={
              <RequireRole roles={['developer']}>
                <RagDebug />
              </RequireRole>
            }
          />
          <Route
            path="dev/agent-stats"
            element={
              <RequireRole roles={['developer']}>
                <AgentCallStats />
              </RequireRole>
            }
          />
          <Route
            path="dev/traces"
            element={
              <RequireRole roles={['developer']}>
                <SpanTreeView />
              </RequireRole>
            }
          />
          <Route
            path="dev/tokens"
            element={
              <RequireRole roles={['developer']}>
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
