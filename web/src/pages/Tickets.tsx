import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTickets, useCreateTicket } from '@/hooks/useApi'
import { api } from '@/lib/api'
import { getKeyMaterialPrompt, SERVICE_TYPE_OPTIONS } from '@/lib/ticketPresentation'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog'
import { StatusBadge, CategoryBadge, PriorityBadge } from '@/components/layout/StatusBadge'
import type { Ticket, TicketCategory } from '@/types'
import {
  Bot, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Loader2, Plus, RefreshCw, Search, SendHorizontal, Sparkles,
} from 'lucide-react'

const PAGE_SIZE_OPTIONS = [10, 20, 50]

const MOCK_CATEGORY_OPTIONS: Array<{ value: TicketCategory; label: string }> = [
  { value: 'inquiry', label: '咨询问询' },
  { value: 'technical', label: '技术支持' },
  { value: 'billing', label: '账务问题' },
  { value: 'complaint', label: '投诉建议' },
]

const MOCK_CATEGORY_LABELS = MOCK_CATEGORY_OPTIONS.reduce<Record<TicketCategory, string>>(
  (acc, option) => {
    acc[option.value] = option.label
    return acc
  },
  {} as Record<TicketCategory, string>,
)

const EXAMPLE_PROMPTS = [
  '今天上午 10:15 开始后台一直 504，部分业务人员无法登录，请尽快恢复，联系 ops@example.com',
  '上个月账单多扣了 200 元，已经核对订单记录，请帮我退款，手机号 13800000000',
  '我找不到导出本月工单报表的入口，请告知在哪里操作',
]

interface AgentTicketComposerProps {
  compact?: boolean
  onCreated?: () => void
}

