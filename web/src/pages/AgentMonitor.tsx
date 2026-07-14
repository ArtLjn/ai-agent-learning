import { useMemo, useState } from 'react'
import { api } from '@/lib/api'
import { useTraces } from '@/hooks/useApi'
import type { Span, Trace, TraceDetail } from '@/types'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import { DecisionCard, type DecisionData } from '@/components/trace/DecisionCard'
import { extractFields, FieldList } from '@/components/trace/spanFormat'
import {
  Activity, RefreshCw, Cpu, Bot, Wrench, Zap, FileJson, Search,
  ArrowRightLeft, Info, X, AlertCircle, Copy, ChevronDown, ChevronUp,
  ChevronLeft, ChevronRight, BookOpen, CheckCircle2, Tag, GitFork,
} from 'lucide-react'

type TimelineNode = Span & {
  depth: number
  roundIndex: number
}

type TimelineRound = {
  index: number
  title: string
  nodes: TimelineNode[]
  duration: number
  success: number
  error: number
}

const traceStatusStyles: Record<string, string> = {
  running: 'bg-primary/15 text-primary',
  completed: 'bg-success/15 text-success',
  failed: 'bg-destructive/15 text-destructive',
}

const spanStatusStyles: Record<string, string> = {
  ok: 'bg-success/15 text-success',
  error: 'bg-destructive/15 text-destructive',
  fallback: 'bg-warning/15 text-warning',
}

const nodeTypeColors: Record<string, string> = {
  node: 'bg-orange-400',
  react_iter: 'bg-sky-500',
  llm_call: 'bg-violet-500',
  tool_call: 'bg-lime-500',
}

const nodeTypeLabels: Record<string, string> = {
  node: '工作流节点',
  react_iter: 'ReAct 推理',
  llm_call: 'LLM 调用',
  tool_call: '工具调用',
}

const nodeTypeIcons: Record<string, typeof Cpu> = {
  node: Cpu,
  react_iter: Bot,
  llm_call: Zap,
  tool_call: Wrench,
}

const categoryLabels: Record<string, string> = {
  technical: 'IT 与办公支持',
  billing: '费用薪酬报销',
  complaint: '风险投诉升级',
  inquiry: '制度流程咨询',
}

const PAGE_SIZE_OPTIONS = [5, 10, 20]

