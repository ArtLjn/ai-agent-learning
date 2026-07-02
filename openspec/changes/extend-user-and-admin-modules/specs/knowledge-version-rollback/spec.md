## ADDED Requirements

### Requirement: 知识库版本回滚透传接口

系统 SHALL 提供 `POST /api/admin/knowledge/{doc_id}/rollback?version=X` 接口，将回滚请求透传到 rag-service 的 `POST /documents/{doc_id}/rollback?version=X`。

- 接口 MUST 通过 `require_admin` 鉴权
- 主系统 MUST NOT 在本地维护文档版本表（版本管理由 rag-service 承担）
- 请求路径参数 `doc_id` 与 query 参数 `version` 1:1 透传

#### Scenario: 回滚成功

- **WHEN** 管理员调用 `POST /api/admin/knowledge/DOC-001/rollback?version=3`
- **THEN** 系统 MUST 转发到 rag-service `POST /documents/DOC-001/rollback?version=3`
- **AND** rag-service 返回 200 时主系统返回 200 + 透传响应体

#### Scenario: 文档不存在

- **WHEN** 管理员调用回滚接口但 doc_id 在 rag-service 不存在
- **THEN** rag-service 返回 404
- **AND** 主系统 MUST 透传 404 给客户端

#### Scenario: 版本号不存在

- **WHEN** 管理员指定的 version 在 rag-service 不存在
- **THEN** rag-service 返回 404
- **AND** 主系统透传 404

### Requirement: rag-service 不可用降级

当 rag-service 调用失败（超时 / 连接错误 / 5xx）时，主系统 MUST 返回结构化 502 错误，不抛未捕获异常。

- 默认超时 30s（回滚是重操作，比 RAG 调试器的 10s 更长）
- 响应 `{error: "rag_service_unavailable", detail: "..."}`，HTTP 502

#### Scenario: rag-service 宕机

- **WHEN** rag-service 不可达
- **THEN** 主系统 MUST 返回 502 + `rag_service_unavailable`
- **AND** 不影响主系统其他功能

#### Scenario: rag-service 超时

- **WHEN** rag-service 30s 内未响应
- **THEN** 主系统 MUST 返回 504 + `rag_service_timeout`

### Requirement: 回滚操作审计落盘

回滚操作 MUST 通过 A-07 审计中间件自动写入 `audit_logs`，action 为 `knowledge_rollback`，detail 含 `{doc_id, version}`。

#### Scenario: 回滚落审计

- **WHEN** 管理员成功调用回滚接口
- **THEN** `audit_logs` MUST 新增一行
- **AND** `action="knowledge_rollback"`
- **AND** `target_type="knowledge_doc"`、`target_id=doc_id`
- **AND** `detail={doc_id: "DOC-001", version: 3}`

### Requirement: 前端版本历史 Sheet

前端 `web/src/pages/Knowledge.tsx`（管理员视图）文档列表每行 MUST 提供「版本历史」按钮，点击弹出 Sheet 展示该文档的版本列表 + 回滚入口。

- 版本列表数据来源：调用主系统 `GET /api/admin/knowledge/{doc_id}/versions`（透传 rag-service）
- 每个版本项展示：version / created_at / uploaded_by / 文件大小 / 操作「回滚到此版本」
- 回滚操作 MUST 二次确认（影响生产知识库）

#### Scenario: 查看版本历史

- **WHEN** 管理员点击 DOC-001 的「版本历史」
- **THEN** Sheet MUST 调用版本列表接口
- **AND** 按版本号倒序展示

#### Scenario: 触发回滚

- **WHEN** 管理员在 Sheet 中点击某版本的「回滚到此版本」并在二次确认中点击「确认」
- **THEN** 前端 MUST 调用 `POST /api/admin/knowledge/{doc_id}/rollback?version=X`
- **AND** 成功后显示「回滚成功」提示
- **AND** 关闭 Sheet，刷新文档列表

#### Scenario: 取消回滚

- **WHEN** 管理员在二次确认中点击「取消」
- **THEN** 系统 MUST 不发起请求
- **AND** Sheet 保持打开

### Requirement: 回滚后文档列表刷新

回滚成功后，前端 MUST 刷新文档列表以反映 Qdrant 中向量数据的变化。

#### Scenario: 列表自动刷新

- **WHEN** 回滚成功
- **THEN** 前端 MUST 重新调用 `GET /api/admin/knowledge` 刷新文档列表
- **AND** 显示最新状态（如版本号、chunk 数量）
