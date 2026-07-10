import type { TicketStatus } from '@/types'

export interface TicketProgressStep {
  key: string
  label: string
}

export interface TicketProgress {
  percent: number
  label: string
  detail: string
  tone: 'active' | 'success' | 'warning' | 'error'
  currentStep: number
  steps: TicketProgressStep[]
}

export function extractUserTicketContent(content: string) {
  const marker = '【原始描述】'
  const index = content.lastIndexOf(marker)
  if (index === -1) return content.trim()

  return content.slice(index + marker.length).trim()
}

export function getTicketProgress(status: TicketStatus): TicketProgress {
  const steps = [
    { key: 'submitted', label: '已提交' },
    { key: 'understanding', label: '识别问题' },
    { key: 'processing', label: '处理方案' },
    { key: 'review', label: '结果核对' },
    { key: 'done', label: '完成' },
  ]

  switch (status) {
    case 'received':
      return {
        steps,
        percent: 12,
        currentStep: 0,
        label: '已收到工单',
        detail: '系统已收到你的问题，正在排队进入识别流程。',
        tone: 'active',
      }
    case 'classifying':
      return {
        steps,
        percent: 28,
        currentStep: 1,
        label: '正在识别问题',
        detail: '正在理解你的问题类型和处理方向。',
        tone: 'active',
      }
    case 'processing':
      return {
        steps,
        percent: 55,
        currentStep: 2,
        label: '正在生成处理方案',
        detail: 'Agent 正在结合已有资料整理可执行的处理建议。',
        tone: 'active',
      }
    case 'reviewing':
      return {
        steps,
        percent: 72,
        currentStep: 3,
        label: '正在核对结果',
        detail: '系统正在核对处理结果，确认后会展示给你。',
        tone: 'active',
      }
    case 'pending_human_review':
      return {
        steps,
        percent: 76,
        currentStep: 3,
        label: '人工复核中',
        detail: '该问题需要人工复核，请稍候。',
        tone: 'warning',
      }
    case 'waiting_user_input':
      return {
        steps,
        percent: 64,
        currentStep: 2,
        label: '等待你补充信息',
        detail: '还缺少必要信息，补充后会继续处理。',
        tone: 'warning',
      }
    case 'completed':
      return {
        steps,
        percent: 100,
        currentStep: 4,
        label: '处理完成',
        detail: '本次工单已生成处理结果。',
        tone: 'success',
      }
    case 'failed':
      return {
        steps,
        percent: 100,
        currentStep: 4,
        label: '处理遇到异常',
        detail: '处理过程中出现异常，请等待人工进一步核查。',
        tone: 'error',
      }
    default:
      return {
        steps,
        percent: 20,
        currentStep: 0,
        label: '处理中',
        detail: '工单正在处理中。',
        tone: 'active',
      }
  }
}
