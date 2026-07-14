import type { Ticket } from '@/types'

interface SupplementPrompt {
  title: string
  placeholder: string
  helperText: string
}

const DEFAULT_PROMPT: SupplementPrompt = {
  title: '补充关键信息',
  placeholder: [
    '请补充与本次问题直接相关的信息，例如：',
    '1. 员工号、系统入口、办公地点或业务对象',
    '2. 发生时间和操作步骤',
    '3. 截图、凭证或其他可核对材料',
  ].join('\n'),
  helperText: '请尽量填写可核对的编号、时间、地点、审批单号或截图说明，便于继续处理。',
}

const BILLING_PROMPT: SupplementPrompt = {
  title: '补充费用/薪酬/报销核对信息',
  placeholder: [
    '请补充以下可核对信息：',
    '1. 报销单号、出差审批单、加班审批单或工资月份',
    '2. 费用类型、补贴类型、发票问题或付款节点',
    '3. PeopleHub / FinFlow 中看到的状态',
    '4. 截图请遮挡身份证号、银行卡号和具体薪酬金额',
  ].join('\n'),
  helperText: '涉及报销、差旅、餐补、薪酬、社保公积金时，请优先补充审批单号或月份。',
}

const TECHNICAL_PROMPT: SupplementPrompt = {
  title: '补充 IT/办公支持定位信息',
  placeholder: [
    '请补充以下定位信息：',
    '1. 系统入口、设备资产编号、会议室或办公地点',
    '2. 报错码、报错截图或完整提示',
    '3. 发生时间、影响账号、网络或设备环境',
    '4. 是否影响会议、入职、远程办公或多人办公',
  ].join('\n'),
  helperText: 'IT 和办公支持问题请尽量提供入口、地点、资产编号、报错截图和发生时间。',
}

export function getTicketSupplementPrompt(ticket: Pick<Ticket, 'category' | 'content'>): SupplementPrompt {
  const content = ticket.content.toLowerCase()
  const isBilling = ticket.category === 'billing'
    || /报销|差旅|餐补|补贴|工资|薪酬|社保|公积金|费用|付款|发票|预算|出差/.test(content)
  if (isBilling) return BILLING_PROMPT

  const isTechnical = ticket.category === 'technical'
    || /报错|无法|失败|登录|超时|异常|故障|截图|vpn|cloudid|邮箱|电脑|打印机|会议室|网络/.test(content)
  if (isTechnical) return TECHNICAL_PROMPT

  return DEFAULT_PROMPT
}
