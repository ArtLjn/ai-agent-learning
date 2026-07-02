import type {
  Analytics,
  ApiRecord,
  ChangePasswordRequest,
  ChangePasswordResponse,
  KnowledgeListResponse,
  RegisterRequest,
  RegisterResponse,
  SystemSettings,
  Ticket,
  TicketCategory,
  TicketCreateResponse,
  TicketFeedbackResponse,
  TicketListParams,
  TicketMessage,
  TicketMessageCreateRequest,
  TraceDecisionsResponse,
  TraceDetail,
  TraceListResponse,
  TraceStatsResponse,
  UpdateMeRequest,
  UserProfile,
} from '@/types'

const BASE_URL = '/api'

export class ApiError extends Error {
  status: number
  detail?: string
  body?: unknown

  constructor(status: number, statusText: string, detail?: string, body?: unknown) {
    super(detail || `API Error: ${status} ${statusText}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}

/** 401 时跳转登录页（避免循环跳转：当前已经在登录页则不跳）。 */
function handleUnauthorized() {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

export async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (res.status === 401) {
    handleUnauthorized()
  }
  if (!res.ok) {
    let detail: string | undefined
    let body: unknown = undefined
    try {
      body = await res.json()
      const candidate = (body as Record<string, unknown> | null)?.detail
      detail = typeof candidate === 'string' ? candidate : undefined
    } catch {
      detail = undefined
    }
    throw new ApiError(res.status, res.statusText, detail, body)
  }
  return res.json()
}

export interface AuthState {
  logged_in: boolean
  username: string | null
  auth_enabled: boolean
}

export const api = {
  // 鉴权
  login: (username: string, password: string) =>
    request<{ username: string; logged_in: boolean }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  register: (payload: RegisterRequest) =>
    request<RegisterResponse>('/auth/register', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  logout: () => request<{ logged_out: boolean }>('/auth/logout', { method: 'POST' }),
  getAuthState: () => request<AuthState>('/auth/me'),

  // 用户自助（U-03 / U-04）
  getMe: () => request<UserProfile>('/users/me'),
  updateMe: (payload: UpdateMeRequest) =>
    request<UserProfile>('/users/me', {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  changePassword: (payload: ChangePasswordRequest) =>
    request<ChangePasswordResponse>('/users/me/password', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  // 工单
  getTickets: (params?: TicketListParams) => {
    const qs = params ? '?' + new URLSearchParams(Object.entries(params)).toString() : ''
    return request<Ticket[]>(`/tickets${qs}`)
  },
  getTicket: (id: string) => request<Ticket>(`/tickets/${id}`),
  createTicket: (data: { content: string; user_id?: string }) =>
    request<TicketCreateResponse>('/tickets', { method: 'POST', body: JSON.stringify(data) }),
  generateMockTicketQuestion: (category?: TicketCategory) => {
    const qs = category ? `?${new URLSearchParams({ category }).toString()}` : ''
    return request<{ prompt: string; generation_mode: string; knowledge_title: string | null; category: TicketCategory | null }>(
      `/tickets/mock-question${qs}`,
    )
  },
  submitFeedback: (id: string, satisfied: boolean) =>
    request<TicketFeedbackResponse>(`/tickets/${id}/feedback`, { method: 'POST', body: JSON.stringify({ satisfied }) }),
  getTicketMessages: (id: string) =>
    request<TicketMessage[]>(`/tickets/${encodeURIComponent(id)}/messages`),
  createTicketMessage: (id: string, data: TicketMessageCreateRequest) =>
    request<ApiRecord>(`/tickets/${encodeURIComponent(id)}/messages`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Trace
  getTraces: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<TraceListResponse>(`/traces${qs}`)
  },
  getTicketTrace: (ticketId: string) => request<TraceDetail>(`/tickets/${ticketId}/trace`),
  getTraceStats: (traceId: string) => request<TraceStatsResponse>(`/traces/${traceId}/stats`),
  getTraceDecisions: (traceId: string) => request<TraceDecisionsResponse>(`/traces/${traceId}/decisions`),

  // Analytics
  getAnalytics: () => request<Analytics>('/analytics'),

  // Knowledge
  getKnowledge: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<KnowledgeListResponse>(`/knowledge${qs}`)
  },
  uploadKnowledge: (data: { title: string; content: string; category?: string }) =>
    request<ApiRecord>('/knowledge', { method: 'POST', body: JSON.stringify(data) }),

  // Settings
  getSettings: () => request<SystemSettings>('/settings'),

  // Health（不鉴权，供前端探活）
  getHealth: () => request<ApiRecord>('/health'),
}
