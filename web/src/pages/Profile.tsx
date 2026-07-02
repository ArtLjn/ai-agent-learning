import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, Lock, User, UserCircle, Mail, Save, KeyRound, ShieldCheck } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { api, ApiError } from '@/lib/api'
import { toast } from '@/lib/toast'
import type { TicketCategory, UserProfile } from '@/types'

const ALL_CATEGORIES: { value: TicketCategory; label: string }[] = [
  { value: 'technical', label: '技术类' },
  { value: 'billing', label: '账务类' },
  { value: 'complaint', label: '投诉类' },
  { value: 'inquiry', label: '咨询类' },
]

export function Profile() {
  const navigate = useNavigate()

  // ---- 信息管理 ----
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [loadingProfile, setLoadingProfile] = useState(true)
  const [nickname, setNickname] = useState('')
  const [contact, setContact] = useState('')
  const [preferred, setPreferred] = useState<TicketCategory[]>([])
  const [savingProfile, setSavingProfile] = useState(false)

  // ---- 修改密码 ----
  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmNew, setConfirmNew] = useState('')
  const [savingPwd, setSavingPwd] = useState(false)
  const [pwdError, setPwdError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const data = await api.getMe()
        if (cancelled) return
        setProfile(data)
        setNickname(data.nickname ?? '')
        setContact(data.contact ?? '')
        setPreferred(data.preferred_categories ?? [])
      } catch (err) {
        if (!cancelled) {
          toast.error('加载资料失败', err instanceof Error ? err.message : String(err))
        }
      } finally {
        if (!cancelled) setLoadingProfile(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  function toggleCategory(cat: TicketCategory) {
    setPreferred((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat],
    )
  }

  async function handleSaveProfile(e: FormEvent) {
    e.preventDefault()
    if (savingProfile) return
    setSavingProfile(true)
    try {
      const updated = await api.updateMe({
        nickname: nickname.trim() || undefined,
        contact: contact.trim() || undefined,
        preferred_categories: preferred,
      })
      setProfile(updated)
      setNickname(updated.nickname ?? '')
      setContact(updated.contact ?? '')
      setPreferred(updated.preferred_categories ?? [])
      toast.success('已保存', '个人资料更新成功')
    } catch (err) {
      const msg = err instanceof ApiError
        ? (err.body as { error?: string } | undefined)?.error === 'invalid_category'
          ? '包含未知的偏好分类'
          : err.detail || `保存失败（${err.status}）`
        : err instanceof Error
          ? err.message
          : '保存失败'
      toast.error('保存失败', msg)
    } finally {
      setSavingProfile(false)
    }
  }

  async function handleChangePassword(e: FormEvent) {
    e.preventDefault()
    if (savingPwd) return
    setPwdError(null)

    if (newPassword.length < 8) {
      setPwdError('新密码至少 8 位')
      return
    }
    if (newPassword === oldPassword) {
      setPwdError('新密码不能与旧密码相同')
      return
    }
    if (newPassword !== confirmNew) {
      setPwdError('两次输入的新密码不一致')
      return
    }

    setSavingPwd(true)
    try {
      await api.changePassword({
        old_password: oldPassword,
        new_password: newPassword,
      })
      toast.success('密码已修改', '请使用新密码重新登录')
      // 清本地表单
      setOldPassword('')
      setNewPassword('')
      setConfirmNew('')
      // 跳登录页
      navigate('/login', { replace: true })
    } catch (err) {
      if (err instanceof ApiError) {
        const code = (err.body as { error?: string } | undefined)?.error
        if (err.status === 401 || code === 'invalid_credentials') {
          setPwdError('凭证错误，请检查旧密码')
        } else if (code === 'password_same_as_old') {
          setPwdError('新密码不能与旧密码相同')
        } else if (code === 'password_too_weak') {
          setPwdError('新密码强度不足（至少 8 位）')
        } else {
          setPwdError(err.detail || `修改失败（${err.status}）`)
        }
      } else {
        setPwdError(err instanceof Error ? err.message : '修改失败')
      }
    } finally {
      setSavingPwd(false)
    }
  }

  if (loadingProfile) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
        加载中...
      </div>
    )
  }

  const isDemoAdmin = !profile?.user_id

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-foreground">个人资料</h1>
        <p className="text-sm text-muted-foreground mt-1">
          管理你的账户信息与偏好设置
        </p>
      </div>

      {isDemoAdmin && (
        <div className="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning-foreground">
          当前是演示管理员账户（无数据库行），不支持在线修改资料与密码。请注册一个新账户以体验完整流程。
        </div>
      )}

      {/* 卡片 1：基本信息 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <UserCircle className="w-5 h-5 text-primary" />
            基本信息
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSaveProfile} className="space-y-5" noValidate>
            <div className="grid grid-cols-2 gap-4">
              <ReadonlyField
                label="用户 ID"
                value={profile?.user_id ?? '—'}
              />
              <ReadonlyField
                label="用户名"
                value={profile?.username ?? '—'}
              />
              <ReadonlyField
                label="VIP 等级"
                value={profile ? `Lv.${profile.vip_level}` : '—'}
              />
              <ReadonlyField
                label="注册时间"
                value={profile?.created_at ?? '—'}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="profile-nickname" className="text-sm font-medium text-foreground">
                  昵称
                </label>
                <div className="relative">
                  <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="profile-nickname"
                    type="text"
                    value={nickname}
                    onChange={(e) => setNickname(e.target.value)}
                    placeholder="留空则使用用户名"
                    className="pl-9"
                    maxLength={32}
                    disabled={isDemoAdmin}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="profile-contact" className="text-sm font-medium text-foreground">
                  联系方式
                </label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="profile-contact"
                    type="text"
                    value={contact}
                    onChange={(e) => setContact(e.target.value)}
                    placeholder="邮箱或手机号（可选）"
                    className="pl-9"
                    maxLength={128}
                    disabled={isDemoAdmin}
                  />
                </div>
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium text-foreground">偏好分类</label>
              <div className="flex flex-wrap gap-2">
                {ALL_CATEGORIES.map((c) => {
                  const active = preferred.includes(c.value)
                  return (
                    <button
                      key={c.value}
                      type="button"
                      onClick={() => !isDemoAdmin && toggleCategory(c.value)}
                      disabled={isDemoAdmin}
                      className={
                        'px-3 py-1.5 rounded-md text-sm border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ' +
                        (active
                          ? 'bg-primary/10 border-primary/40 text-primary'
                          : 'bg-background border-border text-muted-foreground hover:text-foreground hover:border-foreground/30')
                      }
                      aria-pressed={active}
                    >
                      {c.label}
                    </button>
                  )
                })}
              </div>
              <p className="text-xs text-muted-foreground">
                选中的分类将在工单分类与统计中作为偏好参考
              </p>
            </div>

            <div className="flex justify-end">
              <Button
                type="submit"
                disabled={savingProfile || isDemoAdmin}
                className="bg-primary text-primary-foreground hover:bg-primary/90"
              >
                {savingProfile ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <Save className="w-4 h-4 mr-2" />
                )}
                {savingProfile ? '保存中...' : '保存'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* 卡片 2：修改密码 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <KeyRound className="w-5 h-5 text-primary" />
            修改密码
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleChangePassword} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <label htmlFor="pwd-old" className="text-sm font-medium text-foreground">
                旧密码
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                <Input
                  id="pwd-old"
                  type="password"
                  value={oldPassword}
                  onChange={(e) => setOldPassword(e.target.value)}
                  placeholder="••••••••"
                  className="pl-9"
                  autoComplete="current-password"
                  required
                  disabled={isDemoAdmin}
                  aria-invalid={!!pwdError}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label htmlFor="pwd-new" className="text-sm font-medium text-foreground">
                  新密码
                </label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="pwd-new"
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="至少 8 位"
                    className="pl-9"
                    autoComplete="new-password"
                    required
                    disabled={isDemoAdmin}
                    aria-invalid={!!pwdError}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <label htmlFor="pwd-confirm" className="text-sm font-medium text-foreground">
                  确认新密码
                </label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                  <Input
                    id="pwd-confirm"
                    type="password"
                    value={confirmNew}
                    onChange={(e) => setConfirmNew(e.target.value)}
                    placeholder="再次输入新密码"
                    className="pl-9"
                    autoComplete="new-password"
                    required
                    disabled={isDemoAdmin}
                    aria-invalid={!!pwdError}
                  />
                </div>
              </div>
            </div>

            {pwdError && (
              <p
                role="alert"
                className="text-sm text-destructive bg-destructive/10 border border-destructive/20 px-3 py-2 rounded-md flex items-center gap-2"
              >
                <ShieldCheck className="w-4 h-4 shrink-0" />
                {pwdError}
              </p>
            )}

            <div className="flex justify-between items-center">
              <p className="text-xs text-muted-foreground">
                修改成功后将自动退出当前登录，请使用新密码重新登录
              </p>
              <Button
                type="submit"
                disabled={savingPwd || isDemoAdmin || !oldPassword || !newPassword || !confirmNew}
                className="bg-primary text-primary-foreground hover:bg-primary/90"
              >
                {savingPwd ? (
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                ) : (
                  <KeyRound className="w-4 h-4 mr-2" />
                )}
                {savingPwd ? '提交中...' : '修改密码'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

function ReadonlyField({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1.5">
      <label className="text-sm font-medium text-muted-foreground">{label}</label>
      <div className="h-9 px-3 flex items-center rounded-md border border-border bg-muted/30 text-sm text-foreground/80">
        <span className="truncate">{value}</span>
      </div>
    </div>
  )
}
