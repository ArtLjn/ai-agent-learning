// D-01 Trace 决策树主页面（shadcn/ui 重写）。
// 设计来源：13 号文档第 4 节。
// 范围：工单 ID 输入 + span 树形展示 + 点击 span 弹 Sheet 看详情含决策五元组。

import { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Search as SearchIcon,
} from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import type { AdminSpanDecision, AdminTraceDetail, Span } from '@/types'

// 决策类型徽章颜色（详见 13 号文档第 4.3 节）
const DECISION_TYPE_COLOR: Record<string, string> = {
  routing: 'bg-blue-500/15 text-blue-300 border-blue-500/30',
  branching: 'bg-violet-500/15 text-violet-300 border-violet-500/30',
  tool_selection: 'bg-slate-500/15 text-slate-300 border-slate-500/30',
  quality_gate: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30',
  boundary: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  escalation: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
}

// 置信度色阶
function confidenceClass(conf: number | null | undefined): string {
  if (conf == null) return 'text-muted-foreground'
  if (conf < 0.5) return 'text-rose-400'
  if (conf < 0.7) return 'text-amber-400'
  if (conf < 0.9) return 'text-yellow-300'
  return 'text-emerald-400'
}

function statusBadgeClass(status: string | null | undefined): string {
  if (status === 'ok') return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
  if (status === 'error') return 'bg-rose-500/15 text-rose-300 border-rose-500/30'
  if (status === 'fallback') return 'bg-amber-500/15 text-amber-300 border-amber-500/30'
  return 'bg-muted text-muted-foreground'
}

function formatDuration(duration: number | null | undefined): string {
  if (duration == null) return '-'
  if (duration < 1) return `${Math.round(duration * 1000)}ms`
  return `${duration.toFixed(2)}s`
}

export default function SpanTreeView() {
  const [ticketInput, setTicketInput] = useState('')
  const [activeTicketId, setActiveTicketId] = useState<string | null>(null)
  const [trace, setTrace] = useState<AdminTraceDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null)
  const [spanDetailLoading, setSpanDetailLoading] = useState(false)

  useEffect(() => {
    if (!activeTicketId) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setLoading(true)
      setError(null)
      api
        .getAdminTrace(activeTicketId)
        .then((data) => {
          if (!cancelled) setTrace(data)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          if (err instanceof ApiError && err.status === 404) {
            setError(`未找到工单 ${activeTicketId} 的 trace`)
          } else {
            setError(err instanceof Error ? err.message : '加载失败')
          }
          setTrace(null)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    })
    return () => {
      cancelled = true
    }
  }, [activeTicketId])

  function handleSearch() {
    const trimmed = ticketInput.trim()
    if (!trimmed) return
    setActiveTicketId(trimmed)
    setSelectedSpan(null)
  }

  function handleSpanClick(spanId: string) {
    if (!activeTicketId) return
    setSpanDetailLoading(true)
    setSelectedSpan(null)
    api
      .getAdminSpanDetail(activeTicketId, spanId)
      .then((data) => setSelectedSpan(data))
      .catch(() => setSelectedSpan(null))
      .finally(() => setSpanDetailLoading(false))
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <header>
        <h1 className="text-xl font-semibold">Trace 决策树</h1>
        <p className="text-sm text-muted-foreground">
          输入工单 ID 查看 LangGraph 完整执行轨迹与决策点
        </p>
      </header>

      <Card>
        <CardContent className="flex flex-wrap gap-2">
          <Input
            className="max-w-md"
            placeholder="TK-20260704-XXXX"
            value={ticketInput}
            onChange={(e) => setTicketInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSearch()
            }}
          />
          <Button onClick={handleSearch} disabled={loading || !ticketInput.trim()}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <SearchIcon className="mr-2 h-4 w-4" />}
            查询
          </Button>
          {trace && (
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <Badge variant="outline">trace: {trace.trace_id}</Badge>
              <Badge variant="outline">status: {trace.status}</Badge>
              <Badge variant="outline">tokens: {trace.total_tokens}</Badge>
              <Badge variant="outline">决策点: {trace.decision_count}</Badge>
            </div>
          )}
        </CardContent>
      </Card>

      {error && (
        <Card className="border-rose-500/40">
          <CardContent className="text-sm text-rose-300">{error}</CardContent>
        </Card>
      )}

      {loading && <Skeleton className="h-64 w-full" />}

      {!loading && trace && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          {/* 左 + 中：span 树 */}
          <Card className="xl:col-span-2">
            <CardHeader>
              <CardTitle>Span 树（点击查看详情）</CardTitle>
            </CardHeader>
            <CardContent>
              <SpanTreeRenderer
                spans={trace.spans}
                decisions={trace.decisions}
                onSelect={handleSpanClick}
              />
            </CardContent>
          </Card>

          {/* 右：决策时间线 */}
          <Card>
            <CardHeader>
              <CardTitle>决策时间线</CardTitle>
            </CardHeader>
            <CardContent>
              <DecisionTimeline
                decisions={trace.decisions}
                onSelect={handleSpanClick}
              />
            </CardContent>
          </Card>
        </div>
      )}

      {/* Span 详情 Sheet */}
      <Sheet
        open={!!selectedSpan || spanDetailLoading}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedSpan(null)
            setSpanDetailLoading(false)
          }
        }}
      >
        <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
          {spanDetailLoading ? (
            <div className="flex h-full items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : selectedSpan ? (
            <SpanDetailPanel span={selectedSpan} />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  )
}

