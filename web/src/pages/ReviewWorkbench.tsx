import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ShieldCheck } from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { useReviewQueue, useReviewDetail, useSubmitDecision } from '@/hooks/useReviews'
import { useReviewEvents } from '@/hooks/useReviewEvents'
import { api, type AuthState } from '@/lib/api'
import { toast } from '@/lib/toast'
import type { ReviewDecision, ReviewQueueItem as ReviewQueueItemType } from '@/types'
import { ReviewQueue } from '@/components/reviews/ReviewQueue'
import { ReviewDetailPanel } from '@/components/reviews/ReviewDetailPanel'
import { AIProcessingResultCard } from '@/components/reviews/AIProcessingResultCard'
import { AIAssistanceCard } from '@/components/reviews/AIAssistanceCard'
import { DecisionPanel } from '@/components/reviews/DecisionPanel'
import { canSubmitReviewDecision } from '@/lib/ticketDetailPermissions'

const DEFAULT_REVIEWER_ID = 'reviewer-001'

export function ReviewWorkbench() {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [reviewerId, setReviewerId] = useState<string>(DEFAULT_REVIEWER_ID)
  const [auth, setAuth] = useState<AuthState | null>(null)

  // 筛选器
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [triggerFilter, setTriggerFilter] = useState<string>('')
  const [categoryFilter, setCategoryFilter] = useState<string>('')
  const [priorityFilter, setPriorityFilter] = useState<string>('')

  const params = useMemo(
    () => ({
      status: statusFilter || undefined,
      trigger_type: triggerFilter || undefined,
      category: categoryFilter || undefined,
      priority: priorityFilter || undefined,
      limit: 50,
      offset: 0,
    }),
    [statusFilter, triggerFilter, categoryFilter, priorityFilter],
  )

  const queueQuery = useReviewQueue(params)
  const detailQuery = useReviewDetail(selectedId)
  const submitMutation = useSubmitDecision()
  const canDecide = canSubmitReviewDecision(auth?.role)

  const queue: ReviewQueueItemType[] = useMemo(
    () => queueQuery.data?.queue ?? [],
    [queueQuery.data],
  )

  // 自动选中第一个（仅当未选中且队列非空）
  const effectiveSelectedId = selectedId ?? queue[0]?.ticket_id ?? null
  const detail = detailQuery.data

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

  const onSelect = useCallback((ticketId: string) => {
    setSelectedId(ticketId)
  }, [])

  // WebSocket 事件处理
  const onReviewRequested = useCallback(
    (event: { ticket_id: string; trigger_type: string; priority: string | null }) => {
      toast.info('新审核请求', `${event.ticket_id.slice(0, 16)} · ${event.trigger_type}`)
      queueQuery.refetch()
    },
    [queueQuery],
  )

  const onReviewDecided = useCallback(
    (event: { ticket_id: string; decision: string; reviewer_id: string }) => {
      // 若是当前选中工单，刷新详情
      if (event.ticket_id === effectiveSelectedId) {
        detailQuery.refetch()
        toast.success(
          '审核已决策',
          `${event.ticket_id.slice(0, 16)} · ${event.decision} · by ${event.reviewer_id}`,
        )
      }
      // 队列通常已通过本次提交 invalidate；外部其他用户决策也同步刷新
      queueQuery.refetch()
    },
    [effectiveSelectedId, detailQuery, queueQuery],
  )

  useReviewEvents({ onReviewRequested, onReviewDecided })

  const handleSubmit = useCallback(
    async (decision: ReviewDecision, reason: string, rewritten?: string) => {
      if (!effectiveSelectedId) return
      if (!reviewerId.trim()) {
        toast.warning('请填写审核员 ID')
        return
      }
      try {
        const res = await submitMutation.mutateAsync({
          ticketId: effectiveSelectedId,
          body: {
            decision,
            decision_reason: reason,
            reviewer_id: reviewerId.trim(),
            ...(rewritten ? { rewritten_result: rewritten } : {}),
          },
        })
        if (decision === 'request_info') {
          toast.success('已请求用户补充信息', `下一节点：${res.next_node}`)
        } else {
          toast.success(
            '决策已提交',
            `下一节点：${res.next_node}${res.workflow_resumed ? '（工作流已恢复）' : ''}`,
          )
        }
        // 选中下一条待审核
        const next = queue.find((q: ReviewQueueItemType) => q.ticket_id !== effectiveSelectedId)
        setSelectedId(next?.ticket_id ?? null)
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        toast.error('决策提交失败', message)
      }
    },
    [effectiveSelectedId, queue, reviewerId, submitMutation],
  )

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <ShieldCheck className="h-5 w-5 text-primary" />
            审核工作台
          </h2>
          <p className="text-sm text-muted-foreground mt-0.5">人工介入审核与决策</p>
        </div>
      </div>

      <div className="grid grid-cols-12 items-start gap-4">
        {/* 左栏：筛选与待审队列 */}
        <aside className="col-span-3 flex min-h-0 flex-col gap-3">
          {/* 筛选器 */}
          <div className="rounded-lg border border-border bg-card p-3 space-y-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">筛选器</p>
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v === 'all' ? '' : (v ?? ''))}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="全部状态" />
              </SelectTrigger>
              <SelectContent className="bg-popover border-border">
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="pending_human_review">待人工审核</SelectItem>
                <SelectItem value="waiting_user_input">待员工补充</SelectItem>
              </SelectContent>
            </Select>
            <Select value={triggerFilter} onValueChange={(v) => setTriggerFilter(v === 'all' ? '' : (v ?? ''))}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="全部触发" />
              </SelectTrigger>
              <SelectContent className="bg-popover border-border">
                <SelectItem value="all">全部触发</SelectItem>
                <SelectItem value="escalate">升级</SelectItem>
                <SelectItem value="review_failed">复核未通过</SelectItem>
                <SelectItem value="error_fallback">错误兜底</SelectItem>
                <SelectItem value="user_request">用户请求</SelectItem>
              </SelectContent>
            </Select>
            <Select value={categoryFilter} onValueChange={(v) => setCategoryFilter(v === 'all' ? '' : (v ?? ''))}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="全部分类" />
              </SelectTrigger>
              <SelectContent className="bg-popover border-border">
                <SelectItem value="all">全部分类</SelectItem>
                <SelectItem value="technical">IT 与办公支持</SelectItem>
                <SelectItem value="billing">费用薪酬报销</SelectItem>
                <SelectItem value="complaint">风险投诉升级</SelectItem>
                <SelectItem value="inquiry">制度流程咨询</SelectItem>
              </SelectContent>
            </Select>
            <Select value={priorityFilter} onValueChange={(v) => setPriorityFilter(v === 'all' ? '' : (v ?? ''))}>
              <SelectTrigger className="h-8 text-xs">
                <SelectValue placeholder="全部优先级" />
              </SelectTrigger>
              <SelectContent className="bg-popover border-border">
                <SelectItem value="all">全部优先级</SelectItem>
                <SelectItem value="P0">P0</SelectItem>
                <SelectItem value="P1">P1</SelectItem>
                <SelectItem value="P2">P2</SelectItem>
                <SelectItem value="P3">P3</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="h-[calc(100vh-300px)] min-h-[420px] overflow-hidden">
            <ReviewQueue
              items={queue}
              selectedId={effectiveSelectedId}
              loading={queueQuery.isLoading}
              error={queueQuery.error}
              onSelect={onSelect}
              onRefresh={() => queueQuery.refetch()}
            />
          </div>
        </aside>

        {/* 右栏：审阅内容 + 决策操作 */}
        <section className="col-span-9">
          {!effectiveSelectedId ? (
            <EmptyState />
          ) : detailQuery.isLoading ? (
            <Skeleton className="h-96 rounded-lg" />
          ) : detailQuery.error ? (
            <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-6 text-sm text-destructive">
              加载详情失败：{detailQuery.error.message}
              <button
                type="button"
                className="ml-2 underline"
                onClick={() => detailQuery.refetch()}
              >
                重试
              </button>
            </div>
          ) : !detail ? (
            <EmptyState />
          ) : (
            <ol className="space-y-4">
              <ReviewStep
                index={1}
                title="核对工单材料"
                description="先确认用户原文、分类、优先级与触发审核原因。"
              >
                <ReviewDetailPanel detail={detail} />
              </ReviewStep>

              <ReviewStep
                index={2}
                title="查看 AI 处理与建议"
                description="对比 AI 处理结果和辅助建议，判断是否可采纳。"
              >
                <div className="grid grid-cols-2 items-start gap-4">
                  <AIProcessingResultCard
                    processingResult={detail.processing_result}
                    reviewScore={detail.review_score}
                  />
                  <AIAssistanceCard suggestion={detail.current_review?.ai_suggestion ?? null} />
                </div>
              </ReviewStep>

              <ReviewStep
                index={3}
                title="提交人工决策"
                description="选择通过、改写、重处理、请求补充或驳回，并填写决策理由。"
                isLast
              >
                <ServiceDeskDecisionAction
                  key={detail.ticket_id}
                  canSubmit={canDecide}
                  detail={detail}
                  reviewerId={reviewerId}
                  onReviewerIdChange={setReviewerId}
                  onSubmit={handleSubmit}
                  submitting={submitMutation.isPending}
                />
              </ReviewStep>
            </ol>
          )}
        </section>
      </div>
    </div>
  )
}

