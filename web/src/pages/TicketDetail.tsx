import { useCallback, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useParams, useNavigate } from 'react-router-dom'
import { useTicket, useTicketTrace, useTraceDecisions } from '@/hooks/useApi'
import { useWebSocket } from '@/hooks/useWebSocket'
import { api, type AuthState } from '@/lib/api'
import { toast } from '@/lib/toast'
import type { Span, TicketMessage, TraceDetail, TraceDecisionsResponse, WSMessage } from '@/types'
import { buildKnowledgeSearchParams } from '@/lib/knowledgeReference'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge, CategoryBadge, PriorityBadge } from '@/components/layout/StatusBadge'
import { TraceGantt } from '@/components/trace/TraceGantt'
import { SpanDetailSheet } from '@/components/trace/SpanDetailSheet'
import { DecisionTimeline } from '@/components/trace/DecisionTimeline'
import { LiveExecutionFlow } from '@/components/trace/LiveExecutionFlow'
import { Markdown } from '@/components/ui/markdown'
import { formatDuration } from '@/components/trace/spanTypes'
import {
  ArrowLeft, MessageSquare, Brain, Clock, Layers, Bot, Wrench,
  BookOpen, ExternalLink, Activity, Zap, GitFork, Send, ShieldCheck, RotateCcw,
  ThumbsDown, ThumbsUp,
} from 'lucide-react'