export function AgentMonitor() {
  const [filters, setFilters] = useState({ trace: '', ticket: '', status: '' })
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(5)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [details, setDetails] = useState<Record<string, TraceDetail>>({})
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set())
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null)
  const traceParams = useMemo(() => {
    const params: Record<string, string> = {
      limit: String(pageSize),
      offset: String(page * pageSize),
    }
    if (filters.status.trim()) {
      params.status = filters.status.trim()
    }
    return params
  }, [page, pageSize, filters.status])
  const { data: tracesData, isLoading, refetch } = useTraces(traceParams)

  const traces = (tracesData?.traces || []) as Trace[]
  const total = Number((tracesData as { total?: number } | undefined)?.total ?? traces.length)
  const totalPages = Math.max(Math.ceil(total / pageSize), 1)
  const pageTraces = traces
  const filteredTraces = useMemo(() => {
    return pageTraces.filter((trace) => {
      const traceMatched = !filters.trace.trim() || trace.trace_id.includes(filters.trace.trim())
      const ticketMatched = !filters.ticket.trim() || trace.ticket_id.includes(filters.ticket.trim())
      const statusMatched = !filters.status.trim() || trace.status.includes(filters.status.trim())
      return traceMatched && ticketMatched && statusMatched
    })
  }, [pageTraces, filters])

  const updateFilter = (key: keyof typeof filters, value: string) => {
    setPage(0)
    setExpandedIds(new Set())
    setFilters((prev) => ({ ...prev, [key]: value }))
  }

  const changePage = (value: number) => {
    setPage(value)
    setExpandedIds(new Set())
    setSelectedSpan(null)
  }

  const changePageSize = (value: number) => {
    setPage(0)
    setExpandedIds(new Set())
    setPageSize(value)
  }

  const toggleTrace = async (trace: Trace) => {
    // 切换 trace 时自动收起右侧详情抽屉，避免视觉错位
    setSelectedSpan(null)
    const next = new Set(expandedIds)
    if (next.has(trace.trace_id)) {
      next.delete(trace.trace_id)
      setExpandedIds(next)
      return
    }

    next.add(trace.trace_id)
    setExpandedIds(next)
    if (details[trace.trace_id]) return

    setLoadingIds((prev) => new Set(prev).add(trace.trace_id))
    try {
      const detail = await api.getTicketTrace(trace.ticket_id)
      setDetails((prev) => ({ ...prev, [trace.trace_id]: detail }))
    } finally {
      setLoadingIds((prev) => {
        const copy = new Set(prev)
        copy.delete(trace.trace_id)
        return copy
      })
    }
  }

  const clearFilters = () => {
    setPage(0)
    setExpandedIds(new Set())
    setFilters({ trace: '', ticket: '', status: '' })
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <Activity className="h-5 w-5 text-primary" />
            Agent 监控
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">链路追踪、节点耗时与输入输出分析</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
          刷新
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Input
          value={filters.trace}
          onChange={(event) => updateFilter('trace', event.target.value)}
          placeholder="Trace ID"
          className="h-10 w-[260px]"
        />
        <Input
          value={filters.ticket}
          onChange={(event) => updateFilter('ticket', event.target.value)}
          placeholder="Ticket ID"
          className="h-10 w-[260px]"
        />
        <Input
          value={filters.status}
          onChange={(event) => updateFilter('status', event.target.value)}
          placeholder="状态"
          className="h-10 w-[180px]"
        />
        <Button size="sm" className="h-10 px-5" onClick={() => refetch()}>
          <Search className="mr-1.5 h-4 w-4" />
          查询
        </Button>
        <Button variant="outline" size="sm" onClick={clearFilters}>
          <X className="mr-1.5 h-3.5 w-3.5" />
          重置
        </Button>
      </div>

      <TraceList
        traces={filteredTraces}
        isLoading={isLoading}
        pageSize={pageSize}
        details={details}
        expandedIds={expandedIds}
        loadingIds={loadingIds}
        onToggle={toggleTrace}
        onNodeClick={setSelectedSpan}
      />

      <TracePagination
        page={page}
        pageSize={pageSize}
        total={total}
        totalPages={totalPages}
        isLoading={isLoading}
        onPageChange={changePage}
        onPageSizeChange={changePageSize}
      />

      {selectedSpan && (
        <NodeDrawer span={selectedSpan} onClose={() => setSelectedSpan(null)} />
      )}
    </div>
  )
}

function TraceList({
  traces,
  isLoading,
  pageSize,
  details,
  expandedIds,
  loadingIds,
  onToggle,
  onNodeClick,
}: {
  traces: Trace[]
  isLoading: boolean
  pageSize: number
  details: Record<string, TraceDetail>
  expandedIds: Set<string>
  loadingIds: Set<string>
  onToggle: (trace: Trace) => void
  onNodeClick: (span: Span) => void
}) {
  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: pageSize }).map((_, index) => (
          <Skeleton key={index} className="h-[42px] rounded-lg" />
        ))}
      </div>
    )
  }

  if (traces.length === 0) {
    return (
      <Card className="bg-card border-border">
        <CardContent className="py-14 text-center text-sm text-muted-foreground">
          暂无匹配 trace
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-3">
      {traces.map((trace) => (
        <TraceCard
          key={trace.trace_id}
          trace={trace}
          detail={details[trace.trace_id]}
          expanded={expandedIds.has(trace.trace_id)}
          loading={loadingIds.has(trace.trace_id)}
          onToggle={() => onToggle(trace)}
          onNodeClick={onNodeClick}
        />
      ))}
    </div>
  )
}

