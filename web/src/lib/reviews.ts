import { request } from '@/lib/api'
import type {
  ReviewDecisionRequest,
  ReviewDecisionResponse,
  ReviewDetail,
  ReviewQueueResponse,
  ReviewStats,
} from '@/types'

export interface ReviewQueryParams {
  status?: string
  trigger_type?: string
  category?: string
  priority?: string
  limit?: number
  offset?: number
}

function buildQuery(params?: ReviewQueryParams): string {
  if (!params) return ''
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== '',
  )
  if (entries.length === 0) return ''
  const sp = new URLSearchParams()
  for (const [k, v] of entries) sp.set(k, String(v))
  return '?' + sp.toString()
}

export const reviewsApi = {
  getReviewQueue: (params?: ReviewQueryParams) =>
    request<ReviewQueueResponse>(`/reviews/queue${buildQuery(params)}`),

  getReviewDetail: (ticketId: string) =>
    request<ReviewDetail>(`/reviews/${encodeURIComponent(ticketId)}`),

  submitDecision: (ticketId: string, body: ReviewDecisionRequest) =>
    request<ReviewDecisionResponse>(
      `/reviews/${encodeURIComponent(ticketId)}/decision`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  getReviewStats: () => request<ReviewStats>('/reviews/stats'),
}
