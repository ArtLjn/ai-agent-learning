import type {
  AdminTraceDetail,
  AdminTraceListResponse,
  Analytics,
  ApiRecord,
  AuditLogListResponse,
  ChangePasswordRequest,
  ChangePasswordResponse,
  KnowledgeListResponse,
  RegisterRequest,
  RegisterResponse,
  Span,
  SystemConfig,
  SystemSettings,
  Ticket,
  TicketCategory,
  TicketCreateResponse,
  TicketFeedbackResponse,
  TicketListParams,
  TicketMessage,
  TicketMessageCreateRequest,
  TokenDailyResponse,
  TokenHourlyResponse,
  TokenSummaryResponse,
  TraceDecisionsResponse,
  TraceDetail,
  TraceListResponse,
  TraceStatsResponse,
  UpdateMeRequest,
  UserProfile,
  UserQuotaResponse,
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
  role: string | null
}

export interface MyPermissions {
  role: string
  routes: string[]
}

export const api = {
  // 通用底层方法（供特殊场景直接调用 path，如 admin/users）
  request,

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
  getMyPermissions: () => request<MyPermissions>('/users/me/permissions'),
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

  // A-06 系统配置（只读脱敏，6 类）
  getSystemConfig: () => request<SystemConfig>('/admin/config'),

  // A-07 操作日志审计
  getAuditLogs: (params?: Record<string, string>) => {
    const qs = params ? '?' + new URLSearchParams(params).toString() : ''
    return request<AuditLogListResponse>(`/admin/audit-logs${qs}`)
  },

  // Health（不鉴权，供前端探活）
  getHealth: () => request<ApiRecord>('/health'),

  // ============================================================
  // D-01 / D-04 开发人员工作台（admin trace + token stats）
  // ============================================================

  // D-01 Trace 决策树
  getAdminTraces: (params?: Record<string, string | number>) => {
    const qs = params
      ? '?' +
        new URLSearchParams(
          Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)])),
        ).toString()
      : ''
    return request<AdminTraceListResponse>(`/admin/traces${qs}`)
  },
  getAdminTrace: (ticketId: string) =>
    request<AdminTraceDetail>(`/admin/traces/${encodeURIComponent(ticketId)}`),
  getAdminSpanDetail: (ticketId: string, spanId: string) =>
    request<Span>(`/admin/traces/${encodeURIComponent(ticketId)}/spans/${encodeURIComponent(spanId)}`),

  // D-04 Token 成本控制台
  getTokenSummary: (params?: { days?: number; user_id?: string }) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params ?? {}).map(([k, v]) => [k, String(v ?? '')]),
      ),
    ).toString()
    return request<TokenSummaryResponse>(`/admin/stats/tokens${qs ? '?' + qs : ''}`)
  },
  getTokenDaily: (params?: { date?: string; user_id?: string }) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params ?? {}).map(([k, v]) => [k, String(v ?? '')]),
      ),
    ).toString()
    return request<TokenDailyResponse>(`/admin/stats/tokens/daily${qs ? '?' + qs : ''}`)
  },
  getTokenHourly: (params?: { date?: string; user_id?: string; model?: string }) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params ?? {}).map(([k, v]) => [k, String(v ?? '')]),
      ),
    ).toString()
    return request<TokenHourlyResponse>(`/admin/stats/tokens/hourly${qs ? '?' + qs : ''}`)
  },
  getUserQuota: (userId: string) =>
    request<UserQuotaResponse>(`/admin/stats/quota/${encodeURIComponent(userId)}`),
}
