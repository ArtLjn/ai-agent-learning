## Why

v2.2 定稿已将工单提交人明确为企业内部员工，但现有实现和旧提案仍偏向“用户模块”技术清单，不能充分体现员工从提报、补充、跟踪到验收的业务链条。需要将员工服务端作为独立实施包，补齐内部服务请求门户的显性业务体量。

## What Changes

- 新增员工服务端业务闭环：员工档案、工单提报、进度协同、验收反馈。
- 将提单入口从单一文本提交升级为服务类型选择、问题描述、关键信息填写和材料提示。
- 强化工单详情页的员工视角：只展示员工可理解内容，隐藏内部检索相似度、知识库命中、Agent 置信度等调试信息。
- 支持 `waiting_user_input` 状态下员工补充材料，并在完成后恢复智能处理流程。
- 支持完成工单的结果验收、不满意申诉和历史工单查询。

## Capabilities

### New Capabilities

- `employee-service-portal`: 企业内部员工提交、跟踪、补充和验收服务台工单的门户能力。

### Modified Capabilities

无。

## Impact

- 前端：`web/src/pages/Tickets.tsx`、`TicketDetail.tsx`、`Profile.tsx`、提单组件、状态进度组件。
- 后端：`/api/tickets`、`/api/tickets/{ticket_id}`、`/api/tickets/{ticket_id}/messages`、`/api/tickets/{ticket_id}/feedback`、用户资料接口。
- 数据：用户档案字段、工单 metadata、补充消息、反馈记录。
- 测试：员工提单、用户隔离、补充恢复、结果验收、申诉升级和用户视角内容清洗。
