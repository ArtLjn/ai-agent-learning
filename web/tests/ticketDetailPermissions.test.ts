import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canCreateEmployeeTicket,
  canSubmitReviewDecision,
  canSubmitTicketFeedback,
  canUseTicketReplyComposer,
  canViewExecutionTrace,
} from '../src/lib/ticketDetailPermissions.ts'

test('用户在待补充状态可提交补充信息', () => {
  assert.equal(canUseTicketReplyComposer('user', true), true)
  assert.equal(canUseTicketReplyComposer('user', false), false)
})

test('管理员和开发者只能查看补充记录，不能提交补充', () => {
  assert.equal(canUseTicketReplyComposer('admin', true), false)
  assert.equal(canUseTicketReplyComposer('developer', true), false)
  assert.equal(canUseTicketReplyComposer('admin', false), false)
  assert.equal(canUseTicketReplyComposer('developer', false), false)
})

test('未知角色不显示回复入口', () => {
  assert.equal(canUseTicketReplyComposer(null, true), false)
  assert.equal(canUseTicketReplyComposer(undefined, true), false)
})

test('员工写动作只开放员工服务请求和结果反馈', () => {
  assert.equal(canCreateEmployeeTicket('user'), true)
  assert.equal(canSubmitTicketFeedback('user'), true)
  assert.equal(canCreateEmployeeTicket('admin'), false)
  assert.equal(canSubmitTicketFeedback('developer'), false)
})

test('服务台人员只拼装人工审核决策写组件', () => {
  assert.equal(canSubmitReviewDecision('admin'), true)
  assert.equal(canSubmitReviewDecision('user'), false)
  assert.equal(canSubmitReviewDecision('developer'), false)
})

test('Trace 链路只开放给系统运维人员', () => {
  assert.equal(canViewExecutionTrace('developer'), true)
  assert.equal(canViewExecutionTrace('admin'), false)
  assert.equal(canViewExecutionTrace('user'), false)
})
