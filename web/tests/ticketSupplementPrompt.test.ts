import assert from 'node:assert/strict'
import test from 'node:test'

import { getTicketSupplementPrompt } from '../src/lib/ticketSupplementPrompt.ts'

test('账务和订单缺失场景提示用户补充可核对字段', () => {
  const prompt = getTicketSupplementPrompt({
    category: 'billing',
    content: '本月话费异常增加50元且无通知，需要核查订单缺失',
  })

  assert.equal(prompt.title, '补充账务核对信息')
  assert.match(prompt.placeholder, /订单号/)
  assert.match(prompt.placeholder, /支付流水号/)
  assert.match(prompt.placeholder, /扣费时间/)
  assert.match(prompt.placeholder, /扣费金额/)
})

test('技术问题提示用户补充故障定位字段', () => {
  const prompt = getTicketSupplementPrompt({
    category: 'technical',
    content: '登录接口超时失败',
  })

  assert.equal(prompt.title, '补充故障定位信息')
  assert.match(prompt.placeholder, /操作入口/)
  assert.match(prompt.placeholder, /报错/)
})
