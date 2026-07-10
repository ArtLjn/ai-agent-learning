import assert from 'node:assert/strict'
import test from 'node:test'

import { extractUserTicketContent, getTicketProgress } from '../src/lib/ticketPresentation.ts'

test('用户视角只展示原始提交内容', () => {
  const content = [
    '【问题标题】阿里云OSS杭州节点不明扣费咨询',
    '【问题类型】账务问题',
    '【风险等级】low',
    '【Agent判断】用户明确提出账单问题，置信度 0.95',
    '【原始描述】我对接了阿里云OSS，最近发现账单里多了一笔不明扣费，请问这是存储还是请求费用？',
  ].join('\n')

  assert.equal(
    extractUserTicketContent(content),
    '我对接了阿里云OSS，最近发现账单里多了一笔不明扣费，请问这是存储还是请求费用？',
  )
})

test('普通文本原样作为用户提交内容', () => {
  assert.equal(extractUserTicketContent('  登录失败，请帮忙看看  '), '登录失败，请帮忙看看')
})

test('工单状态映射为用户可理解的进度', () => {
  assert.equal(getTicketProgress('classifying').label, '正在识别问题')
  assert.equal(getTicketProgress('waiting_user_input').label, '等待你补充信息')
  assert.equal(getTicketProgress('completed').percent, 100)
})
