"""初始化企业员工服务台知识库。

数据来源：
1. 内置 KNOWLEDGE_DOCUMENTS（默认 8 篇）
2. data/knowledge_base/*.md 文件（自动加载，可在文件中持续维护）

用法：
    cd ai-agent-learning
    python scripts/seed_knowledge.py                  # 上传 内置 + markdown
    python scripts/seed_knowledge.py --markdown-only  # 仅 markdown
    python scripts/seed_knowledge.py --inline-only    # 仅内置

前提：Qdrant 和 Embedding 服务已启动；config.yaml 已配置好凭据。
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.multi_agent_system.config import Settings  # noqa: E402
from src.multi_agent_system.tools.knowledge_search import KnowledgeSearchTool  # noqa: E402

KB_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge_base"


KNOWLEDGE_DOCUMENTS: list[dict] = [
    {
        "id": "employee-inline-001",
        "title": "新员工首日办公检查清单",
        "category": "inquiry",
        "source": "员工服务台内置种子",
        "content": (
            "云舟科技新员工首日应确认员工号、钉钉组织关系、邮箱、CloudID、YunVPN、工牌门禁、"
            "办公设备和常用系统入口。若任一项缺失，请提供员工号、部门、入职日期、"
            "办公地点和直属主管。服务台先核查 HR 主数据和默认权限模板，再转 IT、"
            "行政或 HR 处理。"
        ),
    },
    {
        "id": "employee-inline-002",
        "title": "公司制度查询入口",
        "category": "inquiry",
        "source": "员工服务台内置种子",
        "content": (
            "员工查询考勤、请假、加班、餐补、报销、采购、福利和信息安全制度时，"
            "优先进入钉钉工作台的“小舟助手”或云舟员工门户，也可在 CloudID 门户搜索制度名称。"
            "小舟助手只提供摘要和入口，最终口径以最新有效制度和归口部门答复为准。"
        ),
    },
    {
        "id": "employee-inline-003",
        "title": "考勤加班与餐补处理口径",
        "category": "billing",
        "source": "员工服务台内置种子",
        "content": (
            "加班餐补是否生成，需要结合现行制度、办公地点、加班审批和考勤记录判断。"
            "员工应提供员工号、加班日期、审批单号、PeopleHub 考勤记录和补贴类型。云舟服务台可核查"
            "同步状态，制度解释转 HR 或行政，金额和付款问题转财务或 FinFlow 支持。"
        ),
    },
    {
        "id": "employee-inline-004",
        "title": "采购平台与办公用品申请",
        "category": "inquiry",
        "source": "员工服务台内置种子",
        "content": (
            "办公用品、软件许可证、SaaS 服务和固定资产采购应通过钉钉工作台或 BuyDesk"
            "提交，不建议员工私下向供应商下单。申请时需填写品类、数量、用途、成本中心、"
            "预算归属和期望到货时间。涉及软件、数据或外部服务时需 IT、安全或法务审核。"
        ),
    },
    {
        "id": "employee-inline-005",
        "title": "费用报销与发票问题",
        "category": "billing",
        "source": "员工服务台内置种子",
        "content": (
            "报销问题需提供报销单号、费用类型、金额、发票状态、审批节点和退回原因。"
            "发票抬头、税号、金额、日期和附件必须符合云舟科技财务制度。入口和材料问题可由"
            "服务台指导，付款异常、发票风险和财务规则解释应转财务人工确认。"
        ),
    },
    {
        "id": "employee-inline-006",
        "title": "钉钉员工咨询机器人能力边界",
        "category": "inquiry",
        "source": "员工服务台内置种子",
        "content": (
            "钉钉员工咨询机器人“小舟助手”可查询制度、定位系统入口、说明审批材料、创建工单和提醒补充信息。"
            "机器人不能直接修改考勤、工资、报销、权限、合同或人事记录。涉及薪酬、绩效、"
            "劳动关系、个人隐私、安全事件和敏感权限时必须转人工审核。"
        ),
    },
    {
        "id": "employee-inline-007",
        "title": "账号 SSO 与权限申请",
        "category": "technical",
        "source": "员工服务台内置种子",
        "content": (
            "员工无法登录 CloudID、邮箱、钉钉或业务系统时，应提供员工号、系统名称、报错截图和"
            "发生时间。权限新增或变更必须通过审批流提交，服务台不得绕过审批直接授权。"
            "生产、财务、人事和客户数据权限需系统负责人和数据权限负责人共同确认。"
        ),
    },
    {
        "id": "employee-inline-008",
        "title": "数据安全与设备丢失升级",
        "category": "complaint",
        "source": "员工服务台内置种子",
        "content": (
            "员工不要在工单、钉钉机器人或外部 AI 工具中粘贴密码、验证码、API Key、客户隐私、"
            "合同全文或源代码。设备丢失、DLP 拦截、敏感数据误发和离职权限未回收应立即升级"
            "云舟安全团队，优先冻结 CloudID、远程擦除、回收会话并记录影响范围。"
        ),
    },
]


def load_markdown_documents(directory: Path = KB_DIR) -> list[dict]:
    """从 data/knowledge_base/ 加载所有 markdown 文件作为知识库文档。

    每个文件生成一份文档，字段映射：
      - id:       "md-" + 文件名 md5 前 8 位（保证内容不变时 id 稳定）
      - title:    markdown 第一个一级标题（# xxx），找不到时用文件名 stem
      - category: 文件名 stem（如 employee-dingtalk-bot-guide）
      - source:   相对路径（如 data/knowledge_base/employee-dingtalk-bot-guide.md）
      - content:  文件全文
    """
    if not directory.exists():
        print(f"[warn] markdown 目录不存在: {directory}")
        return []

    docs: list[dict] = []
    for md_file in sorted(directory.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else md_file.stem
        doc_id = "md-" + hashlib.md5(md_file.stem.encode("utf-8")).hexdigest()[:8]
        docs.append(
            {
                "id": doc_id,
                "title": title,
                "category": md_file.stem,
                "source": f"data/knowledge_base/{md_file.name}",
                "content": text,
            }
        )
    return docs


def main() -> None:
    """执行知识库初始化。"""
    parser = argparse.ArgumentParser(description="初始化 Qdrant 知识库")
    parser.add_argument(
        "--markdown-only",
        action="store_true",
        help="只上传 data/knowledge_base/ 下的 markdown 文件",
    )
    parser.add_argument(
        "--inline-only",
        action="store_true",
        help="只上传脚本内置的 KNOWLEDGE_DOCUMENTS",
    )
    args = parser.parse_args()

    settings = Settings()
    tool = KnowledgeSearchTool.create_from_settings()

    documents: list[dict] = []
    if not args.markdown_only:
        documents.extend(KNOWLEDGE_DOCUMENTS)
    markdown_docs: list[dict] = []
    if not args.inline_only:
        markdown_docs = load_markdown_documents()
        documents.extend(markdown_docs)

    print(f"连接 Qdrant: {settings.qdrant_url}")
    print(f"Embedding 模型: {settings.embedding_model}")
    print(f"内置文档: {len(KNOWLEDGE_DOCUMENTS)} 篇")
    print(f"Markdown 文档: {len(markdown_docs)} 篇 (来源: {KB_DIR})")
    print(f"待导入总数: {len(documents)} 篇\n")

    tool.ensure_collection()
    total_chunks = tool.add_documents(documents)

    print(f"\n导入完成！共 {len(documents)} 篇文档，{total_chunks} 个向量块")

    by_category: dict[str, int] = {}
    for document in documents:
        category = document.get("category", "未分类")
        by_category[category] = by_category.get(category, 0) + 1
    print("分类覆盖:")
    for category, count in sorted(by_category.items()):
        print(f"  {category}: {count} 篇")


if __name__ == "__main__":
    main()