function ServiceDeskDecisionAction({
  canSubmit,
  detail,
  reviewerId,
  onReviewerIdChange,
  onSubmit,
  submitting,
}: Parameters<typeof DecisionPanel>[0] & { canSubmit: boolean }) {
  if (!canSubmit) {
    return null
  }

  return (
    <DecisionPanel
      detail={detail}
      reviewerId={reviewerId}
      onReviewerIdChange={onReviewerIdChange}
      onSubmit={onSubmit}
      submitting={submitting}
    />
  )
}

function ReviewStep({
  index,
  title,
  description,
  children,
  isLast = false,
}: {
  index: number
  title: string
  description: string
  children: ReactNode
  isLast?: boolean
}) {
  return (
    <li className="grid grid-cols-[44px_1fr] gap-3">
      <div className="relative flex justify-center">
        <span className="z-10 flex h-9 w-9 items-center justify-center rounded-full border border-primary/50 bg-primary/10 font-mono text-sm font-semibold text-primary">
          {index}
        </span>
        {!isLast && <span className="absolute top-9 bottom-[-1rem] w-px bg-border" />}
      </div>
      <div className="space-y-2 pb-1">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        </div>
        {children}
      </div>
    </li>
  )
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-border bg-card p-16 text-center">
      <ShieldCheck className="mx-auto h-10 w-10 text-muted-foreground/50" />
      <p className="mt-3 text-sm text-muted-foreground">从左侧队列选择一个待审核工单</p>
    </div>
  )
}
