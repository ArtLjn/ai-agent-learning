// D-04 Token 成本控制台主页面（shadcn/ui + Recharts 精简版）。
// 设计来源：12 号文档第 5 节 + farm-manager TokenDashboard 948 行精简。
// 范围：Token 总览 + 模型用量 + 趋势图 + 配额面板。

import { useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { RefreshCw as ReloadIcon } from 'lucide-react'
import { api, ApiError } from '@/lib/api'
import type {
  TokenDailyResponse,
  TokenHourlyResponse,
  TokenSummaryResponse,
  UserQuotaResponse,
} from '@/types'
import {
  CALL_TYPES,
  type CallType,
  CALL_TYPE_LABEL,
  colorForCallType,
  computeRange,
  formatCompactNumber,
  formatDateKey,
  formatNumber,
  normalizeModelStats,
  type RangeMode,
  toNumber,
} from './shared'

type TrendPoint = {
  key: string
  label: string
  date: string
  hour: string
  prompt: number
  completion: number
  total: number
  requests: number
}

const HOURS_24 = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'))

export default function TokenDashboard() {
  const [rangeMode, setRangeMode] = useState<RangeMode>('week')
  const [userIdInput, setUserIdInput] = useState<string>('')
  const [selectedUserId, setSelectedUserId] = useState<string | undefined>(undefined)
  const [selectedModel, setSelectedModel] = useState<string | undefined>(undefined)
  const [summary, setSummary] = useState<TokenSummaryResponse | null>(null)
  const [daily, setDaily] = useState<TokenDailyResponse | null>(null)
  const [hourly, setHourly] = useState<TokenHourlyResponse | null>(null)
  const [quota, setQuota] = useState<UserQuotaResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [quotaLoading, setQuotaLoading] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const todayStr = useMemo(() => formatDateKey(new Date()), [])
  const range = useMemo(() => computeRange(rangeMode), [rangeMode])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadFailed(false)
    const params = {
      days: range.days,
      user_id: selectedUserId,
    }
    const hourlyParams = {
      user_id: selectedUserId,
      model: selectedModel,
      date: todayStr,
    }
    Promise.all([
      api.getTokenSummary(params),
      api.getTokenDaily({ date: todayStr, user_id: selectedUserId }),
      api.getTokenHourly(hourlyParams),
    ])
      .then(([sum, day, hr]) => {
        if (!cancelled) {
          setSummary(sum)
          setDaily(day)
          setHourly(hr)
        }
      })
      .catch(() => {
        if (!cancelled) setLoadFailed(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [range, selectedUserId, selectedModel, todayStr, refreshKey])

  useEffect(() => {
    let cancelled = false
    if (!selectedUserId) {
      setQuota(null)
      return () => {
        cancelled = true
      }
    }
    setQuotaLoading(true)
    api
      .getUserQuota(selectedUserId)
      .then((res) => {
        if (!cancelled) setQuota(res)
      })
      .catch(() => {
        if (!cancelled) setQuota(null)
      })
      .finally(() => {
        if (!cancelled) setQuotaLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedUserId])

  const modelStats = useMemo(
    () =>
      normalizeModelStats(summary?.by_model).filter(
        (m) => !selectedModel || m.model === selectedModel,
      ),
    [summary, selectedModel],
  )

  const modelOptions = useMemo(() => {
    if (!summary) return [] as string[]
    const set = new Set<string>()
    Object.values(summary.by_model).forEach((m) => set.add(m.model))
    return Array.from(set).sort()
  }, [summary])

  const todayUsage = useMemo(
    () =>
      (daily?.items ?? [])
        .filter((item) => !selectedModel || item.model === selectedModel)
        .reduce((sum, item) => sum + toNumber(item.total_tokens), 0),
    [daily, selectedModel],
  )

  const hourlyTrend = useMemo<TrendPoint[]>(() => {
    const rows = new Map<string, TrendPoint>()
    if (range.mode === 'day') {
      HOURS_24.forEach((hour) => {
        const key = `${range.startDate}-${hour}`
        rows.set(key, {
          key,
          label: `${hour}:00`,
          date: range.startDate,
          hour,
          prompt: 0,
          completion: 0,
          total: 0,
          requests: 0,
        })
      })
    }
    ;(hourly?.items ?? []).forEach((item) => {
      const date = range.mode === 'day' ? range.startDate : todayStr
      const hour = item.hour ?? '00'
      const key = `${date}-${hour}`
      const row =
        rows.get(key) ??
        {
          key,
          label: range.mode === 'day' ? `${hour}:00` : `${date.slice(5)} ${hour}:00`,
          date,
          hour,
          prompt: 0,
          completion: 0,
          total: 0,
          requests: 0,
        }
      row.prompt += toNumber(item.prompt_tokens)
      row.completion += toNumber(item.completion_tokens)
      row.total += toNumber(item.total_tokens)
      row.requests += toNumber(item.request_count)
      rows.set(key, row)
    })
    return Array.from(rows.values()).sort((a, b) => a.key.localeCompare(b.key))
  }, [hourly, range, todayStr])

  const totalTokens = summary?.total_tokens ?? 0
  const totalRequests = summary?.total_requests ?? 0
  const maxModelTokens = Math.max(1, ...modelStats.map((m) => m.total_tokens))

  return (
    <div className="space-y-4 p-4 md:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Token 成本控制台</h1>
          <p className="text-sm text-muted-foreground">
            {range.label} · {range.startDate} 至 {range.endDate}
            {selectedUserId ? ` · 用户：${selectedUserId}` : ' · 全部用户'}
            {selectedModel ? ` · 模型：${selectedModel}` : ''}
          </p>
        </div>
        <Badge variant={loadFailed ? 'destructive' : 'default'}>
          {loadFailed ? '加载失败' : loading ? '加载中' : '已加载'}
        </Badge>
      </header>

      {/* 筛选条 */}
      <Card>
        <CardContent className="grid grid-cols-1 gap-3 md:grid-cols-4">
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">用户 ID</label>
            <div className="flex gap-2">
              <Input
                placeholder="留空查看全部"
                value={userIdInput}
                onChange={(e) => setUserIdInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') setSelectedUserId(userIdInput.trim() || undefined)
                }}
              />
              <Button
                variant="secondary"
                onClick={() => setSelectedUserId(userIdInput.trim() || undefined)}
              >
                应用
              </Button>
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">模型</label>
            <Select
              value={selectedModel ?? '__all__'}
              onValueChange={(v: string | null) =>
                setSelectedModel(!v || v === '__all__' ? undefined : v)
              }
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="全部模型" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">全部模型</SelectItem>
                {modelOptions.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">范围</label>
            <div className="flex gap-1">
              {(['day', 'week', 'month'] as RangeMode[]).map((m) => (
                <Button
                  key={m}
                  size="sm"
                  variant={rangeMode === m ? 'default' : 'outline'}
                  onClick={() => setRangeMode(m)}
                >
                  {m === 'day' ? '当日' : m === 'week' ? '近 7 天' : '本月'}
                </Button>
              ))}
            </div>
          </div>
          <div className="flex items-end">
            <Button
              className="w-full"
              onClick={() => setRefreshKey((k) => k + 1)}
              disabled={loading}
            >
              <ReloadIcon className="mr-2 h-4 w-4" />
              刷新
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* 顶部 4 统计卡 */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label={`总 Token（${range.label}）`} value={totalTokens} loading={loading} />
        <StatCard label={`总请求（${range.label}）`} value={totalRequests} loading={loading} />
        <StatCard label="今日 Token" value={todayUsage} loading={loading} />
        <QuotaCard quota={quota} loading={quotaLoading} hasSelected={!!selectedUserId} />
      </div>

      {/* 模型用量列表 */}
      <Card>
        <CardHeader>
          <CardTitle>模型用量（按 model + call_type）</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Skeleton className="h-32 w-full" />
          ) : modelStats.length === 0 ? (
            <EmptyBlock description="当前筛选下暂无模型用量" />
          ) : (
            <ModelUsageRows items={modelStats} maxModelTokens={maxModelTokens} />
          )}
        </CardContent>
      </Card>

      {/* 趋势图 */}
      <Card>
        <CardHeader>
          <CardTitle>{range.mode === 'day' ? '当日小时趋势' : 'Token 趋势'}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <Skeleton className="h-64 w-full" />
          ) : hourlyTrend.length === 0 ? (
            <EmptyBlock description="今日暂无可用于趋势的 Token 数据" />
          ) : (
            <TrendChart data={hourlyTrend} />
          )}
        </CardContent>
      </Card>

      {/* call_type 分布柱图 */}
      <Card>
        <CardHeader>
          <CardTitle>call_type 分布</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? <Skeleton className="h-48 w-full" /> : <CallTypeBar items={modelStats} />}
        </CardContent>
      </Card>
    </div>
  )
}