function TracePagination({
  page,
  pageSize,
  total,
  totalPages,
  isLoading,
  onPageChange,
  onPageSizeChange,
}: {
  page: number
  pageSize: number
  total: number
  totalPages: number
  isLoading: boolean
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
}) {
  const pages = getVisiblePages(page, totalPages)

  return (
    <div className="flex items-center justify-center gap-3 py-2">
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={page === 0 || isLoading}
        onClick={() => onPageChange(Math.max(page - 1, 0))}
        className="text-muted-foreground"
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <div className="flex items-center gap-2">
        {pages.map((item, index) => (
          item === 'ellipsis' ? (
            <span key={`ellipsis-${index}`} className="px-2 text-sm text-muted-foreground">...</span>
          ) : (
            <button
              key={item}
              type="button"
              disabled={isLoading}
              onClick={() => onPageChange(item)}
              className={`h-10 min-w-10 rounded-lg border px-3 text-sm transition-colors ${
                item === page
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border bg-card text-foreground hover:border-primary/60'
              }`}
            >
              {item + 1}
            </button>
          )
        ))}
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        disabled={page >= totalPages - 1 || isLoading}
        onClick={() => onPageChange(Math.min(page + 1, totalPages - 1))}
        className="text-muted-foreground"
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
      <select
        value={pageSize}
        onChange={(event) => onPageSizeChange(Number(event.target.value))}
        className="ml-3 h-10 rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none focus:border-primary"
      >
        {PAGE_SIZE_OPTIONS.map((option) => (
          <option key={option} value={option}>{option} / page</option>
        ))}
      </select>
      <span className="text-xs text-muted-foreground">共 {total} 条</span>
    </div>
  )
}

function TraceCard({
  trace,
  detail,
  expanded,
  loading,
  onToggle,
  onNodeClick,
}: {
  trace: Trace
  detail?: TraceDetail
  expanded: boolean
  loading: boolean
  onToggle: () => void
  onNodeClick: (span: Span) => void
}) {
  const rounds = useMemo(() => buildRounds(detail), [detail])
  const flatNodes = rounds.flatMap((round) => round.nodes)
  const success = flatNodes.filter((node) => node.status !== 'error').length
  const failed = flatNodes.length - success

  return (
    <Card className="overflow-hidden rounded-lg bg-card border-border">
      <button
        type="button"
        onClick={onToggle}
        className="grid w-full grid-cols-[minmax(280px,1.4fr)_minmax(180px,0.8fr)_64px_72px_88px_56px] items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-background/70"
      >
        <span className="min-w-0 space-y-0.5">
          <span className="block truncate text-[13px] font-medium text-foreground">
            {trace.ticket_summary || '暂无工单内容'}
          </span>
          <span className="font-mono text-[10px] text-primary/70">Trace {trace.trace_id.slice(0, 18)}...</span>
        </span>
        <span className="min-w-0 space-y-0.5">
          <span className="block font-mono text-[13px] text-foreground">{trace.ticket_id.slice(0, 16)}</span>
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Tag className="h-3 w-3" />
            {trace.ticket_category ? categoryLabels[trace.ticket_category] || trace.ticket_category : '未分类'}
            {trace.ticket_priority ? ` · ${trace.ticket_priority}` : ''}
          </span>
        </span>
        <span className="text-[13px] tabular-nums text-foreground" title="节点数">
          <strong className="font-medium">{trace.node_count || 0}</strong>
          <span className="ml-1 text-[10px] text-muted-foreground">节点</span>
        </span>
        <span className="flex items-center gap-1 text-[13px] tabular-nums text-foreground" title="知识库引用数">
          <BookOpen className="h-3 w-3 text-primary" />
          <strong className="font-medium">{trace.reference_count || 0}</strong>
        </span>
        <span className="text-[13px] tabular-nums text-foreground" title="总耗时">
          <strong className="font-medium">{formatMilliseconds(trace.duration)}</strong>
        </span>
        <span className="flex shrink-0 items-center justify-end text-[11px] text-primary">
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </span>
      </button>

      {expanded && (
        <div className="border-t border-border p-3">
          {loading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">加载 Timeline...</div>
          ) : detail ? (
            <div className="space-y-3">
              <TraceBusinessSummary trace={detail} />
              <div className="overflow-x-auto rounded-lg border border-border bg-background">
                <div className="min-w-[720px]">
              <TimelineStats
                totalNodes={flatNodes.length}
                totalRounds={rounds.length}
                success={success}
                failed={failed}
              />
              <TimelineHeader />
              {rounds.map((round) => (
                <TimelineRoundView
                  key={round.index}
                  round={round}
                  onNodeClick={onNodeClick}
                />
              ))}
              <TimelineLegend />
                </div>
              </div>
            </div>
          ) : (
            <div className="py-8 text-center text-sm text-muted-foreground">暂无 Timeline 数据</div>
          )}
        </div>
      )}
    </Card>
  )
}

