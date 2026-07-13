import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { api, type AuthState } from '@/lib/api'

function homePathForRole(role: string | null) {
  if (role === 'admin') return '/dashboard'
  if (role === 'developer') return '/dev/traces'
  return '/my'
}

export function RoleHome() {
  const [state, setState] = useState<AuthState | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    api
      .getAuthState()
      .then((nextState) => {
        if (alive) setState(nextState)
      })
      .catch(() => {
        if (alive) setFailed(true)
      })
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

  return <Navigate to={homePathForRole(state.role)} replace />
}
