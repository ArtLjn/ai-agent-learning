import { Clock, AlertTriangle } from 'lucide-react'
import type { ReviewQueueItem as ReviewQueueItemType } from '@/types'
import { CategoryBadge, PriorityBadge, StatusBadge } from '@/components/layout/StatusBadge'
import { TriggerBadge } from './ReviewBadges'
import { formatWaiting, isWaitingTimeout } from './reviewUtils'
import { cn } from '@/lib/utils'

interface Props {
  item: ReviewQueueItemType
  active: boolean
  onSelect: (ticketId: string) => void
}

export function ReviewQueueItem({ item, active, onSelect }: Props) {
  const timeout = isWaitingTimeout(item.waiting_seconds)
  const aiRec = item.ai_suggestion?.recommended_decision

  return (
    <button
      type="button"
      onClick={() => onSelect(item.ticket_id)}
      className={cn(
        'w-full text-left rounded-lg border p-3 transition-colors',
        active
          ? 'border-primary bg-primary/5'
          : 'border-border bg-card hover:border-primary/40 hover:bg-muted/40',
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[11px] text-primary">{item.ticket_id.slice(0, 16)}</span>
        <div className="flex items-center gap-1.5">
          <TriggerBadge trigger={item.trigger_type} />
        </div>
      </div>

      <p className="mt-2 line-clamp-2 text-xs text-foreground/80 leading-relaxed">
        {item.content_preview}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {item.category && <CategoryBadge category={item.category} />}
        {item.priority && <PriorityBadge priority={item.priority} />}
        {item.status && <StatusBadge status={item.status} />}
        {aiRec && (
          <span className="rounded-sm bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            AI: {aiRec}
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground">
        <Clock className="h-3 w-3" />
        <span>等待 {formatWaiting(item.waiting_seconds)}</span>
        {timeout && (
          <span className="ml-auto inline-flex items-center gap-0.5 rounded-sm bg-destructive/15 px-1.5 py-0.5 text-[10px] font-medium text-destructive">
            <AlertTriangle className="h-2.5 w-2.5" />
            超时
          </span>
        )}
      </div>
    </button>
  )
}
