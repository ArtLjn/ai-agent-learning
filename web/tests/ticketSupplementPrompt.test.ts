import assert from 'node:assert/strict'
import test from 'node:test'

import { getTicketSupplementPrompt } from '../src/lib/ticketSupplementPrompt.ts'

test('费用和报销场景提示用户补充可核对字段', () => {
  const prompt = getTicketSupplementPrompt({
    category: 'billing',
    content: '本月加班餐补没有发放，需要核查 PeopleHub 和 FinFlow 状态',
  })

  assert.equal(prompt.title, '补充费用/薪酬/报销核对信息')
  assert.match(prompt.placeholder, /报销单号/)
  assert.match(prompt.placeholder, /加班审批单/)
  assert.match(prompt.placeholder, /PeopleHub/)
  assert.match(prompt.placeholder, /薪酬金额/)
})

test('技术问题提示用户补充故障定位字段', () => {
  const prompt = getTicketSupplementPrompt({
    category: 'technical',
    content: '登录接口超时失败',
  })

  assert.equal(prompt.title, '补充 IT/办公支持定位信息')
  assert.match(prompt.placeholder, /系统入口/)
  assert.match(prompt.placeholder, /报错/)
})