export function AgentTicketComposer({ compact = false, onCreated }: AgentTicketComposerProps) {
  const [content, setContent] = useState('')
  const [serviceType, setServiceType] = useState('account_access')
  const [keyMaterials, setKeyMaterials] = useState('')
  const [mockPrompt, setMockPrompt] = useState('')
  const [mockSource, setMockSource] = useState<string>('')
  const [mockCategory, setMockCategory] = useState<TicketCategory>('inquiry')
  const [mockLoading, setMockLoading] = useState(false)
  const createMutation = useCreateTicket()
  const materialPrompt = getKeyMaterialPrompt(serviceType)

  const canSubmit = content.trim().length > 0 && !createMutation.isPending

  const handleSubmit = async () => {
    if (!canSubmit) return
    // user_id 由后端从 session 自动注入（防伪造），前端不再显示也不传
    await createMutation.mutateAsync({
      content: content.trim(),
      service_type: serviceType,
      key_materials: keyMaterials.trim() ? { notes: keyMaterials.trim() } : {},
    })
    setContent('')
    setKeyMaterials('')
    onCreated?.()
  }

  const refreshMockPrompt = async () => {
    setMockLoading(true)
    try {
      const result = await api.generateMockTicketQuestion(mockCategory)
      setMockPrompt(result.prompt)
      setContent(result.prompt)
      const categoryLabel = result.category ? MOCK_CATEGORY_LABELS[result.category] : MOCK_CATEGORY_LABELS[mockCategory]
      setMockSource(result.knowledge_title
        ? `${categoryLabel} · ${result.generation_mode === 'llm' ? '智能生成' : '兜底生成'} · ${result.knowledge_title}`
        : `${categoryLabel} · 兜底生成`)
    } catch {
      const fallback = EXAMPLE_PROMPTS[Math.floor(Math.random() * EXAMPLE_PROMPTS.length)]
      setMockPrompt(fallback)
      setContent(fallback)
      setMockSource('本地兜底')
    } finally {
      setMockLoading(false)
    }
  }

  return (
    <div className={compact ? 'space-y-4' : 'rounded-xl border border-border bg-card p-4 shadow-sm'}>
      {!compact && (
        <div className="mb-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
              <Bot className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold">创建服务工单</h3>
                <Badge variant="outline" className="border-primary/30 bg-primary/10 text-[10px] text-primary">
                  智能识别
                </Badge>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                选择服务类型并描述问题，系统会整理处理方向并进入服务台流程。
              </p>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={refreshMockPrompt} type="button" disabled={mockLoading}>
            <Sparkles className="h-3.5 w-3.5" />
            生成问题
          </Button>
        </div>
      )}

      <div className="mb-3 grid gap-3 lg:grid-cols-[220px_1fr]">
        <div className="space-y-1.5">
          <label htmlFor={compact ? 'ticket-service-type-compact' : 'ticket-service-type'} className="text-xs font-medium text-foreground">
            服务类型
          </label>
          <Select
            value={serviceType}
            onValueChange={(value) => setServiceType(value || 'other')}
          >
            <SelectTrigger id={compact ? 'ticket-service-type-compact' : 'ticket-service-type'} className="h-9 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="border-border bg-popover">
              {SERVICE_TYPE_OPTIONS.map(option => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <label htmlFor={compact ? 'ticket-key-materials-compact' : 'ticket-key-materials'} className="text-xs font-medium text-foreground">
            {materialPrompt.title}
          </label>
          <Input
            id={compact ? 'ticket-key-materials-compact' : 'ticket-key-materials'}
            value={keyMaterials}
            onChange={(e) => setKeyMaterials(e.target.value)}
            placeholder={materialPrompt.placeholder}
            className="h-9 text-sm"
          />
          <p className="text-[11px] text-muted-foreground">{materialPrompt.helperText}</p>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="例如：今天上午 10:15 开始企业邮箱无法登录，影响我接收项目通知，请协助恢复。"
          rows={compact ? 5 : 2}
          className="min-h-[72px] resize-none"
        />
        <div className="flex items-end gap-2 lg:flex-col lg:justify-end">
          {compact && (
            <Button variant="outline" onClick={refreshMockPrompt} type="button" disabled={mockLoading}>
              <Sparkles className="h-3.5 w-3.5" />
              生成问题
            </Button>
          )}
          <Button onClick={handleSubmit} disabled={!canSubmit} className={compact ? 'min-w-28' : 'min-w-24'}>
            {createMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <SendHorizontal className="h-3.5 w-3.5" />
            )}
            {createMutation.isPending ? '创建中' : '创建'}
          </Button>
        </div>
      </div>

      <div className="mt-3 grid gap-2 lg:grid-cols-[auto_auto_auto_auto_auto_1fr] lg:items-center">
        <span className="text-[11px] text-muted-foreground">系统将辅助整理：</span>
        <Badge variant="secondary" className="w-fit text-[10px]">问题标题</Badge>
        <Badge variant="secondary" className="w-fit text-[10px]">分类</Badge>
        <Badge variant="secondary" className="w-fit text-[10px]">优先级</Badge>
        <Select
          value={mockCategory}
          onValueChange={(value) => setMockCategory(value as TicketCategory)}
        >
          <SelectTrigger
            aria-label="选择 mock 问题生成类型"
            className="h-8 w-full min-w-[112px] text-[11px] lg:w-[112px]"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="border-border bg-popover">
            {MOCK_CATEGORY_OPTIONS.map(option => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="flex min-w-0 items-center rounded-md border border-border bg-background/60">
          <button
            type="button"
            onClick={() => mockPrompt && setContent(mockPrompt)}
            className="min-h-8 flex-1 truncate px-2.5 text-left text-[11px] text-muted-foreground hover:text-foreground"
            title={mockPrompt || '点击右侧刷新，根据知识库生成一个示例问题'}
          >
            {mockPrompt || '可根据知识库生成一条示例问题'}
          </button>
          {mockSource && (
            <span className="hidden shrink-0 px-2 text-[10px] text-muted-foreground/80 lg:inline">
              {mockSource}
            </span>
          )}
          <Tooltip>
            <TooltipTrigger
              render={(
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-xs"
                  className="mr-1"
                  onClick={refreshMockPrompt}
                  disabled={mockLoading}
                />
              )}
            >
              {mockLoading
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <RefreshCw className="h-3 w-3" />}
            </TooltipTrigger>
            <TooltipContent>根据知识库换一个问题</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </div>
  )
}

export function Tickets() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<string>('')
  const [category, setCategory] = useState<string>('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [dialogOpen, setDialogOpen] = useState(false)

  const params: Record<string, string> = {}
  if (status) params.status = status
  if (category) params.category = category

  const { data: tickets = [], isLoading, refetch } = useTickets(params)

  const filtered = useMemo(() => {
    if (!search) return tickets
    const s = search.toLowerCase()
    return tickets.filter((t: Ticket) =>
      t.content?.toLowerCase().includes(s) ||
      t.ticket_id?.toLowerCase().includes(s),
    )
  }, [tickets, search])

  const total = filtered.length
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const currentPage = Math.min(page, totalPages)
  const startIdx = (currentPage - 1) * pageSize
  const endIdx = Math.min(startIdx + pageSize, total)
  const pageItems = filtered.slice(startIdx, endIdx)

  const pageNumbers = useMemo(() => {
    const max = 5
    if (totalPages <= max) return Array.from({ length: totalPages }, (_, i) => i + 1)
    const start = Math.max(1, Math.min(currentPage - 2, totalPages - max + 1))
    return Array.from({ length: max }, (_, i) => start + i)
  }, [currentPage, totalPages])

  const handleCreatedFromDialog = () => {
    setDialogOpen(false)
    refetch()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">员工服务工单</h2>
          <p className="mt-1 text-sm text-muted-foreground">提交内部服务请求并追踪处理进度</p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger render={<Button size="sm" />}>
            <Plus className="h-4 w-4" />
            提交工单
          </DialogTrigger>
          <DialogContent className="border-border bg-card sm:max-w-[680px]">
            <DialogHeader>
              <div className="flex items-start gap-3 pr-8">
                <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
                  <Bot className="h-5 w-5" />
                </div>
                <div className="space-y-1">
                  <DialogTitle>提交服务工单</DialogTitle>
                  <DialogDescription>
                    选择服务类型，描述问题，并补充可选关键材料。
                  </DialogDescription>
                </div>
              </div>
            </DialogHeader>
            <AgentTicketComposer compact onCreated={handleCreatedFromDialog} />
            <DialogFooter className="border-t border-border pt-4 text-xs text-muted-foreground">
              系统会先整理问题信息，再创建并分派工单。
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <AgentTicketComposer onCreated={() => refetch()} />

      <Card className="border-border bg-card">
        <CardContent className="p-3">
          <div className="flex items-center gap-3">
            <div className="relative max-w-xs flex-1">
              <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder="搜索工单..."
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value)
                  setPage(1)
                }}
                className="h-8 pl-8 text-sm"
              />
            </div>
            <Select
              value={status}
              onValueChange={(v) => {
                setStatus(v === 'all' ? '' : (v ?? ''))
                setPage(1)
              }}
            >
              <SelectTrigger className="h-8 w-32 text-sm">
                <SelectValue placeholder="全部状态" />
              </SelectTrigger>
              <SelectContent className="border-border bg-popover">
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="received">已接收</SelectItem>
                <SelectItem value="classifying">分类中</SelectItem>
                <SelectItem value="processing">处理中</SelectItem>
                <SelectItem value="reviewing">审核中</SelectItem>
                <SelectItem value="pending_human_review">待人工审核</SelectItem>
                <SelectItem value="waiting_user_input">待用户补充</SelectItem>
                <SelectItem value="completed">已完成</SelectItem>
                <SelectItem value="failed">失败</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={category}
              onValueChange={(v) => {
                setCategory(v === 'all' ? '' : (v ?? ''))
                setPage(1)
              }}
            >
              <SelectTrigger className="h-8 w-32 text-sm">
                <SelectValue placeholder="全部分类" />
              </SelectTrigger>
              <SelectContent className="border-border bg-popover">
                <SelectItem value="all">全部分类</SelectItem>
                <SelectItem value="technical">技术支持</SelectItem>
                <SelectItem value="billing">账务问题</SelectItem>
                <SelectItem value="complaint">投诉建议</SelectItem>
                <SelectItem value="inquiry">咨询问询</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              <RefreshCw className="h-3.5 w-3.5" />
              刷新
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card className="overflow-hidden border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow className="border-border hover:bg-transparent">
              <TableHead className="h-11 w-[160px] px-4 text-xs font-medium text-muted-foreground">工单 ID</TableHead>
              <TableHead className="h-11 px-4 text-xs font-medium text-muted-foreground">内容</TableHead>
              <TableHead className="h-11 w-[110px] px-4 text-xs font-medium text-muted-foreground">分类</TableHead>
              <TableHead className="h-11 w-[90px] px-4 text-xs font-medium text-muted-foreground">优先级</TableHead>
              <TableHead className="h-11 w-[120px] px-4 text-xs font-medium text-muted-foreground">状态</TableHead>
              <TableHead className="h-11 w-[80px] px-4 text-right text-xs font-medium text-muted-foreground">评分</TableHead>
              <TableHead className="h-11 w-[150px] px-4 text-right text-xs font-medium text-muted-foreground">创建时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: pageSize }).map((_, i) => (
                <TableRow key={i} className="border-border">
                  {Array.from({ length: 7 }).map((_, j) => (
                    <TableCell key={j} className="px-4 py-3">
                      <div className="h-4 w-24 animate-pulse rounded bg-muted" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : pageItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="py-16 text-center text-sm text-muted-foreground">
                  暂无工单数据
                </TableCell>
              </TableRow>
            ) : (
              pageItems.map((ticket: Ticket) => (
                <TableRow
                  key={ticket.ticket_id}
                  className="cursor-pointer border-border transition-colors hover:bg-muted/40"
                  onClick={() => navigate(`/tickets/${ticket.ticket_id}`)}
                >
                  <TableCell className="px-4 py-3 font-mono text-[12px] text-primary">{ticket.ticket_id?.slice(0, 16)}</TableCell>
                  <TableCell className="max-w-[420px] truncate px-4 py-3 text-[13px]">{ticket.content}</TableCell>
                  <TableCell className="px-4 py-3">{ticket.category ? <CategoryBadge category={ticket.category} /> : '-'}</TableCell>
                  <TableCell className="px-4 py-3">{ticket.priority ? <PriorityBadge priority={ticket.priority} /> : '-'}</TableCell>
                  <TableCell className="px-4 py-3"><StatusBadge status={ticket.status} /></TableCell>
                  <TableCell className="px-4 py-3 text-right font-mono text-[12px] tabular-nums">
                    {ticket.review_score != null ? ticket.review_score.toFixed(2) : '-'}
                  </TableCell>
                  <TableCell className="px-4 py-3 text-right text-[12px] tabular-nums text-muted-foreground">
                    {ticket.created_at ? new Date(ticket.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>

        <div className="flex items-center justify-between gap-4 border-t border-border bg-muted/20 px-4 py-3">
          <div className="text-xs tabular-nums text-muted-foreground">
            {total === 0 ? '共 0 条' : `第 ${startIdx + 1}-${endIdx} 条，共 ${total} 条`}
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setPage(1)}
              disabled={currentPage <= 1}
              title="第一页"
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={currentPage <= 1}
              title="上一页"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            {pageNumbers.map(p => (
              <Button
                key={p}
                variant={p === currentPage ? 'default' : 'ghost'}
                size="icon"
                className="h-8 w-8 text-xs tabular-nums"
                onClick={() => setPage(p)}
              >
                {p}
              </Button>
            ))}
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={currentPage >= totalPages}
              title="下一页"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => setPage(totalPages)}
              disabled={currentPage >= totalPages}
              title="最后一页"
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">每页</span>
            <Select
              value={String(pageSize)}
              onValueChange={(v) => {
                setPageSize(Number(v))
                setPage(1)
              }}
            >
              <SelectTrigger className="h-8 w-[70px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="border-border bg-popover">
                {PAGE_SIZE_OPTIONS.map(n => (
                  <SelectItem key={n} value={String(n)}>{n}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-xs text-muted-foreground">条</span>
          </div>
        </div>
      </Card>
    </div>
  )
}
