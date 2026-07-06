import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Clock,
  MessageSquare,
  Plus,
  ShieldCheck,
  Sparkles,
  Ticket as TicketIcon,
  UserCircle,
} from 'lucide-react'
import { useTickets } from '@/hooks/useApi'
import { api } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { StatusBadge, CategoryBadge, PriorityBadge } from '@/components/layout/StatusBadge'
import type { Ticket, TicketStatus } from '@/types'

const activeStatuses = new Set<TicketStatus>([
  'received',
  'classifying',
  'processing',
  'reviewing',
  'pending_human_review',
])

function getTime(value: string) {
  const time = new Date(value).getTime()
  return Number.isNaN(time) ? 0 : time
}

function formatDate(value: string) {
  const time = new Date(value)
  if (Number.isNaN(time.getTime())) return '-'
  return time.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function UserHome() {
  const navigate = useNavigate()
  const { data: tickets = [], isLoading } = useTickets({ limit: '100' })
  const { data: profile } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.getMe(),
  })

  const view = useMemo(() => {
    const sorted = [...tickets].sort((a, b) => getTime(b.created_at) - getTime(a.created_at))
    const waitingForInput = sorted.filter((ticket) => ticket.status === 'waiting_user_input')
    const active = sorted.filter((ticket) => activeStatuses.has(ticket.status))
    const completed = sorted.filter((ticket) => ticket.status === 'completed')
    const failed = sorted.filter((ticket) => ticket.status === 'failed')
    const pendingFeedback = completed.filter((ticket) =>
      ticket.review_score != null
      && ticket.review_score >= 0.7
      && ticket.satisfied == null
    )
    const needsAttention = [...waitingForInput, ...failed, ...pendingFeedback].slice(0, 4)

    return {
      total: sorted.length,
      active: active.length,
      waitingForInput: waitingForInput.length,
      completed: completed.length,
      failed: failed.length,
      pendingFeedback: pendingFeedback.length,
      needsAttention,
      recent: sorted.slice(0, 5),
    }
  }, [tickets])

  const profileTasks = useMemo(() => {
    const tasks: Array<{ title: string; detail: string; done: boolean }> = [
      {
        title: '联系方式',
        detail: profile?.contact ? '已填写，便于人工跟进' : '未填写，建议补充邮箱或手机号',
        done: Boolean(profile?.contact),
      },
      {
        title: '偏好分类',
        detail: profile?.preferred_categories?.length
          ? `已选择 ${profile.preferred_categories.length} 类常见问题`
          : '未设置，影响问题生成和服务偏好',
        done: Boolean(profile?.preferred_categories?.length),
      },
    ]
    const doneCount = tasks.filter((task) => task.done).length
    return { tasks, doneCount }
  }, [profile])

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-xl font-semibold">我的工作台</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            关注待补充、待反馈和账户信息完整度，完整工单操作在工单列表中完成。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => navigate('/profile')}>
            <UserCircle className="h-4 w-4" />
            个人资料
          </Button>
          <Button size="sm" onClick={() => navigate('/tickets')}>
            <TicketIcon className="h-4 w-4" />
            进入工单列表
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="待我补充"
          value={view.waitingForInput}
          detail="需要补充订单号、截图或说明"
          icon={MessageSquare}
          urgent={view.waitingForInput > 0}
        />
        <MetricCard
          label="待查看结果"
          value={view.pendingFeedback}
          detail="可进入详情确认是否满意"
          icon={CheckCircle2}
          urgent={view.pendingFeedback > 0}
        />
        <MetricCard
          label="处理中"
          value={view.active}
          detail="Agent 或审核员正在处理"
          icon={Clock}
        />
        <MetricCard
          label="账户完善"
          value={profileTasks.doneCount}
          detail="联系方式与偏好分类"
          icon={ShieldCheck}
          urgent={profileTasks.doneCount < profileTasks.tasks.length}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Card className="border-border bg-card">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <AlertCircle className="h-4 w-4 text-warning" />
              我的待办
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <LoadingRows count={3} />
            ) : view.needsAttention.length === 0 ? (
              <EmptyState text="暂无需要你处理的工单" />
            ) : (
              <div className="space-y-2">
                {view.needsAttention.map((ticket) => (
                  <TicketRow key={ticket.ticket_id} ticket={ticket} onClick={() => navigate(`/tickets/${ticket.ticket_id}`)} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <ShieldCheck className="h-4 w-4 text-primary" />
              账户服务准备度
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => navigate('/profile')}>
              完善
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {profileTasks.tasks.map((task) => (
              <div key={task.title} className="flex items-start gap-3 rounded-md border border-border bg-background/40 p-3">
                <div className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md ${
                  task.done ? 'bg-success/15 text-success' : 'bg-warning/15 text-warning'
                }`}>
                  {task.done ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium">{task.title}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">{task.detail}</p>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Card className="border-border bg-card">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Sparkles className="h-4 w-4 text-primary" />
              服务请求闭环
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-2">
              <FlowStep title="描述问题" detail="用自然语言说明现象、时间、影响范围" />
              <FlowStep title="系统理解" detail="Agent 自动识别分类、优先级和处理路径" />
              <FlowStep title="跟踪进度" detail="查看接收、分类、处理、审核、完成状态" />
              <FlowStep title="反馈升级" detail="不满意时进入人工审核或二次处理" />
            </div>
            <Button className="mt-4 w-full" onClick={() => navigate('/tickets')}>
              去工单列表处理
              <ArrowRight className="h-4 w-4" />
            </Button>
          </CardContent>
        </Card>

        <Card className="border-border bg-card">
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <TicketIcon className="h-4 w-4 text-primary" />
              最近工单
            </CardTitle>
            <Button variant="ghost" size="sm" onClick={() => navigate('/tickets')}>
              全部
            </Button>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <LoadingRows count={5} />
            ) : view.recent.length === 0 ? (
              <EmptyState
                text="还没有工单，先提交一个问题"
                action={(
                  <Button size="sm" onClick={() => navigate('/tickets')}>
                    <Plus className="h-4 w-4" />
                    去提交
                  </Button>
                )}
              />
            ) : (
              <div className="space-y-2">
                {view.recent.map((ticket) => (
                  <TicketRow key={ticket.ticket_id} ticket={ticket} onClick={() => navigate(`/tickets/${ticket.ticket_id}`)} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  urgent = false,
}: {
  label: string
  value: number
  detail: string
  icon: typeof TicketIcon
  urgent?: boolean
}) {
  return (
    <Card className="border-border bg-card">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs text-muted-foreground">{label}</p>
            <p className={`mt-1 text-2xl font-semibold ${urgent ? 'text-warning' : 'text-primary'}`}>
              {value}
            </p>
          </div>
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-background/70">
            <Icon className={`h-5 w-5 ${urgent ? 'text-warning' : 'text-primary'}`} />
          </div>
        </div>
        <p className="mt-3 truncate text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  )
}

function TicketRow({ ticket, onClick }: { ticket: Ticket; onClick: () => void }) {
  const isFeedbackCandidate = ticket.status === 'completed'
    && ticket.review_score != null
    && ticket.review_score >= 0.7
    && ticket.satisfied == null
  const actionText = ticket.status === 'waiting_user_input'
    ? '补充信息'
    : isFeedbackCandidate
      ? '查看并反馈'
      : ticket.status === 'failed'
        ? '查看异常'
        : '查看详情'

  return (
    <button
      type="button"
      onClick={onClick}
      className="block w-full rounded-md border border-border bg-background/40 p-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/5"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-mono text-xs text-primary">{ticket.ticket_id}</p>
          <p className="mt-1 line-clamp-2 text-sm text-foreground">{ticket.content}</p>
        </div>
        <StatusBadge status={ticket.status} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {ticket.category ? <CategoryBadge category={ticket.category} /> : null}
        {ticket.priority ? <PriorityBadge priority={ticket.priority} /> : null}
        <span>{formatDate(ticket.created_at)}</span>
        <span className="ml-auto text-primary">{actionText}</span>
      </div>
    </button>
  )
}

function FlowStep({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-md border border-border bg-background/40 p-3">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>
    </div>
  )
}

function LoadingRows({ count }: { count: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="h-20 animate-pulse rounded-md bg-muted" />
      ))}
    </div>
  )
}

function EmptyState({ text, action }: { text: string; action?: React.ReactNode }) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center rounded-md border border-dashed border-border text-center">
      <p className="text-sm text-muted-foreground">{text}</p>
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
