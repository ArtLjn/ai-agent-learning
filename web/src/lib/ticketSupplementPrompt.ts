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
    '1. 业务对象或账号信息',
    '2. 发生时间和操作步骤',
    '3. 截图、凭证或其他可核对材料',
  ].join('\n'),
  helperText: '请尽量填写可核对的编号、时间、金额或截图说明，便于继续处理。',
}

const BILLING_PROMPT: SupplementPrompt = {
  title: '补充账务核对信息',
  placeholder: [
    '请补充以下账务核对信息：',
    '1. 订单号或账单号',
    '2. 支付流水号或交易凭证',
    '3. 扣费时间、扣费金额、套餐/账期',
    '4. 你认为异常的扣费项目或截图说明',
  ].join('\n'),
  helperText: '涉及扣费、退款、话费、账单异常时，请优先补充订单号和支付流水号。',
}

const TECHNICAL_PROMPT: SupplementPrompt = {
  title: '补充故障定位信息',
  placeholder: [
    '请补充以下故障定位信息：',
    '1. 操作入口或页面路径',
    '2. 报错码、报错截图或完整提示',
    '3. 发生时间、影响账号或设备环境',
    '4. 是否可以稳定复现',
  ].join('\n'),
  helperText: '技术问题请尽量提供操作路径、报错截图和发生时间，便于定位。',
}

export function getTicketSupplementPrompt(ticket: Pick<Ticket, 'category' | 'content'>): SupplementPrompt {
  const content = ticket.content.toLowerCase()
  const isBilling = ticket.category === 'billing'
    || /账单|扣费|费用|话费|支付|流水|订单|退款|退费|发票|套餐/.test(content)
  if (isBilling) return BILLING_PROMPT

  const isTechnical = ticket.category === 'technical'
    || /报错|无法|失败|登录|接口|超时|崩溃|异常|故障|截图/.test(content)
  if (isTechnical) return TECHNICAL_PROMPT

  return DEFAULT_PROMPT
}