function SpanTreeRenderer({
  spans,
  decisions,
  onSelect,
}: {
  spans: Span[]
  decisions: AdminSpanDecision[]
  onSelect: (spanId: string) => void
}) {
  const decisionBySpan = useMemo(() => {
    const m = new Map<string, AdminSpanDecision>()
    decisions.forEach((d) => m.set(d.span_id, d))
    return m
  }, [decisions])
  if (spans.length === 0) {
    return <EmptyBlock description="该 trace 没有 span 数据" />
  }
  return (
    <div className="space-y-0.5">
      {spans.map((root) => (
        <SpanNode
          key={root.span_id}
          span={root}
          decision={decisionBySpan.get(root.span_id)}
          depth={0}
          onSelect={onSelect}
        />
      ))}
    </div>
  )
}

function SpanNode({
  span,
  decision,
  depth,
  onSelect,
}: {
  span: Span
  decision: AdminSpanDecision | undefined
  depth: number
  onSelect: (spanId: string) => void
}) {
  const [expanded, setExpanded] = useState(true)
  const hasChildren = (span.children?.length ?? 0) > 0
  return (
    <div>
      <div
        className="flex items-center gap-1 rounded px-1.5 py-1 hover:bg-muted/60 cursor-pointer"
        style={{ marginLeft: depth * 12 }}
        onClick={() => onSelect(span.span_id)}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setExpanded((v) => !v)
            }}
            className="text-muted-foreground hover:text-foreground"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="inline-block w-3.5" />
        )}
        <span className="text-sm font-mono">{span.name}</span>
        <Badge variant="outline" className="ml-1 text-[10px]">
          {span.span_type}
        </Badge>
        <Badge variant="outline" className={`text-[10px] ${statusBadgeClass(span.status)}`}>
          {span.status}
        </Badge>
        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
          {formatDuration(span.duration)}
        </span>
        {decision && (
          <Badge
            variant="outline"
            className={`text-[10px] ${DECISION_TYPE_COLOR[decision.decision_type ?? ''] ?? ''}`}
          >
            {decision.decision_type}
          </Badge>
        )}
      </div>
      {expanded &&
        hasChildren &&
        span.children.map((child) => (
          <SpanNode
            key={child.span_id}
            span={child}
            decision={undefined}
            depth={depth + 1}
            onSelect={onSelect}
          />
        ))}
    </div>
  )
}

function DecisionTimeline({
  decisions,
  onSelect,
}: {
  decisions: AdminSpanDecision[]
  onSelect: (spanId: string) => void
}) {
  if (decisions.length === 0) {
    return <EmptyBlock description="该 trace 无决策点埋点" />
  }
  return (
    <ol className="relative space-y-3 border-l border-border pl-4">
      {decisions.map((d, idx) => (
        <li key={`${d.span_id}-${idx}`} className="relative">
          <span
            className={`absolute -left-[21px] top-1.5 h-3 w-3 rounded-full border-2 border-background ${
              DECISION_TYPE_COLOR[d.decision_type ?? '']
                ?.split(' ')
                .find((c) => c.startsWith('bg-')) ?? 'bg-muted'
            }`}
          />
          <button
            type="button"
            onClick={() => onSelect(d.span_id)}
            className="w-full text-left"
          >
            <div className="flex items-center gap-2">
              <span className="text-sm font-mono">{d.span_name}</span>
              <Badge variant="outline" className={`text-[10px] ${DECISION_TYPE_COLOR[d.decision_type ?? ''] ?? ''}`}>
                {d.decision_type}
              </Badge>
            </div>
            <div className="text-xs text-muted-foreground">
              选: <span className="font-medium text-foreground">{d.selection_value ?? '-'}</span>{' '}
              <span className={confidenceClass(d.confidence)}>
                置信度 {d.confidence != null ? d.confidence.toFixed(2) : '-'}
              </span>{' '}
              · {d.options_count} 候选
            </div>
            {d.reason && (
              <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
                {d.reason}
              </div>
            )}
          </button>
        </li>
      ))}
    </ol>
  )
}

