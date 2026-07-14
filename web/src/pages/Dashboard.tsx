import { useMemo, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAnalytics, useTickets } from '@/hooks/useApi'
import { useReviewQueue, useReviewStats } from '@/hooks/useReviews'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { CategoryBadge, PriorityBadge, StatusBadge } from '@/components/layout/StatusBadge'
import { TICKET_CATEGORY_LABELS } from '@/lib/ticketPresentation'
import type { Analytics, DailyStat, EfficiencyStats, ResolutionStats, Ticket } from '@/types'
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock,
  Gauge,
  Layers3,
  ListChecks,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Ticket as TicketIcon,
  Timer,
  TrendingUp,
  Users,
  Wrench,
  XCircle,
} from 'lucide-react'

const COLORS = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a371f7', '#8b949e']

const categoryLabels: Record<string, string> = {
  ...TICKET_CATEGORY_LABELS,
  uncategorized: '未分类',
}

const priorityLabels: Record<string, string> = {
  P0: 'P0 紧急',
  P1: 'P1 高',
  P2: 'P2 中',
  P3: 'P3 低',
  unassigned: '未分配',
}

const statusLabels: Record<string, string> = {
  received: '已接收',
  classifying: '分类中',
  processing: '处理中',
  reviewing: '审核中',
  pending_human_review: '待人工审核',
  waiting_user_input: '待用户补充',
  completed: '已完成',
  failed: '失败',
}

const activeStatuses = new Set(['received', 'classifying', 'processing', 'reviewing', 'pending_human_review', 'waiting_user_input'])

type DistributionItem = {
  name: string
  label: string
  value: number
  percent: number
}

type DailyChartItem = {
  date: string
  created: number
  completed: number
  failed: number
  backlog: number
}

type DashboardTicket = Omit<Ticket, 'status'> & {
  status: string
}

type DissatisfiedTicket = NonNullable<NonNullable<Analytics['service_desk']>['dissatisfied_tickets']>[number]

function toPercent(value: number | undefined | null, digits = 0) {
  return `${((value ?? 0) * 100).toFixed(digits)}%`
}

function formatSeconds(value: number | undefined | null) {
  const seconds = Number(value ?? 0)
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const remain = Math.round(seconds % 60)
  return `${minutes}m ${remain}s`
}

function formatWaitTime(value: number | undefined | null) {
  const seconds = Number(value ?? 0)
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
}

function formatNumber(value: number | undefined | null, digits = 0) {
  return Number(value ?? 0).toFixed(digits)
}

function getDailyTotal(item: DailyStat) {
  return Number(item.total ?? item.created ?? 0)
}

function getCreatedAtTime(ticket: { created_at: string }) {
  const time = new Date(ticket.created_at).getTime()
  return Number.isNaN(time) ? 0 : time
}

function createDistribution(
  source: Record<string, number> | undefined,
  labels: Record<string, string>,
): DistributionItem[] {
  const entries = Object.entries(source ?? {})
  const total = entries.reduce((sum, [, value]) => sum + Number(value || 0), 0)
  return entries
    .map(([name, value]) => ({
      name,
      label: labels[name] ?? name,
      value: Number(value || 0),
      percent: total > 0 ? Number(value || 0) / total : 0,
    }))
    .sort((a, b) => b.value - a.value)
}

