import { useEffect, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Loader2, Activity, AlertTriangle, Clock, Coins } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import type { AgentStatEntry, PromptAgentName } from '@/types'

const AGENT_LABEL: Record<PromptAgentName, string> = {
  intent: 'Intent',
  classify: 'Classifier',
  process: 'Processor',
  review: 'Reviewer',
  coordinator: 'Coordinator',
}

const DAY_OPTIONS = [1, 7, 30, 90]

export function AgentCallStats() {
  const [days, setDays] = useState(7)
  const [stats, setStats] = useState<AgentStatEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const resp = await api.getAgentStats(days)
      setStats(resp.agents)
    } catch (e) {
      setError(e instanceof ApiError ? e.detail || e.message : String(e))
      setStats([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [days])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2">
            <Activity className="w-5 h-5 text-primary" />
            Agent 调用统计
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            5 个 Agent 的调用次数、耗时、成功率与 Token 用量（基于 spans + token_daily_stats）
          </p>
        </div>
        <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DAY_OPTIONS.map((d) => (
              <SelectItem key={d} value={String(d)}>
                最近 {d} 天
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {error && (
        <div className="px-3 py-2 rounded border border-red-500/30 bg-red-500/10 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
        {loading ? (
          <div className="col-span-full flex items-center justify-center py-12">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
          </div>
        ) : (
          stats.map((s) => <StatCard key={s.agent_name} entry={s} />)
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">详细数据（按 Agent 分行）</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead className="text-right">调用次数</TableHead>
                  <TableHead className="text-right">平均耗时</TableHead>
                  <TableHead className="text-right">最大耗时</TableHead>
                  <TableHead className="text-right">成功率</TableHead>
                  <TableHead className="text-right">错误数</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead className="text-right">请求数</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {stats.map((s) => (
                  <TableRow key={s.agent_name}>
                    <TableCell className="font-medium">
                      {AGENT_LABEL[s.agent_name]}
                    </TableCell>
                    <TableCell className="text-right font-mono">{s.call_count}</TableCell>
                    <TableCell className="text-right font-mono">
                      {s.avg_duration_ms.toFixed(1)} ms
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {s.max_duration_ms.toFixed(1)} ms
                    </TableCell>
                    <TableCell className="text-right">
                      <SuccessBadge rate={s.success_rate} />
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {s.error_count > 0 ? (
                        <span className="text-red-400">{s.error_count}</span>
                      ) : (
                        '0'
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono">
                      {s.total_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right font-mono">{s.request_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function StatCard({ entry }: { entry: AgentStatEntry }) {
  const color =
    entry.success_rate >= 0.95
      ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
      : entry.success_rate >= 0.8
      ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
      : 'bg-red-500/15 text-red-300 border-red-500/30'

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span>{AGENT_LABEL[entry.agent_name]}</span>
          <Badge className={color}>{(entry.success_rate * 100).toFixed(1)}%</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground flex items-center gap-1">
            <Activity className="w-3 h-3" /> 调用次数
          </span>
          <span className="font-mono">{entry.call_count}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground flex items-center gap-1">
            <Clock className="w-3 h-3" /> 平均耗时
          </span>
          <span className="font-mono">{entry.avg_duration_ms.toFixed(1)} ms</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" /> 错误数
          </span>
          <span className="font-mono">{entry.error_count}</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground flex items-center gap-1">
            <Coins className="w-3 h-3" /> Tokens
          </span>
          <span className="font-mono">{entry.total_tokens.toLocaleString()}</span>
        </div>
        {entry.call_count === 0 && entry.total_tokens === 0 && (
          <p className="text-[10px] text-muted-foreground italic pt-1 border-t border-border">
            最近无调用
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function SuccessBadge({ rate }: { rate: number }) {
  const cls =
    rate >= 0.95
      ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
      : rate >= 0.8
      ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
      : rate > 0
      ? 'bg-red-500/15 text-red-300 border-red-500/30'
      : 'bg-slate-500/15 text-slate-300 border-slate-500/30'
  return (
    <Badge className={cls}>
      {(rate * 100).toFixed(1)}%
    </Badge>
  )
}
