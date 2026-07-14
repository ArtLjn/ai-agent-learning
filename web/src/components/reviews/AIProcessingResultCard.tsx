import { Brain } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Markdown } from '@/components/ui/markdown'

interface Props {
  processingResult: string | null
  reviewScore: number | null
}

export function AIProcessingResultCard({ processingResult, reviewScore }: Props) {
  return (
    <Card className="bg-card border-border">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Brain className="h-4 w-4 text-primary" />
          AI 处理结果
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-md border border-border bg-background p-2.5">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">审核评分</p>
            <p className="mt-1 text-lg font-bold tabular-nums text-primary">
              {reviewScore != null ? reviewScore.toFixed(2) : '-'}
            </p>
          </div>
          <div className="rounded-md border border-border bg-background p-2.5">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">复核结论</p>
            <p className="mt-1 text-sm font-medium">
              {reviewScore == null
                ? '未评分'
                : reviewScore >= 0.7
                  ? <span className="text-success">自动通过</span>
                  : <span className="text-warning">需人工</span>}
            </p>
          </div>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1">处理结果</p>
          <div className="rounded-md border border-border bg-background p-2.5 min-h-[60px]">
            <Markdown>{processingResult || '（暂无 AI 处理结果）'}</Markdown>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
