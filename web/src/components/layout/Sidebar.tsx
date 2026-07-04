import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  Ticket,
  Activity,
  BookOpen,
  Settings,
  Bot,
  LogOut,
  ShieldCheck,
  UserCircle,
  Users2,
  ScrollText,
  Sparkles,
  BarChart3,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { api, type AuthState } from '@/lib/api'

type Role = 'user' | 'admin' | 'developer'

interface NavItem {
  to: string
  icon: typeof LayoutDashboard
  label: string
  roles: Role[]
}

const navItems: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard', roles: ['user', 'admin', 'developer'] },
  { to: '/tickets', icon: Ticket, label: '工单管理', roles: ['user', 'admin', 'developer'] },
  { to: '/profile', icon: UserCircle, label: '个人资料', roles: ['user', 'admin', 'developer'] },
  { to: '/reviews', icon: ShieldCheck, label: '审核工作台', roles: ['admin'] },
  { to: '/knowledge', icon: BookOpen, label: '知识库', roles: ['admin'] },
  { to: '/admin/users', icon: Users2, label: '用户管理', roles: ['admin'] },
  { to: '/admin/audit-logs', icon: ScrollText, label: '操作日志', roles: ['admin'] },
  { to: '/monitor', icon: Activity, label: 'Agent 监控', roles: ['developer', 'admin'] },
  { to: '/dev/prompts', icon: Sparkles, label: 'Prompt 版本', roles: ['admin'] },
  { to: '/dev/agent-stats', icon: BarChart3, label: 'Agent 统计', roles: ['admin', 'developer'] },
  { to: '/settings', icon: Settings, label: '系统配置', roles: ['admin'] },
]

export function Sidebar() {
  const navigate = useNavigate()
  const [loggingOut, setLoggingOut] = useState(false)
  const [auth, setAuth] = useState<AuthState | null>(null)

  useEffect(() => {
    let alive = true
    api
      .getAuthState()
      .then((s) => {
        if (alive) setAuth(s)
      })
      .catch(() => {
        // 静默失败：Sidebar 仍渲染基础菜单（演示模式视为 admin 兜底）
        if (alive) setAuth(null)
      })
    return () => {
      alive = false
    }
  }, [])

  // 演示模式视为 admin，可见全部菜单
  const role: Role = (auth?.role as Role) || (auth && !auth.auth_enabled ? 'admin' : 'user')
  const visible = auth ? navItems.filter((item) => item.roles.includes(role)) : navItems

  async function handleLogout() {
    if (loggingOut) return
    setLoggingOut(true)
    try {
      await api.logout()
    } finally {
      navigate('/login', { replace: true })
    }
  }

  return (
    <aside className="fixed left-0 top-0 bottom-0 w-56 bg-card border-r border-border flex flex-col z-50">
      {/* Logo */}
      <div className="h-14 flex items-center gap-2 px-4 border-b border-border">
        <Bot className="w-6 h-6 text-primary" />
        <div>
          <h1 className="text-sm font-semibold text-foreground leading-tight">Agent 工单系统</h1>
          <p className="text-[10px] text-muted-foreground">LangGraph + Multi-Agent</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-3 px-2 space-y-0.5">
        {visible.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors',
                isActive
                  ? 'bg-primary/10 text-primary font-medium'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted'
              )
            }
          >
            <item.icon className="w-4 h-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Bottom */}
      <div className="p-3 border-t border-border space-y-2">
        {auth?.logged_in && (
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span className="truncate max-w-[8rem]">{auth.username}</span>
            <span className="px-1.5 py-0.5 rounded text-[10px] bg-muted text-muted-foreground">
              {role}
            </span>
          </div>
        )}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <div className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
          <span>服务在线</span>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          disabled={loggingOut}
          className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
        >
          <LogOut className="w-3.5 h-3.5" />
          {loggingOut ? '退出中...' : '退出登录'}
        </button>
      </div>
    </aside>
  )
}