function TraceBusinessSummary({ trace }: { trace: TraceDetail }) {
  return (
    <div className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-3">
      <div className="rounded-lg border border-border bg-background p-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <Info className="h-3.5 w-3.5 text-primary" />
          工单上下文
        </div>
        <p className="line-clamp-2 text-sm text-foreground">
          {trace.ticket_summary || '暂无工单内容'}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Badge variant="outline" className="border-border bg-card text-xs">
            {trace.ticket_category ? categoryLabels[trace.ticket_category] || trace.ticket_category : '未分类'}
          </Badge>
          <Badge variant="outline" className="border-border bg-card text-xs">
            {trace.ticket_priority || '无优先级'}
          </Badge>
          <Badge variant="outline" className="border-border bg-card text-xs">
            评分 {trace.ticket_review_score ?? '-'}
          </Badge>
        </div>
      </div>
      <div className="rounded-lg border border-border bg-background p-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <CheckCircle2 className="h-3.5 w-3.5 text-success" />
          处理结果
        </div>
        <p className="line-clamp-2 text-sm text-foreground">
          {trace.ticket_result || '暂无处理结果'}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Badge variant="outline" className="border-primary/30 bg-primary/10 text-xs text-primary">
            知识库引用 {trace.reference_count || 0}
          </Badge>
          <Badge variant="outline" className={`border-0 text-xs ${traceStatusStyles[trace.status] || ''}`}>
            {trace.status}
          </Badge>
        </div>
      </div>
    </div>
  )
}

function TimelineStats({
  totalNodes,
  totalRounds,
  success,
  failed,
}: {
  totalNodes: number
  totalRounds: number
  success: number
  failed: number
}) {
  return (
    <div className="flex gap-8 border-b border-border px-3 py-2 text-xs">
      <span className="text-muted-foreground">总节点数: <strong className="text-foreground">{totalNodes}</strong></span>
      <span className="text-muted-foreground">总轮次: <strong className="text-foreground">{totalRounds}</strong></span>
      <span className="text-muted-foreground">成功: <strong className="text-success">{success}</strong></span>
      <span className="text-muted-foreground">失败: <strong className="text-destructive">{failed}</strong></span>
    </div>
  )
}

function TimelineHeader() {
  return (
    <div className="grid grid-cols-[28px_220px_1fr_72px_64px_140px] border-b border-border px-3 py-1.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
      <div className="text-center">类型</div>
      <div>名称</div>
      <div>业务摘要</div>
      <div className="text-right">耗时</div>
      <div className="text-center">状态</div>
      <div>时间线</div>
    </div>
  )
}

