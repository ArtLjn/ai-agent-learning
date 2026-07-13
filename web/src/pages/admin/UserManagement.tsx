import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Loader2, Search, ShieldCheck, Users2, AlertCircle } from 'lucide-react'
import { api, ApiError } from '@/lib/api'

interface AdminUserItem {
  user_id: string
  username: string | null
  nickname: string | null
  contact: string | null
  vip_level: number
  preferred_categories: string[]
  created_at: string | null
  status: string
  role: string
}

interface AdminUserListResponse {
  items: AdminUserItem[]
  total: number
  page: number
  page_size: number
}

type RoleOption = 'user' | 'admin' | 'developer'
type StatusOption = 'active' | 'banned'

const ROLE_LABEL: Record<RoleOption, string> = {
  user: '企业员工',
  admin: '服务台人员',
  developer: '系统运维',
}

const STATUS_LABEL: Record<StatusOption, string> = {
  active: '正常',
  banned: '封禁',
}

const ROLE_BADGE_CLASS: Record<RoleOption, string> = {
  user: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
  admin: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  developer: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
}

const PAGE_SIZE = 20

export function UserManagement() {
  const [data, setData] = useState<AdminUserListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [keyword, setKeyword] = useState('')
  const [roleFilter, setRoleFilter] = useState<RoleOption | 'all'>('all')
  const [statusFilter, setStatusFilter] = useState<StatusOption | 'all'>('all')
  const [appliedKeyword, setAppliedKeyword] = useState('')

  // 编辑对话框
  const [editTarget, setEditTarget] = useState<AdminUserItem | null>(null)
  const [editRole, setEditRole] = useState<RoleOption>('user')
  const [editStatus, setEditStatus] = useState<StatusOption>('active')
  const [editSubmitting, setEditSubmitting] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  async function loadUsers() {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        page: String(page),
        page_size: String(PAGE_SIZE),
      }
      if (appliedKeyword) params.keyword = appliedKeyword
      if (roleFilter !== 'all') params.role = roleFilter
      if (statusFilter !== 'all') params.status = statusFilter
      const qs = new URLSearchParams(params).toString()
      const resp = await api.request<AdminUserListResponse>(`/admin/users?${qs}`)
      setData(resp)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    queueMicrotask(() => {
      void loadUsers()
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, appliedKeyword, roleFilter, statusFilter])

  function onSearch() {
    setPage(1)
    setAppliedKeyword(keyword.trim())
  }

  function openEdit(user: AdminUserItem) {
    setEditTarget(user)
    setEditRole((user.role as RoleOption) || 'user')
    setEditStatus((user.status as StatusOption) || 'active')
    setEditError(null)
  }

  async function submitEdit() {
    if (!editTarget) return
    setEditSubmitting(true)
    setEditError(null)
    try {
      const patch: { role?: RoleOption; status?: StatusOption } = {}
      if (editRole !== editTarget.role) patch.role = editRole
      if (editStatus !== editTarget.status) patch.status = editStatus
      if (Object.keys(patch).length === 0) {
        setEditTarget(null)
        return
      }
      await api.request<AdminUserItem>(`/admin/users/${editTarget.user_id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      })
      setEditTarget(null)
      void loadUsers()
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? (e.body as { detail?: string; error?: string } | null)?.detail ||
            (e.body as { error?: string } | null)?.error ||
            e.message
          : e instanceof Error
            ? e.message
            : '提交失败'
      setEditError(msg)
    } finally {
      setEditSubmitting(false)
    }
  }

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="rounded-lg bg-primary/10 p-2">
          <Users2 className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h1 className="text-xl font-semibold tracking-tight">账号治理</h1>
          <p className="text-sm text-muted-foreground">
            维护企业员工、服务台人员和系统运维人员的角色与账号状态
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-base">
            <span>用户列表（共 {total} 人）</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void loadUsers()}
              disabled={loading}
            >
              {loading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Search className="mr-2 h-4 w-4" />
              )}
              刷新
            </Button>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 筛选栏 */}
          <div className="flex flex-wrap items-center gap-2">
            <Input
              placeholder="搜索 username / nickname"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSearch()
              }}
              className="w-64"
            />
            <Button variant="secondary" size="sm" onClick={onSearch}>
              <Search className="mr-1 h-4 w-4" /> 搜索
            </Button>
            <div className="flex items-center gap-2 ml-auto">
              <span className="text-xs text-muted-foreground">角色</span>
              <Select
                value={roleFilter}
                onValueChange={(v) => {
                  setPage(1)
                  setRoleFilter(v as RoleOption | 'all')
                }}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部</SelectItem>
                  <SelectItem value="user">企业员工</SelectItem>
                  <SelectItem value="admin">服务台人员</SelectItem>
                  <SelectItem value="developer">系统运维</SelectItem>
                </SelectContent>
              </Select>
              <span className="text-xs text-muted-foreground ml-2">状态</span>
              <Select
                value={statusFilter}
                onValueChange={(v) => {
                  setPage(1)
                  setStatusFilter(v as StatusOption | 'all')
                }}
              >
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部</SelectItem>
                  <SelectItem value="active">正常</SelectItem>
                  <SelectItem value="banned">封禁</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="h-4 w-4" />
              {error}
            </div>
          )}

          {/* 表格 */}
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-32">Username</TableHead>
                  <TableHead className="w-32">Nickname</TableHead>
                  <TableHead className="w-24">角色</TableHead>
                  <TableHead className="w-20">状态</TableHead>
                  <TableHead className="w-20">VIP</TableHead>
                  <TableHead>偏好分类</TableHead>
                  <TableHead className="w-40">注册时间</TableHead>
                  <TableHead className="w-24 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && !data ? (
                  <TableRow>
                    <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                      <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                    </TableCell>
                  </TableRow>
                ) : data && data.items.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                      暂无符合条件的用户
                    </TableCell>
                  </TableRow>
                ) : (
                  data?.items.map((u) => (
                    <TableRow key={u.user_id}>
                      <TableCell className="font-mono text-sm">{u.username || '-'}</TableCell>
                      <TableCell className="text-sm">{u.nickname || '-'}</TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={ROLE_BADGE_CLASS[(u.role as RoleOption) || 'user']}
                        >
                          {ROLE_LABEL[(u.role as RoleOption) || 'user']}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={
                            u.status === 'banned'
                              ? 'bg-red-500/15 text-red-300 border-red-500/30'
                              : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
                          }
                        >
                          {STATUS_LABEL[(u.status as StatusOption) || 'active']}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm">V{u.vip_level ?? 0}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {u.preferred_categories?.length
                          ? u.preferred_categories.join(' / ')
                          : '-'}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {u.created_at ? u.created_at.slice(0, 16).replace('T', ' ') : '-'}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openEdit(u)}
                          disabled={u.user_id === currentUserId()}
                          title={
                            u.user_id === currentUserId() ? '不能修改自己' : '修改角色 / 状态'
                          }
                        >
                          <ShieldCheck className="mr-1 h-4 w-4" />
                          管理
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>

          {/* 分页 */}
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted-foreground">
              第 {page} / {totalPages} 页 · 共 {total} 条
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || loading}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages || loading}
              >
                下一页
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 编辑对话框 */}
      <Dialog open={editTarget !== null} onOpenChange={(o) => !o && setEditTarget(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>管理用户</DialogTitle>
            <DialogDescription>
              调整 {editTarget?.username} 的角色与状态。修改自己会被拒绝（防止权限误操作）。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <label className="text-sm font-medium">角色</label>
              <Select value={editRole} onValueChange={(v) => setEditRole(v as RoleOption)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">企业员工（user）</SelectItem>
                  <SelectItem value="admin">服务台人员（admin）</SelectItem>
                  <SelectItem value="developer">系统运维（developer）</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                <strong>企业员工</strong>：提交工单、补充材料、查看进度。<br />
                <strong>服务台人员</strong>：人工审核兜底、知识维护、运营分析。<br />
                <strong>系统运维</strong>：账号治理、流程监控、策略调试、系统健康。
              </p>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">状态</label>
              <Select value={editStatus} onValueChange={(v) => setEditStatus(v as StatusOption)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">正常（active）</SelectItem>
                  <SelectItem value="banned">封禁（banned）</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {editError && (
              <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                {editError}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditTarget(null)} disabled={editSubmitting}>
              取消
            </Button>
            <Button onClick={() => void submitEdit()} disabled={editSubmitting}>
              {editSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              确认修改
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

// 当前登录用户的 user_id（用于禁用「修改自己」按钮）
// session 里没 user_id（兜底管理员 / 演示模式）时返回空字符串
function currentUserId(): string {
  // 简单方案：从 last getAuthState 结果取，但这里不维护全局状态
  // 实际项目中可走 useAuth hook，这里简化为始终返回空（后端也有自我保护）
  return ''
}
