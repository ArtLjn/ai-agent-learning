import { useState, type FormEvent } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { Loader2, Lock, ShieldCheck, User, UserCircle, ArrowLeft } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api'

const USERNAME_RE = /^[a-zA-Z0-9_]{3,32}$/

export function Register() {
  const navigate = useNavigate()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [nickname, setNickname] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function validate(): string | null {
    if (!USERNAME_RE.test(username.trim())) {
      return '用户名需为 3-32 位字母、数字或下划线'
    }
    if (password.length < 8) {
      return '密码至少 8 位'
    }
    if (password !== confirmPassword) {
      return '两次输入的密码不一致'
    }
    return null
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (loading) return
    setError(null)

    const invalid = validate()
    if (invalid) {
      setError(invalid)
      return
    }

    setLoading(true)
    try {
      await api.register({
        username: username.trim(),
        password,
        nickname: nickname.trim() || undefined,
      })
      // 注册即登录，直接进入工单列表
      navigate('/tickets', { replace: true })
    } catch (err) {
      if (err instanceof ApiError) {
        const code = (err.body as { error?: string } | undefined)?.error
        if (err.status === 409 || code === 'username_taken') {
          setError('该用户名已被注册')
        } else if (code === 'invalid_username') {
          setError('用户名格式不合法')
        } else if (code === 'password_too_weak') {
          setError('密码强度不足（至少 8 位）')
        } else {
          setError(err.detail || `注册失败（${err.status}）`)
        }
      } else {
        setError(err instanceof Error ? err.message : '注册失败')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-background">
      {/* 氛围光斑 */}
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

      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            'linear-gradient(to right, #e6edf3 1px, transparent 1px), linear-gradient(to bottom, #e6edf3 1px, transparent 1px)',
          backgroundSize: '40px 40px',
        }}
      />

      <header className="absolute top-8 left-1/2 -translate-x-1/2 flex items-center gap-2.5">
        <div className="w-8 h-8 rounded-lg bg-primary/15 border border-primary/30 flex items-center justify-center">
          <ShieldCheck className="w-4 h-4 text-primary" />
        </div>
        <span className="text-sm font-semibold tracking-wide text-foreground">
          AgentDesk
        </span>
      </header>

      <main className="relative min-h-screen flex items-center justify-center px-4">
        <Card className="w-full max-w-md bg-card border-border shadow-2xl shadow-black/40">
          <CardHeader className="text-center space-y-3 pb-4">
            <div className="mx-auto w-14 h-14 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center">
              <UserCircle className="w-6 h-6 text-primary" />
            </div>
            <div className="space-y-1">
              <CardTitle className="text-2xl text-foreground">创建账户</CardTitle>
              <p className="text-sm text-muted-foreground">
                注册后即可使用多 Agent 工单处理系统
              </p>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4" noValidate>
              <div className="space-y-1.5">
                <label htmlFor="reg-username" className="text-sm font-medium text-foreground">
                  用户名
                </label>
                <div className="relative">
                  <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="reg-username"
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="3-32 位字母、数字、下划线"
                    className="pl-9"
                    autoComplete="username"
                    autoFocus
                    required
                    aria-invalid={!!error}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="reg-nickname" className="text-sm font-medium text-foreground">
                  昵称（可选）
                </label>
                <div className="relative">
                  <UserCircle className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="reg-nickname"
                    type="text"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    placeholder="留空则使用用户名"
                    className="pl-9"
                    autoComplete="nickname"
                    maxLength={32}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="reg-password" className="text-sm font-medium text-foreground">
                  密码
                </label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="reg-password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="至少 8 位"
                    className="pl-9"
                    autoComplete="new-password"
                    required
                    aria-invalid={!!error}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="reg-confirm-password" className="text-sm font-medium text-foreground">
                  确认密码
                </label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="reg-confirm-password"
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="再次输入密码"
                    className="pl-9"
                    autoComplete="new-password"
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
                disabled={loading || !username || !password || !confirmPassword}
              >
                {loading && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
                {loading ? '注册中...' : '注册并登录'}
              </Button>
            </form>

            <div className="mt-6 flex items-center justify-between">
              <Link
                to="/login"
                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                <ArrowLeft className="w-3.5 h-3.5" />
                返回登录
              </Link>
              <p className="text-center text-xs text-muted-foreground/60">
                © 2026 AgentDesk · LangGraph + Multi-Agent
              </p>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
