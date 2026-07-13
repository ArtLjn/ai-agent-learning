# 企业员工服务台知识库

本目录维护面向企业内部员工的服务台知识文档，用于 RAG 检索、AI 辅助建议和服务台人工审核。工单提交人默认为公司内部员工，服务台负责人工审核兜底，系统运维管理端负责查看 Trace、状态机、RAG、Prompt 和 Token。

## 模拟公司设定

本知识库模拟公司为“云舟科技有限公司”。云舟科技是一家约 1200 人规模的企业级 AI 软件公司，在上海、北京、深圳、成都设有办公室。公司统一使用钉钉作为员工门户，员工咨询机器人名为“小舟助手”，内部服务台名为“云舟服务台”。

为了便于演示，本目录中的平台、制度编号、审批流和负责人均为模拟数据，不对应任何真实公司制度。

| 模拟对象 | 名称 | 用途 |
| --- | --- | --- |
| 员工门户 | 云舟员工门户 | 制度、系统入口、通知公告 |
| 钉钉机器人 | 小舟助手 | 员工咨询、入口导航、工单创建 |
| HR 系统 | PeopleHub | 员工信息、考勤、假期、福利 |
| 财务系统 | FinFlow | 报销、发票、付款、预算 |
| 采购平台 | BuyDesk | 办公用品、软件、SaaS、固定资产采购 |
| 资产系统 | AssetOne | 电脑、显示器、手机、工牌和固定资产 |
| SSO 门户 | CloudID | 统一登录、MFA、应用入口 |
| IT 服务台 | 云舟服务台 | 人工审核、转派、处理员工工单 |

## 覆盖范围

- 新员工入职：账号、钉钉、邮箱、办公设备、常用系统入口、默认权限。
- 规章制度：考勤、请假、加班、餐补、福利、薪酬社保查询。
- 行政办公：会议室、工位、门禁、打印、办公用品、快递收发。
- 采购与报销：办公用品、软件服务、采购平台、审批流、发票、费用报销。
- 钉钉机器人：制度查询、待办提醒、审批入口、报障入口、使用边界。
- IT 支持：SSO、VPN、网络、邮箱日历、电脑外设、软件安装、GPT 权限。
- 安全合规：数据外发、权限最小化、设备丢失、离职交接和账号回收。

## 文档清单

| 文件 | 主题 | 推荐分类 |
| --- | --- | --- |
| `company-service-handbook.md` | 云舟科技员工服务总览 | inquiry |
| `company-platform-map.md` | 内部平台地图与入口 | inquiry |
| `employee-onboarding-guide.md` | 新员工入职与首日办公 | inquiry |
| `employee-rules-handbook.md` | 公司制度查询与公告口径 | inquiry |
| `employee-attendance-leave-overtime.md` | 考勤、请假、加班规则 | inquiry |
| `employee-meal-subsidy-and-benefits.md` | 加班餐补、福利、补贴查询 | billing |
| `employee-procurement-platform.md` | 办公用品和软件采购平台 | inquiry |
| `employee-expense-reimbursement.md` | 报销、发票、付款进度 | billing |
| `employee-dingtalk-bot-guide.md` | 钉钉员工咨询机器人 | inquiry |
| `employee-account-sso-permission.md` | 账号、SSO、权限申请 | technical |
| `employee-email-calendar.md` | 邮箱、日历、会议邀请 | technical |
| `employee-vpn-network.md` | VPN、办公网和远程办公 | technical |
| `employee-device-printer-meetingroom.md` | 电脑、打印机、会议室设备 | technical |
| `employee-software-and-gpt-access.md` | 软件安装、许可证、GPT 权限 | inquiry |
| `employee-security-compliance.md` | 数据安全、DLP、设备丢失 | complaint |
| `employee-offboarding-transfer.md` | 离职交接和权限回收 | technical |
| `service-desk-priority-and-escalation.md` | 服务台优先级和升级规则 | technical |
| `knowledge-maintenance-guide.md` | 知识库维护和 RAG 缺口处理 | inquiry |

## 入库建议

通过知识库管理页批量上传 Markdown，或直接调用 RAG 服务 `/ingest` 接口。文件名会作为分类来源之一，建议保持稳定，避免评测种子和历史引用失效。

```bash
curl -X POST https://rag.example.com/ingest \
  -H "X-API-Key: <RAG_SERVICE_API_KEY>" \
  -F collection=ticket_knowledge \
  -F "file=@data/knowledge_base/employee-dingtalk-bot-guide.md" \
  -F source=employee-dingtalk-bot-guide.md \
  -F category=inquiry
```

## 评测建议

入库后使用 `data/evaluation/golden/retrieval_itsm_seed.jsonl` 做第一轮召回检查。评测问题应覆盖口语化问法，例如“加班有没有餐补”“办公用品在哪个平台买”“钉钉机器人能不能帮我查制度”“新人第一天没有邮箱怎么办”。
