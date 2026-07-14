import { Badge } from '@/components/ui/badge'
import { TICKET_CATEGORY_LABELS } from '@/lib/ticketPresentation'
import { cn } from '@/lib/utils'

const statusStyles: Record<string, string> = {
  received: 'bg-secondary text-muted-foreground',
  classifying: 'bg-warning/15 text-warning',
  processing: 'bg-primary/15 text-primary',
  reviewing: 'bg-[#a371f7]/15 text-[#a371f7]',
  pending_human_review: 'bg-[#a371f7]/15 text-[#a371f7]',
  waiting_user_input: 'bg-warning/15 text-warning',
  completed: 'bg-success/15 text-success',
  failed: 'bg-destructive/15 text-destructive',
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

const categoryStyles: Record<string, string> = {
  technical: 'bg-primary/15 text-primary',
  billing: 'bg-warning/15 text-warning',
  complaint: 'bg-destructive/15 text-destructive',
  inquiry: 'bg-success/15 text-success',
}

const priorityStyles: Record<string, string> = {
  P0: 'bg-destructive/15 text-destructive',
  P1: 'bg-warning/15 text-warning',
  P2: 'bg-primary/15 text-primary',
  P3: 'bg-secondary text-muted-foreground',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={cn('border-0 font-medium', statusStyles[status] || 'bg-secondary')}>
      {statusLabels[status] || status}
    </Badge>
  )
}

export function CategoryBadge({ category }: { category: string }) {
  return (
    <Badge variant="outline" className={cn('border-0 font-medium', categoryStyles[category] || 'bg-secondary')}>
      {TICKET_CATEGORY_LABELS[category] || category}
    </Badge>
  )
}

export function PriorityBadge({ priority }: { priority: string }) {
  return (
    <Badge variant="outline" className={cn('border-0 font-medium', priorityStyles[priority] || 'bg-secondary')}>
      {priority}
    </Badge>
  )
}
