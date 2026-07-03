import { Fragment, useEffect, useState } from 'react'
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
import { ChevronDown, ChevronRight, Loader2, Search, ShieldCheck, AlertCircle } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import type { AuditLogEntry, AuditLogListResponse } from '@/types'

const PAGE_SIZE = 50

const TARGET_LABEL: Record<string, string> = {
  user: '用户',
  knowledge: '知识库',
  review: '审核单',
  prompt: 'Prompt',
}

function actionBadgeClass(action: string): string {
  if (action.startsWith('user_')) return 'bg-slate-500/15 text-slate-300 border-slate-500/30'
  if (action.startsWith('knowledge_')) return 'bg-amber-500/15 text-amber-300 border-amber-500/30'
  if (action === 'review_decision') return 'bg-blue-500/15 text-blue-300 border-blue-500/30'
  if (action === 'prompt_activate') return 'bg-violet-500/15 text-violet-300 border-violet-500/30'
  if (action === 'quota_update') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
  return 'bg-muted text-muted-foreground'
}

export function AuditLog() {
  const [data, setData] = useState<AuditLogListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [actionFilter, setActionFilter] = useState<string>('all')
  const [adminIdInput, setAdminIdInput] = useState('')
  const [appliedAdminId, setAppliedAdminId] = useState('')
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())

  async function load() {
    setLoading(true)
    setError(null)
    const params: Record<string, string> = {
      page: String(page),
      page_size: String(PAGE_SIZE),
    }
    if (actionFilter !== 'all') params.action = actionFilter
    if (appliedAdminId.trim()) params.admin_id = appliedAdminId.trim()
    try {
      const resp = await api.getAuditLogs(params)
      setData(resp)
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setError(err.detail || `加载失败 (${err.status})`)
      } else {
        setError('加载审计日志失败')
      }
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, actionFilter, appliedAdminId])

  function toggleExpand(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function handleSearch() {
    setPage(1)
    setAppliedAdminId(adminIdInput)
  }

  function handleReset() {
    setActionFilter('all')
    setAdminIdInput('')
    setAppliedAdminId('')
    setPage(1)
  }

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-primary" />
          操作日志审计
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          管理员写操作的不可篡改历史（{total} 条记录）
        </p>
      </div>

      {/* 筛选栏 */}
      <Card className="bg-card border-border">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">筛选</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">操作类型</label>
              <Select value={actionFilter} onValueChange={(v) => { setActionFilter(v); setPage(1) }}>
                <SelectTrigger className="w-[200px]">
                  <SelectValue placeholder="全部" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部</SelectItem>
                  {data?.actions &&
                    Object.entries(data.actions).map(([value, label]) => (
                      <SelectItem key={value} value={value}>
                        {label}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1 flex-1 min-w-[220px]">
              <label className="text-xs text-muted-foreground">管理员 ID</label>
              <div className="flex gap-2">
                <Input
                  placeholder="U-xxxxxxxx"
                  value={adminIdInput}
                  onChange={(e) => setAdminIdInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleSearch()
                  }}
                />
                <Button variant="secondary" onClick={handleSearch}>
                  <Search className="w-4 h-4 mr-1" />
                  查找
                </Button>
              </div>
            </div>
            <Button variant="ghost" onClick={handleReset}>
              重置
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 表格 */}
      <Card className="bg-card border-border">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center justify-between">
            <span>记录列表</span>
            {loading && <Loader2 className="w-3.5 h-3.5 animate-spin text-muted-foreground" />}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {error ? (
            <div className="flex items-center gap-2 text-sm text-destructive py-8 justify-center">
              <AlertCircle className="w-4 h-4" />
              <span>{error}</span>
            </div>
          ) : !data || data.items.length === 0 ? (
            <div className="text-sm text-muted-foreground py-12 text-center">
              没有匹配的审计日志
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[40px]"></TableHead>
                    <TableHead className="w-[160px]">时间</TableHead>
                    <TableHead className="w-[120px]">管理员</TableHead>
                    <TableHead className="w-[140px]">操作</TableHead>
                    <TableHead className="w-[100px]">目标类型</TableHead>
                    <TableHead>目标 ID</TableHead>
                    <TableHead className="w-[120px]">IP</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((item: AuditLogEntry) => {
                    const expanded = expandedIds.has(item.id)
                    return (
                      <Fragment key={item.id}>
                        <TableRow
                          onClick={() => toggleExpand(item.id)}
                          className="cursor-pointer hover:bg-muted/50"
                        >
                          <TableCell className="text-muted-foreground">
                            {item.detail ? (
                              expanded ? (
                                <ChevronDown className="w-4 h-4" />
                              ) : (
                                <ChevronRight className="w-4 h-4" />
                              )
                            ) : null}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground font-mono">
                            {item.created_at || '-'}
                          </TableCell>
                          <TableCell className="text-sm">
                            <div className="flex flex-col">
                              <span className="font-medium">{item.admin_username || '-'}</span>
                              {item.admin_id && (
                                <span className="text-[10px] text-muted-foreground font-mono">
                                  {item.admin_id}
                                </span>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className={`border text-xs ${actionBadgeClass(item.action)}`}>
                              {item.action_label}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-xs">
                            {item.target_type ? TARGET_LABEL[item.target_type] || item.target_type : '-'}
                          </TableCell>
                          <TableCell className="text-xs font-mono text-muted-foreground">
                            {item.target_id || '-'}
                          </TableCell>
                          <TableCell className="text-xs font-mono text-muted-foreground">
                            {item.ip || '-'}
                          </TableCell>
                        </TableRow>
                        {expanded && item.detail && (
                          <TableRow className="bg-muted/30">
                            <TableCell colSpan={7}>
                              <div className="py-2">
                                <p className="text-xs text-muted-foreground mb-2">详情（密码类字段已过滤）</p>
                                <pre className="text-xs font-mono bg-background rounded-md p-3 border border-border overflow-auto max-h-64">
                                  {JSON.stringify(item.detail, null, 2)}
                                </pre>
                              </div>
                            </TableCell>
                          </TableRow>
                        )}
                      </Fragment>
                    )
                  })}
                </TableBody>
              </Table>

              {/* 分页 */}
              <div className="flex items-center justify-between pt-4">
                <p className="text-xs text-muted-foreground">
                  第 {page} / {totalPages} 页，共 {total} 条
                </p>
                <div className="flex gap-2">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page <= 1 || loading}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                  >
                    上一页
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={page >= totalPages || loading}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    下一页
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