export function Dashboard() {
  const navigate = useNavigate()
  const { data, isLoading: analyticsLoading } = useAnalytics()
  const { data: tickets = [], isLoading: ticketsLoading } = useTickets({ limit: '100' })
  const { data: reviewStats, isLoading: reviewStatsLoading } = useReviewStats()
  const { data: reviewQueue, isLoading: reviewQueueLoading } = useReviewQueue({ limit: 5 })

  const isLoading = analyticsLoading || ticketsLoading || reviewStatsLoading || reviewQueueLoading

  const dashboard = useMemo(() => {
    const stats: Partial<ResolutionStats> = data?.resolution_stats ?? {}
    const efficiency: Partial<EfficiencyStats> = data?.efficiency ?? {}
    const categoryData = createDistribution(data?.category_distribution, categoryLabels)
    const priorityData = createDistribution(data?.priority_distribution, priorityLabels)
    const typedTickets = tickets as DashboardTicket[]
    const totalTickets = Number(stats.total ?? typedTickets.length ?? 0)
    const completed = Number(stats.completed ?? 0)
    const failed = Number(stats.failed ?? 0)
    const activeTickets = typedTickets.filter((ticket) => activeStatuses.has(ticket.status))
    const highPriorityOpen = activeTickets.filter((ticket) => ticket.priority === 'P0' || ticket.priority === 'P1')
    const retryTickets = typedTickets.filter((ticket) => Number(ticket.retry_count ?? 0) > 0)
    const pendingReviews = Number(reviewStats?.pending_count ?? reviewQueue?.total ?? 0)
    const successRate = Number(stats.success_rate ?? 0)
    const avgDuration = Number(efficiency.avg_duration_seconds ?? 0)
    const avgTools = Number(efficiency.avg_tool_calls ?? 0)
    const backlog = Math.max(totalTickets - completed - failed, 0)
    const p0Open = activeTickets.filter((ticket) => ticket.priority === 'P0').length
    const p1Open = activeTickets.filter((ticket) => ticket.priority === 'P1').length
    const reviewQueueItems = reviewQueue?.queue ?? []
    const maxReviewWaitSeconds = reviewQueueItems.reduce(
      (max, item) => Math.max(max, Number(item.waiting_seconds ?? 0)),
      0,
    )
    const reviewAdoptionRate = Number(
      data?.service_desk?.review_quality?.ai_adoption_rate ??
      reviewStats?.ai_adoption_rate ??
      0,
    )
    const feedbackSummary = data?.service_desk?.feedback_summary
    const satisfiedFeedback = Number(feedbackSummary?.satisfied ?? 0)
    const dissatisfiedFeedback = Number(feedbackSummary?.dissatisfied ?? 0)
    const totalFeedback = Number(feedbackSummary?.total ?? 0)
    const satisfactionRate = Number(feedbackSummary?.satisfaction_rate ?? 0)
    const dissatisfiedTickets = data?.service_desk?.dissatisfied_tickets ?? []

    const dailyChart: DailyChartItem[] = (data?.daily_stats ?? []).map((item) => {
      const created = getDailyTotal(item)
      const done = Number(item.completed ?? 0)
      const errors = Number(item.failed ?? 0)
      return {
        date: String(item.date ?? '').slice(5),
        created,
        completed: done,
        failed: errors,
        backlog: Math.max(created - done - errors, 0),
      }
    })

    const statusData = Object.entries(
      typedTickets.reduce<Record<string, number>>((acc, ticket) => {
        acc[ticket.status] = (acc[ticket.status] ?? 0) + 1
        return acc
      }, {}),
    ).map(([status, value]) => ({
      status,
      label: statusLabels[status] ?? status,
      value,
    }))

    const recentTickets = [...typedTickets]
      .sort((a, b) => getCreatedAtTime(b) - getCreatedAtTime(a))
      .slice(0, 5)

    const riskTickets = [...typedTickets]
      .filter((ticket) => (
        ticket.status === 'failed' ||
        ticket.status === 'pending_human_review' ||
        ticket.priority === 'P0' ||
        Number(ticket.retry_count ?? 0) > 0 ||
        Number(ticket.review_score ?? 5) < 3
      ))
      .sort((a, b) => {
        const priorityWeight = { P0: 4, P1: 3, P2: 2, P3: 1 } as Record<string, number>
        return (
          (priorityWeight[b.priority ?? ''] ?? 0) - (priorityWeight[a.priority ?? ''] ?? 0) ||
          getCreatedAtTime(b) - getCreatedAtTime(a)
        )
      })
      .slice(0, 5)

    const latestDay = dailyChart[dailyChart.length - 1] ?? { created: 0, completed: 0, failed: 0 }
    const denominator = Math.max(totalTickets, 1)
    const healthScore = Math.max(
      0,
      Math.round(
        100 -
          (failed / denominator) * 40 -
          (backlog / denominator) * 20 -
          (pendingReviews / denominator) * 18 -
          (highPriorityOpen.length / denominator) * 14 -
          (retryTickets.length / denominator) * 8,
      ),
    )

    const suggestions = [
      pendingReviews > 0
        ? `有 ${pendingReviews} 个工单等待人工审核，建议优先处理 P0/P1 队列。`
        : '人工审核队列清空，当前可重点关注用户反馈和知识维护。',
      failed > 0
        ? `失败工单 ${failed} 个，建议进入审核工作台复核处理原因。`
        : '暂无失败工单，服务请求处理保持稳定。',
      highPriorityOpen.length > 0
        ? `仍有 ${highPriorityOpen.length} 个高优先级未闭环工单。`
        : '高优先级工单暂无积压。',
    ]

    return {
      stats,
      categoryData,
      priorityData,
      statusData,
      dailyChart,
      recentTickets,
      riskTickets,
      activeCount: activeTickets.length,
      highPriorityOpen: highPriorityOpen.length,
      retryCount: retryTickets.length,
      p0Open,
      p1Open,
      pendingReviews,
      successRate,
      avgDuration,
      avgTools,
      totalTickets,
      completed,
      failed,
      backlog,
      latestDay,
      maxReviewWaitSeconds,
      reviewAdoptionRate,
      satisfiedFeedback,
      dissatisfiedFeedback,
      totalFeedback,
      satisfactionRate,
      dissatisfiedTickets,
      healthScore,
      suggestions,
    }
  }, [data, reviewQueue, reviewStats?.ai_adoption_rate, reviewStats?.pending_count, tickets])

  if (isLoading || !data) {
    return <DashboardSkeleton />
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em] text-primary">
            <Sparkles className="h-3.5 w-3.5" />
            服务台运营总览
          </div>
          <h2 className="mt-2 text-2xl font-semibold">服务台总览</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            汇总服务请求流量、人工审核压力、AI 建议采纳、知识维护与用户反馈
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5">
            <RefreshCw className="h-3.5 w-3.5 text-primary" />
            30 秒自动刷新
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5">
            <ShieldCheck className="h-3.5 w-3.5 text-success" />
            服务在线
          </span>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Card className="border-border bg-card">
          <CardContent className="grid gap-4 p-4 md:grid-cols-4">
            <HeroMetric
              label="总工单"
              value={dashboard.totalTickets}
              detail={`${dashboard.activeCount} 个处理中`}
              icon={TicketIcon}
              color="text-primary"
            />
            <HeroMetric
              label="自动闭环率"
              value={toPercent(dashboard.successRate)}
              detail={`${dashboard.completed} 已完成 / ${dashboard.failed} 失败`}
              icon={TrendingUp}
              color="text-success"
            />
            <HeroMetric
              label="待人工审核"
              value={dashboard.pendingReviews}
              detail={`${dashboard.highPriorityOpen} 个高优先级未闭环`}
              icon={Users}
              color={dashboard.pendingReviews > 0 ? 'text-warning' : 'text-success'}
            />
            <HeroMetric
              label="平均耗时"
              value={formatSeconds(dashboard.avgDuration)}
              detail={`平均 ${formatNumber(dashboard.avgTools, 1)} 个处理步骤`}
              icon={Timer}
              color="text-primary"
            />
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardContent className="grid grid-cols-[150px_1fr] gap-3 p-4">
            <QualityRing value={dashboard.healthScore} />
            <div className="flex min-w-0 flex-col justify-center">
              <p className="text-xs text-muted-foreground">服务压力指数</p>
              <div className="mt-1 flex items-end gap-2">
                <span className="text-3xl font-bold text-foreground">{dashboard.healthScore}</span>
                <span className="pb-1 text-sm text-muted-foreground">/ 100</span>
              </div>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                根据失败、积压、待审核、高优先级和返工情况扣分，反映当前服务台压力。
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-6">
        <CompactMetric
          label="今日新增"
          value={dashboard.latestDay.created}
          detail={`${dashboard.latestDay.completed} 已完成 / ${dashboard.latestDay.failed} 失败`}
          icon={BarChart3}
          color="text-primary"
        />
        <CompactMetric
          label="当前积压"
          value={dashboard.backlog}
          detail={`${dashboard.activeCount} 个仍在流程中`}
          icon={Gauge}
          color={dashboard.backlog > 0 ? 'text-warning' : 'text-success'}
        />
        <CompactMetric
          label="高优先级未闭环"
          value={dashboard.highPriorityOpen}
          detail={`P0 ${dashboard.p0Open} / P1 ${dashboard.p1Open}`}
          icon={AlertTriangle}
          color={dashboard.highPriorityOpen > 0 ? 'text-destructive' : 'text-success'}
        />
        <CompactMetric
          label="审核最长等待"
          value={dashboard.pendingReviews > 0 ? formatWaitTime(dashboard.maxReviewWaitSeconds) : '0'}
          detail={`${dashboard.pendingReviews} 个待审核`}
          icon={Users}
          color={dashboard.pendingReviews > 0 ? 'text-warning' : 'text-success'}
        />
        <CompactMetric
          label="AI 建议采纳"
          value={toPercent(dashboard.reviewAdoptionRate)}
          detail="人工决策与推荐一致率"
          icon={Sparkles}
          color="text-primary"
        />
        <CompactMetric
          label="不满意反馈"
          value={dashboard.dissatisfiedFeedback}
          detail={`${toPercent(dashboard.satisfactionRate)} 满意率`}
          icon={ShieldCheck}
          color={dashboard.dissatisfiedFeedback > 0 ? 'text-warning' : 'text-success'}
        />
      </div>

      <UserFeedbackPanel
        satisfied={dashboard.satisfiedFeedback}
        dissatisfied={dashboard.dissatisfiedFeedback}
        total={dashboard.totalFeedback}
        satisfactionRate={dashboard.satisfactionRate}
        tickets={dashboard.dissatisfiedTickets}
        onOpenTicket={(ticketId) => navigate(`/tickets/${ticketId}`)}
      />

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <ChartCard
          title="近 7 日处理趋势"
          description="创建、完成、失败与当日积压"
          icon={BarChart3}
        >
          <DailyTrendChart data={dashboard.dailyChart} />
        </ChartCard>

        <ChartCard
          title="工单状态漏斗"
          description="最近工单当前分布"
          icon={Gauge}
        >
          <StatusFunnel data={dashboard.statusData} />
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <ChartCard
          title="分类分布"
          description="识别当前问题类型结构"
          icon={Layers3}
        >
          <DistributionChart data={dashboard.categoryData} />
        </ChartCard>

        <ChartCard
          title="优先级压力"
          description="P0/P1 越高越需要值班关注"
          icon={AlertTriangle}
        >
          <DistributionChart data={dashboard.priorityData} />
        </ChartCard>

        <ChartCard
          title="运营建议"
          description="基于当前指标自动生成"
          icon={ListChecks}
        >
          <div className="space-y-3 pt-1">
            {dashboard.suggestions.map((suggestion, index) => (
              <div key={suggestion} className="flex gap-3 rounded-lg border border-border bg-background/40 p-3">
                <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/15 text-xs font-semibold text-primary">
                  {index + 1}
                </div>
                <p className="text-sm leading-6 text-foreground/90">{suggestion}</p>
              </div>
            ))}
          </div>
        </ChartCard>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <TicketListCard
          title="风险工单"
          description="失败、重试、低评分、高优先级或待审核"
          tickets={dashboard.riskTickets}
          emptyText="暂无风险工单"
          icon={XCircle}
        />
        <TicketListCard
          title="最新工单"
          description="最近提交的工单动态"
          tickets={dashboard.recentTickets}
          emptyText="暂无最新工单"
          icon={Clock}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <PipelineCard label="接入与分类" value={dashboard.totalTickets} icon={TicketIcon} color="text-primary" />
        <PipelineCard label="服务处理" value={dashboard.activeCount + dashboard.completed} icon={Wrench} color="text-warning" />
        <PipelineCard label="质量复核完成" value={dashboard.completed} icon={CheckCircle2} color="text-success" />
      </div>
    </div>
  )
}

