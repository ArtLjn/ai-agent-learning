import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildKnowledgeSearchParams,
  filterKnowledgeDocuments,
} from '../src/lib/knowledgeReference.ts'

const docs = [
  {
    id: 'doc-1',
    title: '加班餐补与福利查询',
    category: 'employee-meal-subsidy-and-benefits',
    content: '加班餐补需要结合当前制度、办公地点、加班审批和考勤记录判断。',
    preview: '加班餐补需要结合当前制度、办公地点、加班审批和考勤记录判断。',
    chunk_count: 1,
    chunks: [],
  },
  {
    id: 'doc-2',
    title: '账号 SSO 与权限申请',
    category: 'technical',
    content: '核查员工号、账号状态、SSO 同步、MFA 和业务系统用户组。',
    preview: '核查员工号、账号状态、SSO 同步、MFA 和业务系统用户组。',
    chunk_count: 1,
    chunks: [],
  },
]

test('知识库参考跳转不使用不可靠分类硬过滤', () => {
  const reference = [
    '检索到以下知识片段：',
    '1. 标题: 加班餐补与福利查询；分类: employee-meal-subsidy-and-benefits；相似度: 0.75',
    '内容: > 加班餐补需要结合当前制度、办公地点、加班审批和考勤记录判断',
  ].join('\n')

  const params = buildKnowledgeSearchParams(reference)

  assert.equal(params.get('q'), '加班餐补与福利查询')
  assert.equal(params.has('category'), false)
})

test('知识库搜索能用标题命中分类别名不同的文档', () => {
  const matched = filterKnowledgeDocuments(docs, {
    query: '加班餐补与福利查询',
    category: 'meal-subsidy-guide',
  })

  assert.deepEqual(matched.map((doc) => doc.id), ['doc-1'])
})