function TimelineRoundView({
  round,
  onNodeClick,
}: {
  round: TimelineRound
  onNodeClick: (span: Span) => void
}) {
  const [expanded, setExpanded] = useState(true)
  const roundDuration = Math.max(round.duration, 0.001)

  return (
    <div className="border-b border-border/60">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className={`grid w-full grid-cols-[28px_220px_1fr_72px_64px_140px] items-center gap-2 border-b border-border/60 px-3 py-2 text-left transition-colors ${
          expanded ? 'bg-primary/5' : 'hover:bg-card'
        }`}
      >
        <span className="col-span-2 flex items-center gap-2">
          <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-semibold text-primary">
            R{round.index + 1}
          </span>
          <span className="truncate font-mono text-[11px] text-muted-foreground">{round.title}</span>
        </span>
        <span className="truncate text-[11px] text-muted-foreground">
          {round.nodes.length} 节点 · ✓{round.success}{round.error > 0 && ` · ×${round.error}`}
        </span>
        <span className="text-right font-mono text-[11px] tabular-nums text-foreground/80">
          {formatSeconds(round.duration)}
        </span>
        <span className="text-center text-[10px] text-muted-foreground">—</span>
        <span className="text-right text-[11px] text-primary">{expanded ? '收起' : '展开'}</span>
      </button>
      {expanded ? (
        <>
          {round.nodes.map((node) => (
            <TimelineNodeRow
              key={node.span_id}
              node={node}
              totalDuration={roundDuration}
              onClick={() => onNodeClick(node)}
            />
          ))}
        </>
      ) : (
        <CompactRoundBar nodes={round.nodes} totalDuration={roundDuration} />
      )}
    </div>
  )
}

function CompactRoundBar({ nodes, totalDuration }: { nodes: TimelineNode[]; totalDuration: number }) {
  const segments = nodes.reduce<Array<{ node: TimelineNode; left: number; width: number }>>((acc, node) => {
    const elapsed = acc.reduce((sum, item) => sum + (item.node.duration || 0), 0)
    const left = totalDuration > 0 ? (elapsed / totalDuration) * 100 : 0
    const width = totalDuration > 0 ? Math.max(((node.duration || 0) / totalDuration) * 100, 1) : 1
    acc.push({ node, left, width })
    return acc
  }, [])

  return (
    <div className="grid grid-cols-[28px_220px_1fr_72px_64px_140px] items-center border-b border-border px-3 py-1 text-[11px] text-muted-foreground">
      <div />
      <div className="text-primary">折叠预览</div>
      <div />
      <div />
      <div />
      <div className="relative h-2 rounded bg-muted">
        {segments.map(({ node, left, width }) => {
          return (
            <div
              key={node.span_id}
              className={`absolute top-0 h-full rounded ${nodeTypeColors[node.span_type] || 'bg-muted-foreground'}`}
              style={{ left: `${left}%`, width: `${width}%` }}
            />
          )
        })}
      </div>
    </div>
  )
}

function TimelineNodeRow({
  node,
  totalDuration,
  onClick,
}: {
  node: TimelineNode
  totalDuration: number
  onClick: () => void
}) {
  const Icon = nodeTypeIcons[node.span_type] || Cpu
  const width = totalDuration > 0 ? Math.max(((node.duration || 0) / totalDuration) * 100, 1) : 1
  const indent = Math.min(node.depth * 22, 80)
  const summary = summarizeSpan(node)

  return (
    <button
      type="button"
      onClick={onClick}
      className="grid w-full grid-cols-[28px_220px_1fr_72px_64px_140px] items-center gap-2 border-b border-border/40 px-3 py-1.5 text-left transition-colors hover:bg-card"
    >
      <div className="text-center">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${nodeTypeColors[node.span_type] || 'bg-muted-foreground'}`} />
      </div>
      <div className="flex min-w-0 items-center gap-1.5" style={{ paddingLeft: indent }}>
        <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate text-[12px] font-medium">{node.name}</span>
        {node.depth > 0 && (
          <span className="shrink-0 text-[10px] text-muted-foreground/60">↳ L{node.depth}</span>
        )}
      </div>
      <div className="truncate text-[11px] text-muted-foreground">{summary}</div>
      <div className="text-right font-mono text-[11px] tabular-nums text-foreground/80">
        {formatSeconds(node.duration)}
      </div>
      <div className="text-center">
        <Badge variant="outline" className={`border-0 px-1 py-0 text-[9px] leading-tight ${spanStatusStyles[node.status] || 'bg-secondary'}`}>
          {node.status}
        </Badge>
      </div>
      <div className="relative h-2 rounded-full bg-muted overflow-hidden">
        <div
          className={`absolute top-0 left-0 h-full rounded-full ${nodeTypeColors[node.span_type] || 'bg-muted-foreground'}`}
          style={{ width: `${width}%` }}
        />
      </div>
    </button>
  )
}

function TimelineLegend() {
  const items = [
    ['node', '工作流节点'],
    ['react_iter', 'ReAct 推理'],
    ['llm_call', 'LLM 调用'],
    ['tool_call', '工具调用'],
  ]
  return (
    <div className="flex flex-wrap gap-4 px-3 py-2 text-xs text-muted-foreground">
      {items.map(([type, label]) => (
        <span key={type} className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${nodeTypeColors[type]}`} />
          {label}
        </span>
      ))}
      <span className="flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full bg-destructive" />
        错误
      </span>
    </div>
  )
}

