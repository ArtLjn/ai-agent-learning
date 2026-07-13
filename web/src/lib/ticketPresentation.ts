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

export interface KeyMaterialPrompt {
  title: string
  helperText: string
  placeholder: string
}

export const SERVICE_TYPE_OPTIONS = [
  { value: 'account_access', label: '账号与权限' },
  { value: 'business_system', label: '业务系统' },
  { value: 'office_device', label: '办公设备' },
  { value: 'network', label: '网络环境' },
  { value: 'administrative', label: '行政服务' },
  { value: 'other', label: '其他服务' },
] as const

export type ServiceType = (typeof SERVICE_TYPE_OPTIONS)[number]['value']

const SERVICE_TYPE_LABELS = SERVICE_TYPE_OPTIONS.reduce<Record<string, string>>((acc, option) => {
  acc[option.value] = option.label
  return acc
}, {})

const MATERIAL_PROMPTS: Record<string, KeyMaterialPrompt> = {
  account_access: {
    title: '账号与权限材料',
    helperText: '可补充账号、系统入口、权限范围、报错提示和影响范围。',
    placeholder: '例如：账号 alice@example.com；系统：企业邮箱；报错：密码正确但提示账号锁定。',
  },
  business_system: {
    title: '业务系统材料',
    helperText: '可补充系统名称、页面入口、操作步骤、发生时间和错误提示。',
    placeholder: '例如：系统：CRM；入口：客户列表；操作：导出报表；错误提示：请求超时。',
  },
  office_device: {
    title: '办公设备材料',
    helperText: '可补充设备类型、资产编号、故障现象、所在位置和是否影响办公。',
    placeholder: '例如：设备：打印机；位置：3F 西区；现象：卡纸后无法继续打印。',
  },
  network: {
    title: '网络环境材料',
    helperText: '可补充办公区域、网络类型、受影响设备、发生时间和错误截图说明。',
    placeholder: '例如：区域：4F 会议室；网络：Wi-Fi；现象：连接后无法访问内网系统。',
  },
  administrative: {
    title: '行政服务材料',
    helperText: '可补充服务事项、期望时间、地点、人数或相关审批信息。',
    placeholder: '例如：事项：会议室空调维修；地点：2F A201；期望处理时间：今天下午。',
  },
  other: {
    title: '关键材料',
    helperText: '可选填写相关系统、账号、时间、地点、截图说明或其他上下文。',
    placeholder: '请补充能帮助服务台判断问题的关键信息。',
  },
}

export function getServiceTypeLabel(serviceType: string | null | undefined) {
  if (!serviceType) return '未选择'
  return SERVICE_TYPE_LABELS[serviceType] || serviceType
}

export function getKeyMaterialPrompt(serviceType: string | null | undefined): KeyMaterialPrompt {
  return MATERIAL_PROMPTS[serviceType || ''] || MATERIAL_PROMPTS.other
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
        detail: '系统正在结合已有资料整理可执行的处理建议。',
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