export function TicketDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [auth, setAuth] = useState<AuthState | null>(null)
  const { data: ticket, isLoading, refetch: refetchTicket } = useTicket(id!, true)
  const ticketStatus = ticket?.status
  const isRunning = !!ticketStatus && !['completed', 'failed'].includes(ticketStatus)
  const role = auth?.role
  const isDeveloper = role === 'developer'
  const { data: trace } = useTicketTrace(id!, isRunning, isDeveloper)
  const traceDetail = trace as TraceDetail | undefined
  const traceId = traceDetail?.trace_id || ''
  const { data: decisionsResp } = useTraceDecisions(traceId)
  const { data: ticketMessages = [] } = useQuery({
    queryKey: ['ticketMessages', id],
    queryFn: () => api.getTicketMessages(id!),
    enabled: !!id,
  })
  const decisions = (decisionsResp as TraceDecisionsResponse | undefined)?.decisions || []
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const [reply, setReply] = useState('')

  useEffect(() => {
    let alive = true
    api
      .getAuthState()
      .then((state) => {
        if (alive) setAuth(state)
      })
      .catch(() => {
        if (alive) setAuth(null)
      })
    return () => {
      alive = false
    }
  }, [])

  const submitMessage = useMutation({
    mutationFn: (content: string) => api.createTicketMessage(id!, {
      content,
      sender_id: 'user-001',
    }),
    onSuccess: () => {
      setReply('')
      qc.invalidateQueries({ queryKey: ['ticketMessages', id] })
      qc.invalidateQueries({ queryKey: ['ticket', id] })
      qc.invalidateQueries({ queryKey: ['tickets'] })
      qc.invalidateQueries({ queryKey: ['ticketTrace', id] })
    },
  })

  const submitFeedback = useMutation({
    mutationFn: (satisfied: boolean) => api.submitFeedback(id!, satisfied),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['ticket', id] })
      qc.invalidateQueries({ queryKey: ['tickets'] })
      if (result.satisfied) {
        toast.success('已记录反馈', '感谢确认，本次服务已完成闭环')
      } else {
        toast.warning('已升级人工审核', '系统已将该工单转入人工复核队列')
      }
    },
    onError: (error) => {
      toast.error('反馈提交失败', error instanceof Error ? error.message : '请稍后重试')
    },
  })

  const refreshTicketSnapshot = useCallback((msg: WSMessage) => {
    if (!id || msg.ticket_id !== id) return
    qc.invalidateQueries({ queryKey: ['ticket', id] })
    qc.invalidateQueries({ queryKey: ['tickets'] })
    qc.invalidateQueries({ queryKey: ['ticketTrace', id] })
    qc.invalidateQueries({ queryKey: ['ticketMessages', id] })
    if (traceId) {
      qc.invalidateQueries({ queryKey: ['traceDecisions', traceId] })
    }

    if (['completed', 'failed'].includes(msg.status) || msg.node === 'complete') {
      void refetchTicket()
    }
  }, [id, qc, refetchTicket, traceId])

  useWebSocket(refreshTicketSnapshot)

  const traceStats = useMemo(() => buildTraceOverviewStats(traceDetail), [traceDetail])

  const handleSelectSpan = (span: Span) => {
    setSelectedSpan(span)
    setSheetOpen(true)
  }

  if (isLoading || !ticket) {
    return <DetailSkeleton />
  }

  const reviewPassed = ticket.status === 'completed'
    && Boolean(ticket.processing_result)
    && (ticket.review_score == null || ticket.review_score >= 0.7)
  const waitingForUserInput = ticket.status === 'waiting_user_input'
  const canShowProcessingResult = Boolean(ticket.processing_result)
    && (reviewPassed || waitingForUserInput)
  const reviewFailed = ticket.review_score != null && ticket.review_score < 0.7
  const resultLabel = reviewPassed ? '最终处理结果' : waitingForUserInput ? '待补充说明' : '处理结果'

  // 构建消息链
  const messages: { role: string; content: string }[] = []
  if (ticket.status) messages.push({ role: 'system', content: `工单创建，状态: ${ticket.status}` })
  if (ticket.category) messages.push({ role: 'classifier', content: `分类结果: ${ticket.category} | 优先级: ${ticket.priority || '-'}` })
  if (ticket.processing_result) messages.push({ role: 'processor', content: ticket.processing_result })
  if (ticket.references?.length) messages.push({ role: 'knowledge', content: `已引用 ${ticket.references.length} 条知识库片段` })
  if (ticket.review_score != null) messages.push({ role: 'reviewer', content: `审核评分: ${ticket.review_score.toFixed(2)} ${ticket.review_score >= 0.7 ? '(通过)' : '(未通过)'}` })
  if (ticket.error) messages.push({ role: 'coordinator', content: `错误: ${ticket.error}` })
  if (ticket.retry_count > 0) messages.push({ role: 'system', content: `已重试 ${ticket.retry_count} 次` })

  const buildKnowledgeHref = (reference: string) => `/knowledge?${buildKnowledgeSearchParams(reference).toString()}`

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => navigate('/tickets')}>
          <ArrowLeft className="w-4 h-4 mr-1" />
          返回
        </Button>
        <div>
          <h2 className="text-xl font-semibold font-mono">{ticket.ticket_id}</h2>
          <p className="text-sm text-muted-foreground">工单详情</p>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-6">
        {/* 左侧：工单上下文 + 过程记录 */}
        <div className={isDeveloper ? 'col-span-5 space-y-4' : 'col-span-12 space-y-4'}>
          {/* 基本信息卡片 */}
          <Card className="bg-card border-border">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">工单信息</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <InfoField label="状态"><StatusBadge status={ticket.status} /></InfoField>
                <InfoField label="分类">{ticket.category ? <CategoryBadge category={ticket.category} /> : '-'}</InfoField>
                <InfoField label="优先级">{ticket.priority ? <PriorityBadge priority={ticket.priority} /> : '-'}</InfoField>
                <InfoField label="重试次数">
                  <span className="text-sm font-mono">{ticket.retry_count} / 3</span>
                </InfoField>
              </div>
              <Separator className="bg-border" />
              <InfoField label="工单内容">
                <div className="mt-1">
                  <Markdown>{ticket.content}</Markdown>
                </div>
              </InfoField>
              <Separator className="bg-border" />
              <InfoField label={resultLabel}>
                <div className="mt-1">
                  {canShowProcessingResult && ticket.processing_result
                    ? <Markdown>{ticket.processing_result}</Markdown>
                    : (
                      <ReviewGatePlaceholder
                        status={ticket.status}
                        hasDraft={Boolean(ticket.processing_result)}
                        reviewFailed={reviewFailed}
                      />
                    )}
                </div>
              </InfoField>
              {ticket.review_score != null && (
                <>
                  <Separator className="bg-border" />
                  <div className="grid grid-cols-2 gap-4">
                    <InfoField label="审核评分">
                      <span className={`text-lg font-bold ${ticket.review_score >= 0.7 ? 'text-success' : 'text-warning'}`}>
                        {ticket.review_score.toFixed(2)}
                      </span>
                    </InfoField>
                    <InfoField label="审核结果">
                      <Badge variant="outline" className={`border-0 ${ticket.review_score >= 0.7 ? 'bg-success/15 text-success' : 'bg-warning/15 text-warning'}`}>
                        {ticket.review_score >= 0.7 ? '通过' : '未通过'}
                      </Badge>
                    </InfoField>
                  </div>
                </>
              )}
              {ticket.references?.length > 0 && (
                <>
                  <Separator className="bg-border" />
                  <InfoField label="知识库参考">
                    <div className="mt-2 space-y-1.5">
                      {ticket.references.map((ref: string, index: number) => {
                        return (
                          <button
                            key={index}
                            type="button"
                            onClick={() => navigate(buildKnowledgeHref(ref))}
                            className="group block w-full text-left rounded-md border border-border bg-background px-2.5 py-1.5 transition-colors hover:border-primary/50 hover:bg-primary/5"
                            title="点击在知识库中检索相关文档"
                          >
                            <div className="flex items-start gap-2">
                              <BookOpen className="mt-0.5 h-3 w-3 shrink-0 text-primary" />
                              <p className="flex-1 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground group-hover:text-foreground">
                                {ref}
                              </p>
                              <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground/50 opacity-0 transition-opacity group-hover:opacity-100" />
                            </div>
                          </button>
                        )
                      })}
                    </div>
                  </InfoField>
                </>
              )}
            </CardContent>
          </Card>

          <ProcessLogCard
            agentMessages={messages}
            messages={ticketMessages}
            waitingForUser={waitingForUserInput}
            reply={reply}
            onReplyChange={setReply}
            onSubmit={() => {
              const content = reply.trim()
              if (content) submitMessage.mutate(content)
            }}
            submitting={submitMessage.isPending}
            error={submitMessage.error instanceof Error ? submitMessage.error.message : null}
          />

          <FeedbackCard
            status={ticket.status}
            satisfied={ticket.satisfied}
            canSubmit={role === 'user'}
            submitting={submitFeedback.isPending}
            onSubmit={(satisfied) => submitFeedback.mutate(satisfied)}
          />
        </div>

        {isDeveloper && (
          <TraceSection
            ticketId={ticket.ticket_id}
            ticketStatus={ticket.status}
            traceDetail={traceDetail}
            traceStats={traceStats}
            decisions={decisions}
            onSelectSpan={handleSelectSpan}
          />
        )}
      </div>

      {isDeveloper && (
        <SpanDetailSheet
          span={selectedSpan}
          open={sheetOpen}
          onOpenChange={setSheetOpen}
        />
      )}
    </div>
  )
}

