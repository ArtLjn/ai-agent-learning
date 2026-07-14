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
  { value: 'onboarding', label: '新人入职' },
  { value: 'policy_attendance', label: '制度与考勤' },
  { value: 'benefits_payroll', label: '薪酬福利' },
  { value: 'expense_travel', label: '报销差旅' },
  { value: 'procurement_assets', label: '采购资产' },
  { value: 'account_access', label: '账号权限' },
  { value: 'it_network_device', label: 'IT 与设备' },
  { value: 'office_admin', label: '行政办公' },
  { value: 'mailroom_commute', label: '快递通勤' },
  { value: 'training_performance', label: '培训绩效' },
  { value: 'security_compliance', label: '安全合规' },
  { value: 'legal_contract', label: '合同用印' },
  { value: 'other', label: '其他服务' },
] as const

export type ServiceType = (typeof SERVICE_TYPE_OPTIONS)[number]['value']

export const TICKET_CATEGORY_OPTIONS = [
  { value: 'inquiry', label: '制度流程咨询' },
  { value: 'technical', label: 'IT 与办公支持' },
  { value: 'billing', label: '费用薪酬报销' },
  { value: 'complaint', label: '风险投诉升级' },
] as const

export const TICKET_CATEGORY_LABELS = TICKET_CATEGORY_OPTIONS.reduce<Record<string, string>>(
  (acc, option) => {
    acc[option.value] = option.label
    return acc
  },
  {},
)

const LEGACY_SERVICE_TYPE_LABELS: Record<string, string> = {
  business_system: '业务系统',
  office_device: '办公设备',
  network: '网络环境',
  administrative: '行政服务',
}

const SERVICE_TYPE_LABELS = {
  ...LEGACY_SERVICE_TYPE_LABELS,
  ...SERVICE_TYPE_OPTIONS.reduce<Record<string, string>>((acc, option) => {
    acc[option.value] = option.label
    return acc
  }, {}),
}

const MATERIAL_PROMPTS: Record<string, KeyMaterialPrompt> = {
  onboarding: {
    title: '补充材料（可选）',
    helperText: '可补充员工号、入职日期、办公地点、缺失账号或设备，便于云舟服务台定位。',
    placeholder: '例如：员工号 E10240；7 月 15 日入职上海研发中心；邮箱未开通，VPN 无权限。',
  },
  policy_attendance: {
    title: '补充材料（可选）',
    helperText: '可补充制度主题、日期、审批单号、PeopleHub 或钉钉截图说明。',
    placeholder: '例如：7 月 13 日忘打卡；补卡审批单 BC-20260713-09；想确认加班餐补规则。',
  },
  benefits_payroll: {
    title: '补充材料（可选）',
    helperText: '可补充月份、福利类型、补贴类型、PeopleHub 记录；薪酬截图请遮挡敏感金额。',
    placeholder: '例如：6 月工资单未显示夜间餐补；加班审批 OT-20260628-018 已通过。',
  },
  expense_travel: {
    title: '补充材料（可选）',
    helperText: '可补充报销单号、出差审批单、发票问题、付款节点或差旅平台错误。',
    placeholder: '例如：FinFlow 报销单 EX-202607-042 被退回；提示发票抬头不一致。',
  },
  procurement_assets: {
    title: '补充材料（可选）',
    helperText: '可补充采购品类、数量、用途、成本中心、资产编号或期望到货时间。',
    placeholder: '例如：团队需采购 2 台显示器和 1 个设计软件席位；成本中心 RD-AI。',
  },
  account_access: {
    title: '补充材料（可选）',
    helperText: '可补充 CloudID 账号、系统入口、权限范围、审批单号、报错提示和影响范围。',
    placeholder: '例如：账号 alice@yunzhou.example；系统：CloudID；报错：MFA 通过后提示无权限。',
  },
  it_network_device: {
    title: '补充材料（可选）',
    helperText: '可补充设备资产编号、YunVPN 错误、办公地点、网络类型、发生时间和截图说明。',
    placeholder: '例如：YunVPN 连接成功但打不开 PeopleHub；上海 A 座 4F；错误截图已上传。',
  },
  office_admin: {
    title: '补充材料（可选）',
    helperText: '可补充办公地点、门禁点位、会议室、工位、访客或行政服务时间要求。',
    placeholder: '例如：客户 14:00 到访上海 A 座；需访客预约和临时停车；接待人 E10240。',
  },
  mailroom_commute: {
    title: '补充材料（可选）',
    helperText: '可补充运单号、班车线路、车牌、餐厅窗口、发生日期或收发室记录。',
    placeholder: '例如：顺丰 SF123456；合同原件显示签收但收发室未通知领取。',
  },
  training_performance: {
    title: '补充材料（可选）',
    helperText: '可补充 LearnHub 课程、绩效周期、OKR 页面、证明类型或截止日期。',
    placeholder: '例如：LearnHub 新人必修课已完成但 PeopleHub 未同步记录。',
  },
  security_compliance: {
    title: '补充材料（可选）',
    helperText: '请勿粘贴敏感原文；可说明事件时间、渠道、文件类型、审批单和影响范围。',
    placeholder: '例如：DLP 拦截合同附件外发；已有数据外发审批 DA-20260714-02。',
  },
  legal_contract: {
    title: '补充材料（可选）',
    helperText: '可补充合同类型、对方主体、审批单号、签署截止日和是否涉及用印。',
    placeholder: '例如：NDA 审批单 CT-202607-008 卡在法务节点；明天客户要求盖章。',
  },
  other: {
    title: '补充材料（可选）',
    helperText: '可选填写相关系统、账号、时间、地点、截图说明或其他上下文。',
    placeholder: '请补充能帮助云舟服务台判断问题的员工号、系统、时间、地点或审批单号。',
  },
}

MATERIAL_PROMPTS.business_system = MATERIAL_PROMPTS.account_access
MATERIAL_PROMPTS.office_device = MATERIAL_PROMPTS.it_network_device
MATERIAL_PROMPTS.network = MATERIAL_PROMPTS.it_network_device
MATERIAL_PROMPTS.administrative = MATERIAL_PROMPTS.office_admin

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
