import assert from 'node:assert/strict'
import test from 'node:test'

import {
  extractUserTicketContent,
  getKeyMaterialPrompt,
  getTicketProgress,
} from '../src/lib/ticketPresentation.ts'

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

test('员工进度文案不暴露内部智能处理术语', () => {
  const internalTerms = ['Agent', 'RAG', 'Prompt', 'Trace', 'Token']
  const statuses = [
    'received',
    'classifying',
    'processing',
    'reviewing',
    'pending_human_review',
    'waiting_user_input',
    'completed',
    'failed',
  ] as const

  for (const status of statuses) {
    const progress = getTicketProgress(status)
    for (const term of internalTerms) {
      assert.equal(progress.label.includes(term), false)
      assert.equal(progress.detail.includes(term), false)
    }
  }
})

test('服务类型返回员工可理解的关键材料提示', () => {
  assert.match(getKeyMaterialPrompt('account_access').helperText, /账号|系统/)
  assert.match(getKeyMaterialPrompt('office_device').helperText, /设备|故障/)
  assert.match(getKeyMaterialPrompt('unknown').helperText, /可选/)
})