function TraceSection({
  ticketId,
  ticketStatus,
  traceDetail,
  traceStats,
  decisions,
  onSelectSpan,
}: {
  ticketId: string
  ticketStatus: string
  traceDetail?: TraceDetail
  traceStats: TraceOverviewStats
  decisions: TraceDecisionsResponse['decisions']
  onSelectSpan: (span: Span) => void
}) {
  return (
    <div className="col-span-7 space-y-4">
      {/* 实时执行流（演示用：agent 工作过程动态展示） */}
      <LiveExecutionFlow
        ticketId={ticketId}
        spans={traceDetail?.spans || []}
        isRunning={!['completed', 'failed'].includes(ticketStatus)}
      />

      {traceDetail ? (
        <>
          {/* 决策链概览 */}
          <Card className="bg-card border-border">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Brain className="w-4 h-4 text-primary" />
                决策链概览
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                <MiniStat icon={<Clock className="h-4 w-4" />} label="总耗时" value={formatDuration(traceStats.totalDuration)} />
                <MiniStat icon={<Layers className="h-4 w-4" />} label="节点数" value={traceStats.nodeCount} />
                <MiniStat icon={<Bot className="h-4 w-4" />} label="LLM 调用" value={traceStats.llmCalls} />
                <MiniStat icon={<Wrench className="h-4 w-4" />} label="工具调用" value={traceStats.toolCalls} />
                <MiniStat icon={<Zap className="h-4 w-4" />} label="ReAct 推理" value={traceStats.reactIters} />
              </div>
              {(traceStats.slowestSpan || traceStats.errorCount > 0) && (
                <div className="mt-3 grid grid-cols-2 gap-3">
                  {traceStats.slowestSpan && (
                    <div className="rounded-md border border-border bg-background p-3 text-xs">
                      <p className="text-muted-foreground mb-0.5">最耗时节点</p>
                      <p className="font-medium text-foreground truncate">{traceStats.slowestSpan.name}</p>
                      <p className="font-mono text-primary mt-0.5">{formatDuration(traceStats.slowestSpan.duration)}</p>
                    </div>
                  )}
                  <div className="rounded-md border border-border bg-background p-3 text-xs">
                    <p className="text-muted-foreground mb-0.5">错误节点</p>
                    <p className={`font-medium ${traceStats.errorCount > 0 ? 'text-destructive' : 'text-success'}`}>
                      {traceStats.errorCount > 0 ? `${traceStats.errorCount} 个失败` : '全部成功'}
                    </p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* 决策链甘特图（核心） */}
          <Card className="bg-card border-border">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Activity className="w-4 h-4 text-primary" />
                决策链时间线
                <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                  点击节点查看完整输入/输出
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="max-h-[720px] overflow-y-auto pr-2">
                <TraceGantt
                  spans={traceDetail.spans || []}
                  onSelect={onSelectSpan}
                />
              </div>
            </CardContent>
          </Card>

          {decisions.length > 0 && (
            <Card className="bg-card border-border">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm flex items-center gap-2">
                  <GitFork className="w-4 h-4 text-primary" />
                  决策点明细
                  <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                    每个分岔路口 AI 的候选与选择
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="max-h-[520px] overflow-y-auto pr-2">
                  <DecisionTimeline decisions={decisions} />
                </div>
              </CardContent>
            </Card>
          )}
        </>
      ) : (
        <Card className="bg-card border-border">
          <CardContent className="py-16 text-center">
            <Brain className="h-10 w-10 mx-auto mb-3 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">该工单暂无 Trace 数据</p>
            <p className="text-xs text-muted-foreground/70 mt-1">
              工单被处理后将自动记录决策链
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function InfoField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">{label}</p>
      {children}
    </div>
  )
}

function MiniStat({ icon, label, value }: { icon: React.ReactNode; label: string; value: string | number }) {
  return (
    <div className="bg-background rounded-md p-2.5 border border-border">
      <div className="mb-1 text-primary">{icon}</div>
      <p className="text-base font-bold text-primary tabular-nums">{value}</p>
      <p className="text-[10px] text-muted-foreground">{label}</p>
    </div>
  )
}

function ReviewGatePlaceholder({
  status,
  hasDraft,
  reviewFailed,
}: {
  status: string
  hasDraft: boolean
  reviewFailed: boolean
}) {
  const isReviewing = status === 'reviewing'
  const isWaitingForUser = status === 'waiting_user_input'
  const isReworking = reviewFailed || (hasDraft && ['processing', 'reviewing'].includes(status))

  return (
    <div className={`rounded-md border px-3 py-3 text-sm ${
      isReworking
        ? 'border-warning/30 bg-warning/5 text-warning'
        : 'border-border bg-background text-muted-foreground'
    }`}>
      <div className="flex items-center gap-2">
        {isReworking ? (
          <RotateCcw className="h-4 w-4" />
        ) : (
          <ShieldCheck className="h-4 w-4 text-primary" />
        )}
        <span className="font-medium">
          {isReviewing
            ? '质量审核中，暂不展示最终答复'
            : isWaitingForUser
            ? 'Agent 已识别知识盲区，等待用户补充'
            : isReworking
            ? 'Reviewer 已进入打回/返工检查'
            : '等待 Agent 生成处理方案'}
        </span>
      </div>
      {hasDraft && !isWaitingForUser && (
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          Processor 已产出处理草稿，但需要 Reviewer 质量门禁通过后才会作为最终处理结果展示。
        </p>
      )}
      {isWaitingForUser && (
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          补充信息提交后，Agent 会结合新上下文重新检索知识库并继续处理。
        </p>
      )}
    </div>
  )
}

function ProcessLogCard({
  agentMessages,
  messages,
  waitingForUser,
  reply,
  onReplyChange,
  onSubmit,
  submitting,
  error,
}: {
  agentMessages: { role: string; content: string }[]
  messages: TicketMessage[]
  waitingForUser: boolean
  reply: string
  onReplyChange: (value: string) => void
  onSubmit: () => void
  submitting: boolean
  error: string | null
}) {
  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-primary" />
          工单过程记录
          <span className="ml-auto text-[11px] font-normal text-muted-foreground">
            沟通 {messages.length} 条 · Agent {agentMessages.length} 条
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <p className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">用户沟通</p>
          <div className="max-h-[220px] overflow-y-auto pr-1">
            {messages.length === 0 ? (
              <p className="rounded-md border border-dashed border-border py-6 text-center text-sm text-muted-foreground">
                暂无沟通记录
              </p>
            ) : (
              <div className="space-y-2">
                {messages.map((message) => (
                  <div key={message.message_id} className="rounded-md border border-border bg-background p-2.5">
                    <div className="mb-1 flex items-center gap-2">
                      <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                        {senderLabel(message.sender_type)}
                      </span>
                      <span className="text-[10px] text-muted-foreground">
                        {formatMessageTime(message.created_at)}
                      </span>
                    </div>
                    <Markdown>{message.content}</Markdown>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {waitingForUser && (
          <div className="space-y-2 rounded-md border border-warning/30 bg-warning/5 p-3">
            <p className="text-xs font-medium text-warning">待用户补充信息</p>
            <Textarea
              value={reply}
              onChange={(event) => onReplyChange(event.target.value)}
              placeholder="请输入订单号、支付流水号或其他补充说明..."
              rows={3}
              className="text-sm"
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={onSubmit}
                disabled={!reply.trim() || submitting}
              >
                <Send className="mr-1.5 h-3.5 w-3.5" />
                {submitting ? '提交中...' : '提交补充'}
              </Button>
              {error && <span className="text-xs text-destructive">{error}</span>}
            </div>
          </div>
        )}

        <Separator className="bg-border" />

        <div>
          <p className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">Agent 消息链</p>
          <div className="max-h-[360px] overflow-y-auto pr-1">
            {agentMessages.length === 0 ? (
              <p className="rounded-md border border-dashed border-border py-6 text-center text-sm text-muted-foreground">
                等待处理中...
              </p>
            ) : (
              <div className="space-y-2">
                {agentMessages.map((msg, index) => (
                  <div key={`${msg.role}-${index}`} className="rounded-md bg-background border border-border p-2.5">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] font-mono uppercase tracking-wide text-primary bg-primary/10 rounded px-1.5 py-0.5">
                        {msg.role}
                      </span>
                      <span className="text-[10px] text-muted-foreground/60">#{index + 1}</span>
                    </div>
                    <Markdown>{msg.content}</Markdown>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function FeedbackCard({
  status,
  satisfied,
  canSubmit,
  submitting,
  onSubmit,
}: {
  status: string
  satisfied?: boolean | number | null
  canSubmit: boolean
  submitting: boolean
  onSubmit: (satisfied: boolean) => void
}) {
  const completed = status === 'completed'
  const hasFeedback = satisfied !== undefined && satisfied !== null
  const wasSatisfied = satisfied === true || satisfied === 1

  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-primary" />
          结果反馈与升级
        </CardTitle>
      </CardHeader>
      <CardContent>
        {!completed ? (
          <p className="rounded-md border border-dashed border-border bg-background/40 px-3 py-4 text-sm text-muted-foreground">
            工单完成后可确认处理结果；如果不满意，系统会自动升级到人工审核。
          </p>
        ) : hasFeedback ? (
          <div className={`rounded-md border px-3 py-4 text-sm ${
            wasSatisfied
              ? 'border-success/30 bg-success/10 text-success'
              : 'border-warning/30 bg-warning/10 text-warning'
          }`}>
            {wasSatisfied
              ? '你已确认满意，本次服务闭环完成。'
              : '你已反馈不满意，工单已升级到人工复核队列。'}
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {canSubmit
                ? '请确认本次处理结果是否解决问题。不满意会触发人工复核。'
                : '该工单还没有用户满意度反馈。管理员和开发者只能查看反馈状态。'}
            </p>
            {canSubmit && (
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  onClick={() => onSubmit(true)}
                  disabled={submitting}
                >
                  <ThumbsUp className="h-4 w-4" />
                  满意，结束工单
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => onSubmit(false)}
                  disabled={submitting}
                >
                  <ThumbsDown className="h-4 w-4" />
                  不满意，升级人工
                </Button>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function senderLabel(sender: string) {
  const labels: Record<string, string> = {
    user: '用户',
    reviewer: '审核员',
    system: '系统',
    agent: 'Agent',
  }
  return labels[sender] || sender
}

function formatMessageTime(value: string) {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return value
  return time.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

interface TraceOverviewStats {
  totalDuration: number | null
  nodeCount: number
  llmCalls: number
  toolCalls: number
  reactIters: number
  errorCount: number
  slowestSpan: Span | null
}

function buildTraceOverviewStats(traceDetail?: TraceDetail): TraceOverviewStats {
  const topSpans = traceDetail?.spans || []
  const flatSpans = flattenSpans(topSpans)
  const slowestSpan = flatSpans.reduce<Span | null>((slowest, span) => {
    if (!slowest) return span
    return getDuration(span) > getDuration(slowest) ? span : slowest
  }, null)

  return {
    totalDuration: resolveTraceDuration(traceDetail, topSpans),
    nodeCount: Math.max(traceDetail?.node_count || 0, flatSpans.length),
    llmCalls: flatSpans.filter(isLlmSpan).length,
    toolCalls: flatSpans.filter(isToolSpan).length,
    reactIters: flatSpans.filter(isReactSpan).length,
    errorCount: flatSpans.filter(isErrorSpan).length,
    slowestSpan,
  }
}

function resolveTraceDuration(traceDetail: TraceDetail | undefined, topSpans: Span[]) {
  if (traceDetail?.duration != null) return traceDetail.duration

  if (traceDetail?.start_time != null && traceDetail.end_time != null) {
    const duration = traceDetail.end_time - traceDetail.start_time
    if (duration >= 0) return duration
  }

  const topLevelDuration = topSpans.reduce((sum, span) => sum + getDuration(span), 0)
  if (topLevelDuration > 0) return topLevelDuration

  const flatSpans = flattenSpans(topSpans)
  const timedDuration = resolveDurationFromSpanTimes(flatSpans)
  if (timedDuration != null) return timedDuration

  const maxSpanDuration = Math.max(0, ...flatSpans.map(getDuration))
  return maxSpanDuration > 0 ? maxSpanDuration : null
}

function resolveDurationFromSpanTimes(spans: Span[]) {
  const starts = spans.map((span) => span.start_time).filter(isFiniteNumber)
  const ends = spans.map((span) => span.end_time).filter(isFiniteNumber)
  if (starts.length === 0 || ends.length === 0) return null

  const duration = Math.max(...ends) - Math.min(...starts)
  return duration >= 0 ? duration : null
}

function getDuration(span: Span) {
  return span.duration ?? 0
}

function isLlmSpan(span: Span) {
  const type = span.span_type.toLowerCase()
  const name = span.name.toLowerCase()
  return type === 'llm_call' || name.includes('chat_completion') || name.includes('chat.completion') || name.includes('llm')
}

function isToolSpan(span: Span) {
  const type = span.span_type.toLowerCase()
  const name = span.name.toLowerCase()
  return type === 'tool_call' || name.includes('knowledge_search') || name.includes('search_knowledge') || name.includes('tool')
}

function isReactSpan(span: Span) {
  const type = span.span_type.toLowerCase()
  const name = span.name.toLowerCase()
  return type === 'react_iter' || name.includes('react_iter') || name.includes('react ')
}

function isErrorSpan(span: Span) {
  const status = span.status.toLowerCase()
  return status === 'error' || status === 'failed' || status === 'failure'
}

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function flattenSpans(spans: Span[]): Span[] {
  return spans.flatMap((span) => [span, ...flattenSpans(span.children || [])])
}

function DetailSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-60" />
      <div className="grid grid-cols-12 gap-6">
        <div className="col-span-5 space-y-4">
          <Skeleton className="h-64 rounded-lg" />
          <Skeleton className="h-48 rounded-lg" />
        </div>
        <div className="col-span-7 space-y-4">
          <Skeleton className="h-32 rounded-lg" />
          <Skeleton className="h-96 rounded-lg" />
        </div>
      </div>
    </div>
  )
}
