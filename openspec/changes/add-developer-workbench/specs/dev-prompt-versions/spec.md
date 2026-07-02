## ADDED Requirements

### Requirement: Prompt 模板版本持久化

系统 SHALL 通过 `prompt_versions` 表持久化 5 个 Agent（ticket_intent / classifier / react_processor / reviewer / coordinator）的 Prompt 模板版本。

表结构 MUST 满足：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `id` | BIGINT | PK, AUTO_INCREMENT |
| `agent_name` | VARCHAR(64) | NOT NULL，5 个枚举值之一 |
| `version` | INT | NOT NULL，同 agent_name 内自增 |
| `template` | TEXT | NOT NULL |
| `is_active` | BOOLEAN | NOT NULL DEFAULT FALSE |
| `note` | VARCHAR(500) | NULL |
| `created_by` | VARCHAR(64) | NULL |
| `created_at` | DATETIME | NOT NULL DEFAULT CURRENT_TIMESTAMP |

系统 MUST 在 `(agent_name, version)` 上建立 UNIQUE 约束，在 `(agent_name, is_active)` 上建立索引。

#### Scenario: 新建版本

- **WHEN** 管理员调用 `POST /api/admin/prompts/classifier/versions`，请求体含 `template` 与 `note`
- **THEN** 系统 MUST 插入新行，`version` 为该 agent_name 下现有最大 version + 1
- **AND** `is_active` 默认为 FALSE

#### Scenario: 同 agent_name 内 version 唯一

- **WHEN** 系统并发插入两条 classifier v3
- **THEN** 第二条 MUST 因 UNIQUE 约束失败，返回 409

### Requirement: 同 agent_name 仅一条激活版本

系统 SHALL 保证任意时刻同一 `agent_name` 下至多一条 `is_active=true` 的版本。

激活操作 MUST 在单个数据库事务中完成：

1. `UPDATE prompt_versions SET is_active=false WHERE agent_name=?`
2. `UPDATE prompt_versions SET is_active=true WHERE agent_name=? AND version=?`

#### Scenario: 激活新版本时旧版本自动失活

- **WHEN** classifier 当前激活 v2，管理员激活 v3
- **THEN** 事务完成后 v2 `is_active=false`、v3 `is_active=true`
- **AND** 同 agent_name 下不存在第二条 `is_active=true`

#### Scenario: 激活中断回滚

- **WHEN** 激活事务第二步失败
- **THEN** 整个事务 MUST 回滚
- **AND** 激活状态保持激活前的版本不变

### Requirement: Agent 启动时从 DB 加载激活版本

5 个 Agent 在实例化时 SHALL 通过 `PromptManager.get_active(agent_name)` 查询 DB 中该 agent_name 的激活版本模板。

- 找到激活版本时 MUST 用 DB 模板覆盖源码默认 prompt
- 找不到激活版本（表为空或无激活行）时 MUST 降级到源码常量，不抛异常
- 加载结果 MUST 缓存到 Agent 实例属性，本次进程生命周期内不再查 DB

#### Scenario: DB 有激活版本

- **WHEN** ClassifierAgent 实例化且 DB 中 classifier v3 为激活
- **THEN** Agent 实例的 prompt 属性 MUST 为 v3 的 template

#### Scenario: DB 为空降级

- **WHEN** ClassifierAgent 实例化且 `prompt_versions` 表为空（首次部署）
- **THEN** Agent MUST 降级使用源码常量 prompt
- **AND** 不抛异常，日志记录 warning「prompt 未在 DB 配置，使用源码默认」

### Requirement: 激活切换提示需重启

`POST /api/admin/prompts/{agent_name}/versions/{version}/activate` 接口的响应 MUST 包含 `requires_restart: true` 字段，告知用户激活切换需要重启 workflow 进程才生效。

#### Scenario: 激活响应

- **WHEN** 管理员激活新版本
- **THEN** 响应 MUST 返回 `{activated_version, requires_restart: true, message: "激活成功，需重启 workflow 进程生效"}`

### Requirement: 回滚通过复制新行实现

系统 SHALL 不直接修改历史版本的 `is_active`，回滚到 vX 时复制 vX 的 template 为新行（version 自增），`note` 标注 `rollback to vX`，再激活新行。

#### Scenario: 回滚到历史版本

- **WHEN** 管理员请求"回滚到 classifier v1"，当前激活 v3
- **THEN** 系统 MUST 复制 v1 的 template 创建新行 v4，`note` 为 `rollback to v1`
- **AND** 激活 v4（v3 自动失活）
- **AND** v1 原行保持不变（`is_active=false`，版本历史完整）

#### Scenario: 版本号跳号

- **WHEN** 经过多次回滚，version 序列可能为 v1, v2, v3, v4(=v1), v5(=v2)
- **THEN** 系统 MUST 接受非连续版本号
- **AND** 通过 `note` 字段追溯回滚来源

### Requirement: 前端版本对比 diff 视图

前端 `web/src/pages/dev/PromptVersions.tsx` SHALL 提供左侧版本列表 + 右侧 diff 视图的布局。

- 左侧列表按 `created_at` 倒序，激活版本带 ★ 标记
- 右侧 diff 视图使用 `react-diff-viewer-continued`，支持 split / unified 切换
- 顶部 Agent 选择器（5 个 Agent 下拉）
- 操作区含「新建版本」「激活」「回滚」三个按钮，写操作需二次确认

#### Scenario: 对比两个版本

- **WHEN** 开发人员在左侧列表选中 v3 与 v2
- **THEN** 右侧 diff 视图 MUST 显示两版本的 template 差异
- **AND** 支持在 split / unified 视图间切换

#### Scenario: 新建版本

- **WHEN** 开发人员点击「新建版本」并粘贴 template
- **THEN** 系统 MUST 调用 POST 接口创建新版本
- **AND** 新版本 `is_active=false`，左侧列表立即更新