function StatCard({
  label,
  value,
  loading,
}: {
  label: string
  value: number
  loading: boolean
}) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="text-xs text-muted-foreground">{label}</div>
        {loading ? (
          <Skeleton className="mt-2 h-7 w-24" />
        ) : (
          <div className="mt-1 text-2xl font-semibold tabular-nums">
            {formatCompactNumber(value)}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function QuotaCard({
  quota,
  loading,
  hasSelected,
}: {
  quota: UserQuotaResponse | null
  loading: boolean
  hasSelected: boolean
}) {
  const monthlyPercent =
    quota && quota.monthly_limit > 0
      ? Math.min(100, Math.round((quota.monthly_usage / quota.monthly_limit) * 100))
      : 0
  return (
    <Card size="sm">
      <CardContent>
        <div className="text-xs text-muted-foreground">月配额使用率</div>
        {loading ? (
          <Skeleton className="mt-2 h-7 w-24" />
        ) : !hasSelected || !quota ? (
          <div className="mt-1 text-sm text-muted-foreground">
            {hasSelected ? '该用户暂无配额数据' : '选择用户查看配额'}
          </div>
        ) : (
          <div className="mt-1 space-y-1">
            <div className="text-2xl font-semibold tabular-nums">{monthlyPercent}%</div>
            <div className="text-xs text-muted-foreground tabular-nums">
              {formatCompactNumber(quota.monthly_usage)} /{' '}
              {formatCompactNumber(quota.monthly_limit)}
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded bg-muted">
              <div
                className={
                  monthlyPercent >= 80
                    ? 'h-full bg-rose-500'
                    : monthlyPercent >= 60
                      ? 'h-full bg-amber-500'
                      : 'h-full bg-emerald-500'
                }
                style={{ width: `${monthlyPercent}%` }}
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ModelUsageRows({
  items,
  maxModelTokens,
}: {
  items: ReturnType<typeof normalizeModelStats>
  maxModelTokens: number
}) {
  return (
    <div className="space-y-1.5">
      {items.map((item) => {
        const widthPercent = Math.max(
          4,
          Math.round((item.total_tokens / maxModelTokens) * 100),
        )
        const promptPercent =
          item.total_tokens > 0
            ? Math.round((item.prompt_tokens / item.total_tokens) * 100)
            : 0
        return (
          <div
            key={`${item.model}-${item.call_type}`}
            className="grid grid-cols-12 items-center gap-2 rounded border border-border/50 px-2 py-1.5"
          >
            <div className="col-span-3 min-w-0">
              <div className="truncate text-sm font-medium">{item.model}</div>
              <div className="truncate text-xs text-muted-foreground">{item.call_type}</div>
            </div>
            <div className="col-span-6 min-w-0">
              <div className="flex h-4 overflow-hidden rounded bg-muted">
                <div className="flex" style={{ width: `${widthPercent}%` }}>
                  <div
                    className="h-full"
                    style={{ width: `${promptPercent}%`, background: '#60a5fa' }}
                  />
                  <div
                    className="h-full"
                    style={{ width: `${100 - promptPercent}%`, background: '#34d399' }}
                  />
                </div>
              </div>
              <div className="mt-0.5 flex justify-between text-[10px] text-muted-foreground tabular-nums">
                <span>P {formatCompactNumber(item.prompt_tokens)}</span>
                <span>C {formatCompactNumber(item.completion_tokens)}</span>
              </div>
            </div>
            <div className="col-span-2 text-right text-sm font-semibold tabular-nums">
              {formatCompactNumber(item.total_tokens)}
            </div>
            <div className="col-span-1 text-right text-xs text-muted-foreground tabular-nums">
              {formatNumber(item.request_count)}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function TrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatCompactNumber(Number(v))} />
        <Tooltip
          formatter={(value: unknown, name: unknown) => [
            formatNumber(Number(value)),
            name === 'total' ? 'Token' : name === 'requests' ? '请求' : String(name),
          ]}
          contentStyle={{ fontSize: 12 }}
        />
        <Legend formatter={(entry: unknown) => (entry === 'total' ? 'Token' : '请求')} />
        <Line type="monotone" dataKey="total" stroke="#60a5fa" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="requests" stroke="#fbbf24" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}

function CallTypeBar({ items }: { items: ReturnType<typeof normalizeModelStats> }) {
  const byCallType = new Map<CallType, number>()
  CALL_TYPES.forEach((ct) => byCallType.set(ct, 0))
  items.forEach((it) => {
    const key = (it.call_type as CallType) ?? 'process'
    byCallType.set(key, (byCallType.get(key) ?? 0) + it.total_tokens)
  })
  const data = CALL_TYPES.map((ct) => ({
    call_type: CALL_TYPE_LABEL[ct],
    tokens: byCallType.get(ct) ?? 0,
    fill: colorForCallType(ct),
  }))

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
        <XAxis dataKey="call_type" tick={{ fontSize: 11 }} />
        <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatCompactNumber(Number(v))} />
        <Tooltip
          formatter={(value: unknown) => [formatNumber(Number(value)), 'Token']}
          contentStyle={{ fontSize: 12 }}
        />
        <Bar dataKey="tokens" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function EmptyBlock({ description }: { description: string }) {
  return (
    <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
      {description}
    </div>
  )
}

// 处理 ApiError 的友好提示（页面错误兜底）
export function isTokenDashboardLoadError(err: unknown): err is ApiError {
  return err instanceof ApiError
}