function NodeDrawer({ span, onClose }: { span: Span; onClose: () => void }) {
  const Icon = nodeTypeIcons[span.span_type] || Cpu
  const metadata = (span.metadata || {}) as Record<string, unknown>
  const decision = (metadata.decision || null) as DecisionData | null

  return (
    <div
      className="fixed inset-0 z-[100] flex justify-end bg-black/30"
      onClick={onClose}
    >
      <aside
        className="h-full w-[760px] max-w-[95vw] border-l border-border bg-popover shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div>
            <h3 className="flex items-center gap-2 text-base font-semibold">
              <Icon className="h-4 w-4 text-primary" />
              节点详情
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">{nodeTypeLabels[span.span_type] || span.span_type}</p>
          </div>
          <Button variant="ghost" size="icon-sm" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <ScrollArea className="h-[calc(100vh-73px)]">
          <div className="space-y-5 p-5">
            <div className="flex items-center gap-3">
              <Badge variant="outline" className={`border-0 ${spanStatusStyles[span.status] || 'bg-secondary'}`}>
                {span.status}
              </Badge>
              <Badge variant="outline" className="border-border bg-background">
                {formatSeconds(span.duration)}
              </Badge>
            </div>

            <Field label="节点名称" value={span.name} />
            <Field label="Span ID" value={span.span_id} mono />
            <Field label="开始时间" value={formatTimestamp(span.start_time)} />

            {span.status === 'error' && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
                <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>该节点执行异常，请查看输出数据或元数据。</span>
              </div>
            )}

            {decision && (
              <div>
                <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <GitFork className="h-3.5 w-3.5 text-primary" />
                  <span>决策点</span>
                </div>
                <DecisionCard decision={decision} />
              </div>
            )}

            {span.input_data && (
              <Payload title="输入数据" icon={<ArrowRightLeft className="h-3.5 w-3.5 text-sky-400" />} data={span.input_data} />
            )}
            {span.output_data && (
              <Payload title="输出数据" icon={<FileJson className="h-3.5 w-3.5 text-green-400" />} data={span.output_data} />
            )}
            {span.metadata && (
              <Payload title="元数据" icon={<Info className="h-3.5 w-3.5 text-yellow-400" />} data={span.metadata} />
            )}
          </div>
        </ScrollArea>
      </aside>
    </div>
  )
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <div className={`break-all text-sm ${mono ? 'font-mono text-xs' : 'font-medium'}`}>{value || '-'}</div>
    </div>
  )
}

