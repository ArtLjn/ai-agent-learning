import { useState, type FormEvent } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { Loader2, Lock, ShieldCheck, User } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api'

export function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string })?.from || '/'

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (loading) return
    setError(null)
    setLoading(true)
    try {
      await api.login(username.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('用户名或密码错误')
      } else {
        setError(err instanceof Error ? err.message : '登录失败')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-background">
      {/* 氛围光斑（primary/accent 低不透明度，blur 出深空感） */}
      <div
        className="absolute -top-40 -left-40 w-[520px] h-[520px] rounded-full blur-3xl opacity-20"
        style={{
          background:
            'radial-gradient(circle at center, var(--color-primary) 0%, transparent 70%)',
        }}
      />
      <div
        className="absolute -bottom-48 -right-40 w-[620px] h-[620px] rounded-full blur-3xl opacity-15"
        style={{
          background:
            'radial-gradient(circle at center, var(--color-accent) 0%, transparent 70%)',
        }}
      />

      {/* 细网格背景（高级感，几乎不可见） */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            'linear-gradient(to right, #e6edf3 1px, transparent 1px), linear-gradient(to bottom, #e6edf3 1px, transparent 1px)',
          backgroundSize: '40px 40px',
        }}
      />

      {/* 顶部品牌 */}
      <header className="absolute top-8 left-1/2 -translate-x-1/2 flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-lg bg-primary/15 border border-primary/30 flex items-center justify-center">
          <ShieldCheck className="w-4 h-4 text-primary" />
        </div>
        <span className="text-sm font-semibold tracking-wide text-foreground">
          AgentDesk
        </span>
      </header>

      {/* 登录卡片（居中） */}
      <main className="relative min-h-screen flex items-center justify-center px-4">
        <Card className="w-full max-w-sm bg-card border-border shadow-2xl shadow-black/40">
          <CardHeader className="text-center space-y-3 pb-4">
            <div className="mx-auto w-14 h-14 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center">
              <Lock className="w-6 h-6 text-primary" />
            </div>
            <div className="space-y-1">
              <CardTitle className="text-2xl text-foreground">欢迎回来</CardTitle>
              <p className="text-sm text-muted-foreground">
                登录以使用多 Agent 工单处理系统
              </p>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4" noValidate>
              <div className="space-y-1.5">
                <label htmlFor="login-username" className="text-sm font-medium text-foreground">
                  用户名
                </label>
                <div className="relative">
                  <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="login-username"
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="admin"
                    className="pl-9"
                    autoComplete="username"
                    autoFocus
                    required
                    aria-invalid={!!error}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="login-password" className="text-sm font-medium text-foreground">
                  密码
                </label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="login-password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    className="pl-9"
                    autoComplete="current-password"
                    required
                    aria-invalid={!!error}
                  />
                </div>
              </div>

              {error && (
                <p
                  role="alert"
                  className="text-sm text-destructive bg-destructive/10 border border-destructive/20 px-3 py-2 rounded-md"
                >
                  {error}
                </p>
              )}

              <Button
                type="submit"
                className="w-full bg-primary text-primary-foreground hover:bg-primary/90"
                disabled={loading || !username || !password}
              >
                {loading && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                {loading ? '登录中...' : '登录'}
              </Button>
            </form>

            <p className="mt-6 text-center text-xs text-muted-foreground/60">
              © 2026 AgentDesk · LangGraph + Multi-Agent
            </p>

            <div className="mt-4 pt-4 border-t border-border text-center">
              <span className="text-xs text-muted-foreground">没有账户？</span>{' '}
              <Link
                to="/register"
                className="text-xs font-medium text-primary hover:underline"
              >
                立即注册
              </Link>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
