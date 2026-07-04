// D-04 Token Dashboard 共享常量与工具（精简自 farm-manager dashboard-shared.ts）。

import type { TokenSummaryBucket } from '@/types'

// call_type 6 枚举（与后端 TOKEN_CALL_TYPES 对齐，详见 12 号文档第 7 节）
export const CALL_TYPES = ['intent', 'classify', 'process', 'review', 'coordinator', 'rag'] as const
export type CallType = (typeof CALL_TYPES)[number]

export const CALL_TYPE_LABEL: Record<CallType, string> = {
  intent: '意图理解',
  classify: '分类',
  process: 'ReAct 处理',
  review: '质量审核',
  coordinator: '协调',
  rag: 'RAG 检索',
}

export const CALL_TYPE_COLOR: Record<CallType, string> = {
  intent: '#a78bfa', // purple-400
  classify: '#60a5fa', // blue-400
  process: '#34d399', // emerald-400
  review: '#fbbf24', // amber-400
  coordinator: '#fb7185', // rose-400
  rag: '#22d3ee', // cyan-400
}

export function colorForCallType(callType: string): string {
  return CALL_TYPE_COLOR[callType as CallType] ?? '#9ca3af' // gray-400 兜底
}

export const toNumber = (value: unknown): number => {
  const num = Number(value ?? 0)
  return Number.isFinite(num) ? num : 0
}

export const formatNumber = (value: number): string =>
  Math.round(value).toLocaleString()

export const formatCompactNumber = (value: number): string => {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 1 : 2)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`
  return formatNumber(value)
}

export type NormalizedModelStats = {
  model: string
  call_type: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  request_count: number
}

export function normalizeModelStats(
  byModel: Record<string, TokenSummaryBucket> | undefined,
): NormalizedModelStats[] {
  if (!byModel) return []
  return Object.values(byModel)
    .map((m) => ({
      model: m.model,
      call_type: m.call_type,
      prompt_tokens: toNumber(m.prompt_tokens),
      completion_tokens: toNumber(m.completion_tokens),
      total_tokens: toNumber(m.total_tokens),
      request_count: toNumber(m.request_count),
    }))
    .sort((a, b) => b.total_tokens - a.total_tokens)
}

export function formatDateKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate(),
  ).padStart(2, '0')}`
}

export function addDays(date: Date, offset: number): Date {
  const next = new Date(date)
  next.setDate(next.getDate() + offset)
  return next
}

export type RangeMode = 'day' | 'week' | 'month'

export function computeRange(mode: RangeMode): {
  mode: RangeMode
  days: number
  startDate: string
  endDate: string
  label: string
} {
  const today = new Date()
  const start =
    mode === 'day'
      ? today
      : mode === 'week'
        ? addDays(today, -6)
        : new Date(today.getFullYear(), today.getMonth(), 1)
  const days = Math.max(1, Math.floor((today.getTime() - start.getTime()) / 86_400_000) + 1)
  return {
    mode,
    days,
    startDate: formatDateKey(start),
    endDate: formatDateKey(today),
    label: mode === 'day' ? '当日' : mode === 'week' ? '近 7 天' : '本月',
  }
}