function HeroMetric({
  label,
  value,
  detail,
  icon: Icon,
  color,
}: {
  label: string
  value: string | number
  detail: string
  icon: typeof TicketIcon
  color: string
}) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-background/35 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className={`mt-1 truncate text-2xl font-bold ${color}`}>{value}</p>
        </div>
        <Icon className={`h-7 w-7 shrink-0 ${color} opacity-35`} />
      </div>
      <p className="mt-2 truncate text-xs text-muted-foreground">{detail}</p>
    </div>
  )
}

function CompactMetric({
  label,
  value,
  detail,
  icon: Icon,
  color,
}: {
  label: string
  value: string | number
  detail: string
  icon: typeof TicketIcon
  color: string
}) {
  return (
    <Card className="border-border bg-card">
      <CardContent className="p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className={`mt-1 text-2xl font-semibold ${color}`}>{value}</p>
          </div>
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-background/70">
            <Icon className={`h-5 w-5 ${color}`} />
          </div>
        </div>
        <p className="mt-3 truncate text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  )
}

function QualityRing({ value }: { value: number }) {
  const radius = 44
  const circumference = 2 * Math.PI * radius
  const offset = circumference * (1 - Math.min(Math.max(value, 0), 100) / 100)
  const color = value >= 80 ? '#3fb950' : value >= 60 ? '#d29922' : '#f85149'

  return (
    <div className="flex h-32 min-w-32 items-center justify-center">
      <svg viewBox="0 0 120 120" className="h-28 w-28" role="img" aria-label={`质量评分 ${value}`}>
        <circle cx="60" cy="60" r={radius} fill="none" stroke="#21262d" strokeWidth="12" />
        <circle
          cx="60"
          cy="60"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          transform="rotate(-90 60 60)"
        />
      </svg>
    </div>
  )
}

function DailyTrendChart({ data }: { data: DailyChartItem[] }) {
  const maxValue = Math.max(1, ...data.flatMap((item) => [item.created, item.completed, item.failed]))
  const chartHeight = 220
  const chartWidth = 680
  const padding = { top: 14, right: 18, bottom: 34, left: 38 }
  const innerWidth = chartWidth - padding.left - padding.right
  const innerHeight = chartHeight - padding.top - padding.bottom
  const step = data.length > 1 ? innerWidth / (data.length - 1) : innerWidth
  const barWidth = Math.min(18, Math.max(10, innerWidth / Math.max(data.length, 1) / 4))
  const points = data.map((item, index) => {
    const x = padding.left + step * index
    const y = padding.top + innerHeight - (item.completed / maxValue) * innerHeight
    return `${x},${y}`
  }).join(' ')

  return (
    <div className="h-72 min-w-0 overflow-hidden">
      <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} className="h-full w-full" role="img" aria-label="近 7 日处理趋势">
        {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
          const y = padding.top + innerHeight * ratio
          const value = Math.round(maxValue * (1 - ratio))
          return (
            <g key={ratio}>
              <line x1={padding.left} x2={chartWidth - padding.right} y1={y} y2={y} stroke="#30363d" strokeDasharray="3 3" />
              <text x={padding.left - 12} y={y + 4} textAnchor="end" className="fill-muted-foreground text-[11px]">
                {value}
              </text>
            </g>
          )
        })}
        <polyline points={points} fill="none" stroke="#3fb950" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
        {data.map((item, index) => {
          const x = padding.left + step * index
          const createdHeight = (item.created / maxValue) * innerHeight
          const failedHeight = (item.failed / maxValue) * innerHeight
          const baseY = padding.top + innerHeight
          return (
            <g key={item.date}>
              <rect
                x={x - barWidth}
                y={baseY - createdHeight}
                width={barWidth}
                height={createdHeight}
                rx="3"
                fill="#58a6ff"
              />
              {item.failed > 0 && (
                <rect
                  x={x + 2}
                  y={baseY - failedHeight}
                  width={barWidth}
                  height={failedHeight}
                  rx="3"
                  fill="#f85149"
                />
              )}
              <text x={x} y={chartHeight - 10} textAnchor="middle" className="fill-muted-foreground text-[11px]">
                {item.date}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function StatusFunnel({ data }: { data: { label: string; value: number }[] }) {
  const maxValue = Math.max(1, ...data.map((item) => item.value))

  if (data.length === 0) {
    return <EmptyBlock text="暂无状态数据" />
  }

  return (
    <div className="flex h-72 flex-col justify-center gap-4">
      {data.map((item, index) => (
        <div key={item.label} className="grid grid-cols-[80px_1fr_28px] items-center gap-3">
          <span className="truncate text-right text-xs text-muted-foreground">{item.label}</span>
          <div className="h-8 overflow-hidden rounded-md bg-muted/40">
            <div
              className="h-full rounded-md"
              style={{
                width: `${Math.max((item.value / maxValue) * 100, 6)}%`,
                backgroundColor: COLORS[index % COLORS.length],
              }}
            />
          </div>
          <span className="text-xs font-medium tabular-nums text-foreground">{item.value}</span>
        </div>
      ))}
    </div>
  )
}

function ChartCard({
  title,
  description,
  icon: Icon,
  children,
}: {
  title: string
  description: string
  icon: typeof TicketIcon
  children: ReactNode
}) {
  return (
    <Card className="border-border bg-card">
      <CardHeader className="pb-1">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <Icon className="h-4 w-4 text-primary" />
              {title}
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{description}</p>
          </div>
        </div>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

function DistributionChart({ data }: { data: DistributionItem[] }) {
  if (data.length === 0) {
    return <EmptyBlock text="暂无分布数据" />
  }

  const gradient = data.reduce((segments, item, index) => {
    const start = data.slice(0, index).reduce((sum, current) => sum + current.percent * 100, 0)
    const end = start + item.percent * 100
    return `${segments}, ${COLORS[index % COLORS.length]} ${start.toFixed(2)}% ${end.toFixed(2)}%`
  }, '#21262d 0% 0%')

  return (
    <div className="grid gap-3 pt-1 md:grid-cols-[150px_1fr] xl:grid-cols-1 2xl:grid-cols-[150px_1fr]">
      <div className="flex h-40 min-w-0 items-center justify-center">
        <div
          className="relative h-28 w-28 rounded-full"
          style={{ background: `conic-gradient(${gradient})` }}
          aria-label="分布占比图"
        >
          <div className="absolute inset-5 flex flex-col items-center justify-center rounded-full bg-card">
            <span className="text-xl font-semibold text-foreground">{data[0]?.value ?? 0}</span>
            <span className="mt-0.5 text-[10px] text-muted-foreground">最高项</span>
          </div>
        </div>
      </div>
      <div className="space-y-2 self-center">
        {data.map((item, index) => (
          <div key={item.name} className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="flex min-w-0 items-center gap-2 text-muted-foreground">
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: COLORS[index % COLORS.length] }}
                />
                <span className="truncate">{item.label}</span>
              </span>
              <span className="font-medium text-foreground">{item.value}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.max(item.percent * 100, item.value > 0 ? 5 : 0)}%`,
                  backgroundColor: COLORS[index % COLORS.length],
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function TicketListCard({
  title,
  description,
  tickets,
  emptyText,
  icon: Icon,
}: {
  title: string
  description: string
  tickets: DashboardTicket[]
  emptyText: string
  icon: typeof TicketIcon
}) {
  return (
    <Card className="border-border bg-card">
      <CardHeader className="pb-1">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <Icon className="h-4 w-4 text-primary" />
          {title}
        </CardTitle>
        <p className="text-xs text-muted-foreground">{description}</p>
      </CardHeader>
      <CardContent>
        {tickets.length === 0 ? (
          <EmptyBlock text={emptyText} />
        ) : (
          <div className="divide-y divide-border">
            {tickets.map((ticket) => (
              <div key={ticket.ticket_id} className="grid gap-2 py-3 md:grid-cols-[1fr_auto] md:items-center">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-primary">{ticket.ticket_id}</span>
                    {ticket.priority && <PriorityBadge priority={ticket.priority} />}
                    <StatusBadge status={ticket.status} />
                  </div>
                  <p className="mt-2 line-clamp-1 text-sm text-foreground/90">{ticket.content}</p>
                </div>
                <div className="flex flex-wrap items-center gap-2 md:justify-end">
                  {ticket.category && <CategoryBadge category={ticket.category} />}
                  <span className="text-xs text-muted-foreground">
                    {new Date(ticket.created_at).toLocaleDateString('zh-CN', {
                      month: '2-digit',
                      day: '2-digit',
                    })}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function UserFeedbackPanel({
  satisfied,
  dissatisfied,
  total,
  satisfactionRate,
  tickets,
  onOpenTicket,
}: {
  satisfied: number
  dissatisfied: number
  total: number
  satisfactionRate: number
  tickets: DissatisfiedTicket[]
  onOpenTicket: (ticketId: string) => void
}) {
  const satisfiedPercent = total > 0 ? (satisfied / total) * 100 : 0
  const dissatisfiedPercent = total > 0 ? (dissatisfied / total) * 100 : 0

  return (
    <Card className="border-border bg-card">
      <CardHeader className="pb-2">
        <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-sm font-medium">
              <ShieldCheck className="h-4 w-4 text-primary" />
              用户反馈复核
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              将验收结果转成服务台动作：满意用于闭环统计，不满意进入人工复核队列。
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-md border border-border bg-background/50 px-3 py-2">
            <span className="text-xs text-muted-foreground">满意率</span>
            <span className={`text-lg font-semibold ${satisfactionRate >= 0.8 || total === 0 ? 'text-success' : 'text-warning'}`}>
              {toPercent(satisfactionRate)}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 xl:grid-cols-[0.8fr_1.2fr]">
          <div className="space-y-4 rounded-lg border border-border bg-background/35 p-4">
            <div className="grid grid-cols-3 gap-3">
              <FeedbackStat label="总反馈" value={total} />
              <FeedbackStat label="满意" value={satisfied} tone="success" />
              <FeedbackStat label="不满意" value={dissatisfied} tone={dissatisfied > 0 ? 'warning' : 'success'} />
            </div>
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>反馈分布</span>
                <span>{total > 0 ? `${satisfied} / ${dissatisfied}` : '暂无反馈'}</span>
              </div>
              <div className="flex h-3 overflow-hidden rounded-full bg-muted">
                <div
                  className="bg-success"
                  style={{ width: `${Math.max(satisfiedPercent, satisfied > 0 ? 8 : 0)}%` }}
                  aria-label="满意反馈占比"
                />
                <div
                  className="bg-warning"
                  style={{ width: `${Math.max(dissatisfiedPercent, dissatisfied > 0 ? 8 : 0)}%` }}
                  aria-label="不满意反馈占比"
                />
              </div>
            </div>
            <div className="rounded-md border border-border bg-card/70 p-3 text-xs leading-5 text-muted-foreground">
              {dissatisfied > 0
                ? `当前有 ${dissatisfied} 条不满意反馈需要服务台复核，优先核对申诉原因和人工审核状态。`
                : '暂无不满意反馈，服务台可继续关注新完成工单的验收情况。'}
            </div>
          </div>

          <div className="rounded-lg border border-border bg-background/35">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <p className="text-sm font-medium text-foreground">不满意工单</p>
                <p className="mt-0.5 text-xs text-muted-foreground">点击进入工单详情，查看申诉与复核状态。</p>
              </div>
            </div>
            {tickets.length === 0 ? (
              <div className="p-4">
                <EmptyBlock text="暂无不满意反馈工单" />
              </div>
            ) : (
              <div className="divide-y divide-border">
                {tickets.slice(0, 6).map((ticket) => (
                  <div key={ticket.ticket_id} className="grid gap-3 px-4 py-3 lg:grid-cols-[1fr_auto] lg:items-center">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-primary">{ticket.ticket_id}</span>
                        {ticket.priority && <PriorityBadge priority={ticket.priority} />}
                        <StatusBadge status={ticket.status} />
                        {ticket.category && <CategoryBadge category={ticket.category} />}
                      </div>
                      <p className="mt-2 line-clamp-1 text-sm text-foreground/90">
                        {ticket.trigger_reason || '员工反馈不满意，等待服务台复核。'}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        触发类型：{ticket.trigger_type || 'user_request'} · {new Date(ticket.created_at).toLocaleString('zh-CN', {
                          month: '2-digit',
                          day: '2-digit',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </p>
                    </div>
                    <Button variant="outline" size="sm" onClick={() => onOpenTicket(ticket.ticket_id)}>
                      查看复核
                    </Button>
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

function FeedbackStat({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: number
  tone?: 'default' | 'success' | 'warning'
}) {
  const toneClass = tone === 'success'
    ? 'text-success'
    : tone === 'warning'
      ? 'text-warning'
      : 'text-foreground'

  return (
    <div className="rounded-md border border-border bg-card/70 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${toneClass}`}>{value}</p>
    </div>
  )
}

function PipelineCard({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string
  value: number
  icon: typeof TicketIcon
  color: string
}) {
  return (
    <Card className="border-border bg-card">
      <CardContent className="flex items-center gap-3 p-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-background/70">
          <Icon className={`h-5 w-5 ${color}`} />
        </div>
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">{label}</p>
          <p className={`mt-1 text-xl font-semibold ${color}`}>{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}

function EmptyBlock({ text }: { text: string }) {
  return (
    <div className="flex h-40 items-center justify-center rounded-lg border border-dashed border-border bg-background/30 text-sm text-muted-foreground">
      {text}
    </div>
  )
}

function DashboardSkeleton() {
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Skeleton className="h-4 w-36" />
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-72" />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Skeleton className="h-40 rounded-lg" />
        <Skeleton className="h-40 rounded-lg" />
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-28 rounded-lg" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Skeleton className="h-80 rounded-lg" />
        <Skeleton className="h-80 rounded-lg" />
      </div>
      <div className="grid gap-4 xl:grid-cols-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-64 rounded-lg" />
        ))}
      </div>
    </div>
  )
}
