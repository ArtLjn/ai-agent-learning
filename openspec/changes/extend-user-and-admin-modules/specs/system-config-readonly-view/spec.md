## ADDED Requirements

### Requirement: 系统配置只读接口

系统 SHALL 提供 `GET /api/admin/config` 接口返回当前系统配置摘要，6 类配置分块返回。

- 接口 MUST 通过 `require_admin` 鉴权
- 接口 MUST 为只读（无对应 PATCH/POST）
- 响应 MUST 包含 6 个配置块：`llm` / `vector_store` / `rag_service` / `database` / `prompt_versions` / `default_quota`

#### Scenario: 管理员查看配置

- **WHEN** 管理员调用 `GET /api/admin/config`
- **THEN** 系统 MUST 返回 200 + 6 类配置块

#### Scenario: 非 admin 访问

- **WHEN** 普通用户或未登录用户调用
- **THEN** 系统 MUST 返回 403

### Requirement: 配置脱敏——密钥字段省略

接口响应中**所有密钥类字段**（含 `KEY` / `PASSWORD` / `SECRET` / `TOKEN` 子串的字段名）MUST 直接省略不返回，**不返回 `"***"` 占位**。

#### Scenario: API_KEY 不返回

- **WHEN** 系统读取 LLM 配置含 `api_key="sk-xxx"`
- **THEN** 响应 `llm` 块 MUST 不含 `api_key` 字段
- **AND** 也不含 `"api_key": "***"`

#### Scenario: DB 密码不返回

- **WHEN** 数据库配置含 `password="xxx"`
- **THEN** 响应 `database` 块 MUST 不含 `password` 字段

#### Scenario: URL 完整显示

- **WHEN** LLM 配置含 `base_url="https://api.openai.com/v1"`
- **THEN** 响应 `llm.base_url` MUST 完整显示（不脱敏）

### Requirement: 6 类配置内容契约

响应各块字段 MUST 至少包含：

| 块 | 字段 |
| --- | --- |
| `llm` | `model` / `base_url` / `temperature` / `max_tokens`（不含 api_key） |
| `vector_store` | `qdrant_url` / `default_collection` / `distance_metric` |
| `rag_service` | `rag_service_url` / `default_retrieval_mode` / `default_top_k` |
| `database` | `host` / `port` / `database` / `connection_status`（不含 password） |
| `prompt_versions` | 5 Agent 当前激活版本号：`{ticket_intent: v?, classifier: v?, react_processor: v?, reviewer: v?, coordinator: v?}` |
| `default_quota` | `monthly_limit` / `weekly_limit` / `over_quota_action` |

#### Scenario: prompt_versions 联动 D-02

- **WHEN** D-02 中 classifier 的激活版本从 v2 切换到 v3
- **THEN** 后续调用 `GET /api/admin/config` 的 `prompt_versions.classifier` MUST 反映为 v3

#### Scenario: 未配置激活版本的 Agent

- **WHEN** 某 Agent 在 `prompt_versions` 表无激活行
- **THEN** `prompt_versions.{agent}` MUST 为 `null` 或 `"default"`，表示走源码常量

### Requirement: 前端 SystemConfig 页面只读卡片网格

前端 `web/src/pages/SystemConfig.tsx` SHALL 渲染 6 张只读卡片（对应 6 类配置）。

- 顶部红色提示条：「只读视图，配置修改请联系开发人员通过环境变量调整」
- 每张卡片标题对应配置类别，内容为字段键值对
- 不存在或被脱敏省略的字段不渲染该行
- 提供「刷新」按钮重新拉取配置

#### Scenario: 渲染配置卡片

- **WHEN** 管理员打开 `/config`
- **THEN** 页面 MUST 调用 `GET /api/admin/config`
- **AND** 渲染 6 张卡片
- **AND** 顶部显示红色只读提示条

#### Scenario: 字段缺失不渲染

- **WHEN** 响应中 `llm` 块无 `api_key` 字段（被省略）
- **THEN** LLM 卡片 MUST 不渲染 `api_key` 行
- **AND** 不显示空行或占位符

### Requirement: Settings 页面重定向

旧 `web/src/pages/Settings.tsx` MUST 重定向到 `/config`（SystemConfig.tsx）。

#### Scenario: 访问旧 Settings 路由

- **WHEN** 用户访问 `/settings`
- **THEN** 系统 MUST 重定向到 `/config`
- **AND** 不渲染旧 Settings 页面内容