function Payload({ title, icon, data }: { title: string; icon: React.ReactNode; data: unknown }) {
  const content = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  const copy = async () => {
    await navigator.clipboard.writeText(content)
  }
  const fields = typeof data === 'object' && data !== null && !Array.isArray(data)
    ? extractFields(data as Record<string, unknown>)
    : []

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          {icon}
          <span>{title}</span>
        </div>
        <Button variant="ghost" size="sm" onClick={copy}>
          <Copy className="mr-1 h-3.5 w-3.5" />
          复制
        </Button>
      </div>
      {fields.length > 0 ? (
        <FieldList fields={fields} />
      ) : (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background p-3 text-[12px] leading-relaxed text-foreground/85">
          {content}
        </pre>
      )}
    </div>
  )
}

function buildRounds(detail?: TraceDetail): TimelineRound[] {
  if (!detail) return []
  const roots = detail.spans || []
  return roots.map((root, index) => {
    const nodes = flattenWithDepth(root, 0, index)
    return {
      index,
      title: `${root.name}: ${root.span_id.slice(0, 10)}`,
      nodes,
      duration: nodes.reduce((sum, node) => sum + (node.duration || 0), 0),
      success: nodes.filter((node) => node.status !== 'error').length,
      error: nodes.filter((node) => node.status === 'error').length,
    }
  })
}

function flattenWithDepth(span: Span, depth: number, roundIndex: number): TimelineNode[] {
  return [
    { ...span, depth, roundIndex },
    ...(span.children || []).flatMap((child) => flattenWithDepth(child, depth + 1, roundIndex)),
  ]
}

function getVisiblePages(page: number, totalPages: number): Array<number | 'ellipsis'> {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, index) => index)
  }

  const pages = new Set<number>([0, totalPages - 1])
  for (let index = page - 1; index <= page + 1; index += 1) {
    if (index > 0 && index < totalPages - 1) {
      pages.add(index)
    }
  }

  const sorted = Array.from(pages).sort((a, b) => a - b)
  const visible: Array<number | 'ellipsis'> = []
  sorted.forEach((item, index) => {
    const previous = sorted[index - 1]
    if (index > 0 && item - previous > 1) {
      visible.push('ellipsis')
    }
    visible.push(item)
  })
  return visible
}

function summarizeSpan(span: Span): string {
  const output = span.output_data || {}
  const input = span.input_data || {}
  const metadata = span.metadata || {}

  const fields = [
    output.category,
    output.priority,
    output.answer,
    output.result,
    output.final_answer,
    output.processing_result,
    output.review_score != null ? `评分 ${output.review_score}` : undefined,
    output.query,
    input.content,
    input.query,
    metadata.tool_name,
    metadata.iteration != null ? `第 ${metadata.iteration} 次推理` : undefined,
  ]
    .filter((item): item is string | number => item !== undefined && item !== null && item !== '')
    .map(String)

  if (fields.length > 0) {
    return truncateText(fields.join(' · '), 42)
  }

  if (span.name.includes('classify')) return '识别工单分类、优先级与处理路径'
  if (span.name.includes('process')) return '结合上下文与知识库生成处理方案'
  if (span.name.includes('review')) return '复核处理结果质量并判断是否重试'
  if (span.name.includes('receive')) return '接收用户工单并初始化处理状态'
  if (span.span_type === 'llm_call') return '调用大模型生成推理或回复内容'
  if (span.span_type === 'tool_call') return '调用外部工具获取支撑信息'
  if (span.span_type === 'react_iter') return '执行 ReAct 思考、行动与观察循环'
  return '执行工作流节点'
}

function formatSeconds(value: number | null | undefined): string {
  if (value == null) return '-'
  if (value < 1) return `${Math.round(value * 1000)}ms`
  return `${value.toFixed(2)}s`
}

function formatMilliseconds(value: number | null | undefined): string {
  if (value == null) return '-'
  return `${Math.round(value * 1000)}ms`
}

function formatTimestamp(value: number | null | undefined): string {
  if (!value) return '-'
  return new Date(value * 1000).toLocaleString('zh-CN')
}

function truncateText(value: string, maxLength: number): string {
  return value.length > maxLength ? `${value.slice(0, maxLength)}...` : value
}
