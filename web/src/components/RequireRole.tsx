import { useEffect, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { api, type AuthState } from '@/lib/api'

/**
 * 路由级角色守卫：当前用户 role 不在允许列表时跳转 /403 或回首页。
 *
 * 用法：
 *   <Route path="reviews" element={
 *     <RequireRole roles={['admin']}><ReviewWorkbench /></RequireRole>
 *   } />
 *
 * auth_enabled=false（演示模式）后端把 role 兜底为 admin，所有路由放行。
 */
export function RequireRole({
  roles,
  children,
}: {
  roles: string[]
  children: ReactNode
}) {
  const [state, setState] = useState<AuthState | null>(null)
  const [failed, setFailed] = useState(false)
  const location = useLocation()

  useEffect(() => {
    let alive = true
    api
      .getAuthState()
      .then((s) => {
        if (!alive) return
        setState(s)
      })
      .catch(() => alive && setFailed(true))
    return () => {
      alive = false
    }
  }, [])

  if (failed) {
    return (
      <div className="min-h-screen flex items-center justify-center text-muted-foreground">
        无法连接服务器
      </div>
    )
  }

  if (!state) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  // 演示模式直接放行（后端会把 role 兜底为 admin，命中绝大多数 roles 集合）
  if (!state.auth_enabled) {
    return <>{children}</>
  }

  // 未登录交回 RequireAuth 处理（这里展示 403 兜底）
  if (!state.logged_in) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  const role = state.role || 'user'
  if (!roles.includes(role)) {
    // 直接 redirect 到首页，携带 from + role 信息便于首页 toast 提示
    return (
      <Navigate
        to="/"
        replace
        state={{
          denied_from: location.pathname,
          denied_role: role,
          denied_required: roles,
        }}
      />
    )
  }

  return <>{children}</>
}
