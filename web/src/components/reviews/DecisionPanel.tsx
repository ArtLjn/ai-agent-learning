import { useState } from 'react'
import { Check, Edit3, MessageSquare, RefreshCw, X } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { ReviewDecision, ReviewDetail } from '@/types'
import { decisionMeta } from './reviewUtils'
import { cn } from '@/lib/utils'
import { getTicketSupplementPrompt } from '@/lib/ticketSupplementPrompt'

interface Props {
  detail: ReviewDetail
  reviewerId: string
  onReviewerIdChange: (v: string) => void
  onSubmit: (decision: ReviewDecision, reason: string, rewritten?: string) => Promise<void>
  submitting: boolean
}

interface DecisionOption {
  key: ReviewDecision
  label: string
  icon: typeof Check
  color: string
  needsConfirm?: boolean
}

const OPTIONS: DecisionOption[] = [
  { key: 'approve', label: '通过', icon: Check, color: 'bg-success/90 text-success-foreground hover:bg-success' },
  { key: 'rewrite', label: '改写', icon: Edit3, color: 'bg-primary text-primary-foreground hover:bg-primary/90' },
  { key: 'reprocess', label: '重处理', icon: RefreshCw, color: 'bg-warning text-warning-foreground hover:bg-warning/90', needsConfirm: true },
  { key: 'request_info', label: '请求补充', icon: MessageSquare, color: 'bg-primary text-primary-foreground hover:bg-primary/90' },
  { key: 'reject', label: '驳回', icon: X, color: 'bg-destructive text-destructive-foreground hover:bg-destructive/90', needsConfirm: true },
]

export function DecisionPanel({ detail, reviewerId, onReviewerIdChange, onSubmit, submitting }: Props) {
  const [pendingDecision, setPendingDecision] = useState<ReviewDecision | null>(null)
  const [reason, setReason] = useState('')
  const [rewritten, setRewritten] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)

  // 工单状态为 pending_human_review 时才允许决策；current_review 已 decided 也禁用
  const isDecided =
    detail.current_review?.status === 'decided' ||
    detail.status !== 'pending_human_review'

  const handleClick = (opt: DecisionOption) => {
    setPendingDecision(opt.key)
    if (opt.needsConfirm) {
      setConfirmOpen(true)
    } else {
      // 不需要二次确认，但仍要求理由输入
    }
  }

  const handleSubmit = async () => {
    if (!pendingDecision) return
    if (!reason.trim()) return
    if (pendingDecision === 'rewrite' && !rewritten.trim()) return
    await onSubmit(pendingDecision, reason.trim(), pendingDecision === 'rewrite' ? rewritten.trim() : undefined)
    setPendingDecision(null)
    setReason('')
    setRewritten('')
    setConfirmOpen(false)
  }

  const reasonMissing = !reason.trim()
  const rewriteMissing = pendingDecision === 'rewrite' && !rewritten.trim()
  const reviewerMissing = !reviewerId.trim()
  const canSubmit = !!pendingDecision && !reasonMissing && !rewriteMissing && !reviewerMissing && !submitting
  const supplementPrompt = getTicketSupplementPrompt(detail)

  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">审核员决策</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isDecided ? (
          <div className="rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
            该工单已离开待审核状态（当前状态：{detail.status}），无法继续决策。
          </div>
        ) : (
          <>
            <div>
              <label className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1 block">
                审核员 ID（必填）
              </label>
              <Input
                value={reviewerId}
                onChange={(e) => onReviewerIdChange(e.target.value)}
                placeholder="例：reviewer-001"
                className="h-8 text-sm"
              />
            </div>

            <div className="grid grid-cols-4 gap-2">
              {OPTIONS.map((opt) => {
                const Icon = opt.icon
                const active = pendingDecision === opt.key
                return (
                  <button
                    key={opt.key}
                    type="button"
                    onClick={() => handleClick(opt)}
                    className={cn(
                      'flex h-11 items-center justify-center gap-1.5 rounded-lg text-sm font-medium transition-all',
                      active ? cn(opt.color, 'ring-2 ring-offset-1 ring-offset-background', decisionMeta[opt.key].ring) : 'bg-muted text-foreground hover:bg-muted/70',
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {opt.label}
                  </button>
                )
              })}
            </div>

            {pendingDecision === 'rewrite' && (
              <div>
                <label className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1 block">
                  改写后文本（必填）
                </label>
                <Textarea
                  value={rewritten}
                  onChange={(e) => setRewritten(e.target.value)}
                  placeholder="请输入改写后的处理结果..."
                  rows={4}
                  className="text-sm"
                />
              </div>
            )}

            <div>
              <label className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1 block">
                {pendingDecision === 'request_info' ? '补充说明（必填）' : '决策理由（必填）'}
              </label>
              <Textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={pendingDecision === 'request_info'
                  ? supplementPrompt.placeholder
                  : '请说明本次决策的依据...'}
                rows={pendingDecision === 'request_info' ? 5 : 3}
                className="text-sm"
              />
              {pendingDecision === 'request_info' && (
                <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
                  {supplementPrompt.helperText}
                </p>
              )}
            </div>

            {confirmOpen && (
              <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs">
                <p className="text-destructive font-medium mb-1">二次确认</p>
                <p className="text-muted-foreground">
                  该操作将执行 <span className="font-medium">{decisionMeta[pendingDecision!].label}</span>，且不可撤销。请确认。
                </p>
              </div>
            )}

            <div className="flex items-center gap-2">
              <Button onClick={handleSubmit} disabled={!canSubmit} size="lg" className="min-w-[120px]">
                {submitting ? '提交中...' : '提交决策'}
              </Button>
              {pendingDecision && (
                <Button
                  variant="outline"
                  onClick={() => {
                    setPendingDecision(null)
                    setConfirmOpen(false)
                  }}
                  disabled={submitting}
                >
                  取消
                </Button>
              )}
              {reviewerMissing && (
                <span className="text-[11px] text-warning">请填写审核员 ID</span>
              )}
              {!reviewerMissing && pendingDecision && reasonMissing && (
                <span className="text-[11px] text-warning">请填写理由</span>
              )}
              {!reviewerMissing && rewriteMissing && (
                <span className="text-[11px] text-warning">请填写改写文本</span>
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
