import assert from 'node:assert/strict'
import test from 'node:test'

import { canUseTicketReplyComposer } from '../src/lib/ticketDetailPermissions.ts'

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
