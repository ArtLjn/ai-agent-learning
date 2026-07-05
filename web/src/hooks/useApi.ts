import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

// 工单
export function useTickets(params?: Record<string, string>) {
  return useQuery({
    queryKey: ['tickets', params],
    queryFn: () => api.getTickets(params),
    refetchInterval: 10000,
  })
}

export function useTicket(id: string, refetchWhileActive = false) {
  return useQuery({
    queryKey: ['ticket', id],
    queryFn: () => api.getTicket(id),
    enabled: !!id,
    refetchInterval: refetchWhileActive
      ? (query) => {
        const ticket = query.state.data
        if (ticket && ['completed', 'failed'].includes(ticket.status)) {
          return false
        }
        return 1500
      }
      : false,
    refetchOnWindowFocus: true,
  })
}

export function useCreateTicket() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { content: string; user_id?: string }) => api.createTicket(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tickets'] }),
  })
}

// Trace
export function useTraces(params?: Record<string, string>) {
  return useQuery({
    queryKey: ['traces', params],
    queryFn: () => api.getTraces(params),
    refetchInterval: 15000,
  })
}

export function useTicketTrace(ticketId: string, isRunning?: boolean) {
  return useQuery({
    queryKey: ['ticketTrace', ticketId],
    queryFn: () => api.getTicketTrace(ticketId),
    enabled: !!ticketId,
    refetchInterval: isRunning ? 1500 : false,
    refetchOnWindowFocus: true,
  })
}

export function useTraceStats(traceId: string) {
  return useQuery({
    queryKey: ['traceStats', traceId],
    queryFn: () => api.getTraceStats(traceId),
    enabled: !!traceId,
  })
}

export function useTraceDecisions(traceId: string) {
  return useQuery({
    queryKey: ['traceDecisions', traceId],
    queryFn: () => api.getTraceDecisions(traceId),
    enabled: !!traceId,
  })
}

// Analytics
export function useAnalytics() {
  return useQuery({
    queryKey: ['analytics'],
    queryFn: () => api.getAnalytics(),
    refetchInterval: 30000,
  })
}

// Knowledge（纯代理 rag-service）
export function useKnowledge(page = 1, pageSize = 50) {
  return useQuery({
    queryKey: ['knowledge', page, pageSize],
    queryFn: () => api.getKnowledge(page, pageSize),
  })
}

export function useUploadKnowledgeText() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { title?: string; content: string; category?: string }) =>
      api.uploadKnowledgeText(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['knowledge'] }),
  })
}

export function useUploadKnowledgeFile() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (formData: FormData) => api.uploadKnowledgeFile(formData),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['knowledge'] }),
  })
}

export function useDeleteKnowledge() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (docId: string) => api.deleteKnowledge(docId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['knowledge'] }),
  })
}

// Settings
export function useSystemSettings() {
  return useQuery({
    queryKey: ['systemSettings'],
    queryFn: () => api.getSettings(),
  })
}