function SpanDetailPanel({ span }: { span: Span }) {
  const metadata = (span.metadata ?? {}) as Record<string, unknown>
  const decision = metadata['decision'] as
    | {
        decision_type?: string
        trigger?: Record<string, unknown>
        options?: Array<{ value: string; score: number; reason?: string }>
        selection?: { value?: string; confidence?: number; reason?: string }
        execution?: Record<string, unknown>
      }
    | undefined
  const tokenUsage = metadata['token_usage'] as
    | { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; model?: string }
    | undefined
  const ragStats = metadata['rag_stats'] as
    | { hit_count?: number; top_score?: number; mode?: string }
    | undefined

  return (
    <>
      <SheetHeader>
        <SheetTitle className="font-mono">{span.name}</SheetTitle>
        <SheetDescription>
          {span.span_type} · {span.status} · {formatDuration(span.duration)}
        </SheetDescription>
      </SheetHeader>

      <div className="mt-4 space-y-4">
        {/* 基本信息 */}
        <Section title="基本信息">
          <Field label="span_id" value={span.span_id} mono />
          <Field label="parent_span_id" value={span.parent_span_id ?? '-'} mono />
          <Field label="span_type" value={span.span_type} />
          <Field label="start_time" value={formatTimestamp(span.start_time)} />
          <Field label="end_time" value={formatTimestamp(span.end_time)} />
        </Section>

        {/* 决策五元组（高亮） */}
        {decision && (
          <Section title="决策五元组（metadata.decision）" highlight>
            <Field label="decision_type" value={decision.decision_type ?? '-'} />
            {decision.trigger && (
              <Field
                label="trigger"
                value={JSON.stringify(decision.trigger, null, 2)}
                mono
                block
              />
            )}
            {decision.options && decision.options.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">options</div>
                <div className="space-y-1">
                  {decision.options.map((opt, i) => (
                    <div
                      key={i}
                      className="rounded border border-border/50 px-2 py-1 text-xs"
                    >
                      <div className="flex items-center justify-between">
                        <span className="font-mono">{opt.value}</span>
                        <span className="tabular-nums">{opt.score.toFixed(2)}</span>
                      </div>
                      {opt.reason && (
                        <div className="text-muted-foreground">{opt.reason}</div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {decision.selection && (
              <div className="space-y-1">
                <div className="text-xs text-muted-foreground">selection</div>
                <div className="rounded border border-emerald-500/30 bg-emerald-500/5 px-2 py-1.5 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="font-mono">{decision.selection.value ?? '-'}</span>
                    <span className={`tabular-nums ${confidenceClass(decision.selection.confidence)}`}>
                      {decision.selection.confidence != null
                        ? decision.selection.confidence.toFixed(2)
                        : '-'}
                    </span>
                  </div>
                  {decision.selection.reason && (
                    <div className="mt-0.5 text-muted-foreground">
                      {decision.selection.reason}
                    </div>
                  )}
                </div>
              </div>
            )}
            {decision.execution && (
              <Field
                label="execution"
                value={JSON.stringify(decision.execution, null, 2)}
                mono
                block
              />
            )}
          </Section>
        )}

        {/* Token 使用 */}
        {tokenUsage && (
          <Section title="Token 使用（metadata.token_usage）">
            <Field label="model" value={tokenUsage.model ?? '-'} />
            <Field label="prompt_tokens" value={String(tokenUsage.prompt_tokens ?? 0)} mono />
            <Field
              label="completion_tokens"
              value={String(tokenUsage.completion_tokens ?? 0)}
              mono
            />
            <Field label="total_tokens" value={String(tokenUsage.total_tokens ?? 0)} mono />
          </Section>
        )}

        {/* RAG 统计 */}
        {ragStats && (
          <Section title="RAG 检索统计（metadata.rag_stats）">
            <Field label="hit_count" value={String(ragStats.hit_count ?? 0)} mono />
            <Field
              label="top_score"
              value={ragStats.top_score != null ? ragStats.top_score.toFixed(3) : '-'}
              mono
            />
            <Field label="mode" value={ragStats.mode ?? '-'} />
          </Section>
        )}

        {/* 输入输出 */}
        <Section title="输入数据">
          <pre className="overflow-x-auto rounded bg-muted/60 p-2 text-xs">
            {JSON.stringify(span.input_data ?? {}, null, 2)}
          </pre>
        </Section>
        <Section title="输出数据">
          <pre className="overflow-x-auto rounded bg-muted/60 p-2 text-xs">
            {JSON.stringify(span.output_data ?? {}, null, 2)}
          </pre>
        </Section>
      </div>
    </>
  )
}

function Section({
  title,
  highlight,
  children,
}: {
  title: string
  highlight?: boolean
  children: React.ReactNode
}) {
  return (
    <div
      className={
        highlight
          ? 'rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3'
          : 'rounded-lg border border-border/60 p-3'
      }
    >
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  )
}

function Field({
  label,
  value,
  mono,
  block,
}: {
  label: string
  value: string
  mono?: boolean
  block?: boolean
}) {
  return (
    <div className={block ? 'flex flex-col gap-1' : 'flex items-baseline gap-2'}>
      <span className="min-w-32 text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-sm ${mono ? 'font-mono' : ''} ${block ? 'whitespace-pre-wrap break-words' : 'truncate'}`}
      >
        {value}
      </span>
    </div>
  )
}

function formatTimestamp(value: number | null | undefined): string {
  if (value == null) return '-'
  if (value > 1e9) {
    // epoch seconds
    return new Date(value * 1000).toISOString()
  }
  return String(value)
}

function EmptyBlock({ description }: { description: string }) {
  return (
    <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
      {description}
    </div>
  )
}
