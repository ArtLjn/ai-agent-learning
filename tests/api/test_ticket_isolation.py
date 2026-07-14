"""用户隔离测试：user 角色只能看自己的工单；admin 可看服务台工单，developer 不进业务工单。

覆盖：
- POST /tickets：session user_id 自动注入（防伪造 body.user_id）
- GET /tickets：user 角色按 session.user_id 过滤；admin 看全部；developer 403
- GET /tickets/{id}：user 看他人 403；admin 放行；developer 403
- GET /tickets/{id}/messages：user 看他人 403
- POST /tickets/{id}/messages：user 给他人补充 403
- POST /tickets/{id}/feedback：user 给他人反馈 403
- 演示模式（auth_enabled=false）：全放行
"""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.routes import router as biz_router
from src.multi_agent_system.core.database import DatabaseManager
from tests.conftest import TEST_DATABASE_URL

_SESSION_SECRET = "test-ticket-isolation-secret-32-chars-or-more"


def _build_app() -> FastAPI:
    """构造测试 app，db_manager 在 lifespan 内创建（绑定 TestClient 的 loop）。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        app.state.db_manager = db_manager
        # db_tool 桥接到真 db_manager，让 list_tickets / save_ticket 走真 DB
        db_tool = MagicMock()
        db_tool.save_ticket = AsyncMock(side_effect=db_manager.save_ticket)
        db_tool.get_ticket = AsyncMock(side_effect=db_manager.get_ticket)
        db_tool.list_tickets = AsyncMock(side_effect=db_manager.list_tickets)
        app.state.db_tool = db_tool
        app.state.coordinator = None
        app.state.analytics_tool = MagicMock()
        app.state.knowledge_tool = None
        app.state.memory_manager = None
        app.state.tool_registry = None
        app.state.workflow = MagicMock()
        app.state.trace_manager = None
        app.state.ticket_intent_agent = None  # 走 fallback 路径，不调 LLM
        yield
        await db_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=_SESSION_SECRET)
    app.include_router(auth_router, prefix="/api")
    app.include_router(biz_router, prefix="/api")
    return app


@pytest.fixture
def app():
    return _build_app()


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        app.state._portal = c.portal
        yield c


# ============================================================
# 工具函数
# ============================================================


def _register(client: TestClient, username: str) -> dict[str, Any]:
    """注册用户并自动登录（session 写入）。"""
    resp = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "secret123",
            "nickname": username.title(),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]


def _login(client: TestClient, username: str) -> None:
    """重新登录指定用户。"""
    client.post("/api/auth/logout")
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": "secret123"},
    )
    assert resp.status_code == 200, f"登录失败: {resp.text}"


def _promote(client: TestClient, app: FastAPI, user_id: str, role: str) -> None:
    """DB 层面改 role，然后重新登录让 session 拿到新 role。"""
    updated = client.portal.call(app.state.db_manager.update_user_role, user_id, role)
    assert updated is not None and updated["role"] == role
    _login(client, updated["username"])


def _seed_ticket(
    app: FastAPI,
    ticket_id: str,
    user_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    """直接通过 db_manager 植入一条工单。"""
    ticket = {
        "ticket_id": ticket_id,
        "content": f"工单 {ticket_id} 内容",
        "user_id": user_id,
        "category": "inquiry",
        "priority": "P2",
        "status": "completed",
        "review_score": None,
        "retry_count": 0,
        "references": [],
        "created_at": "2026-07-05T10:00:00",
    }
    ticket.update(overrides)
    client_portal = app.state._portal
    client_portal.call(app.state.db_manager.save_ticket, ticket)
    return ticket


# ============================================================
# 测试用例
# ============================================================


class TestCreateTicketUserIdInjection:
    """POST /tickets 的 user_id 自动注入。"""

    def test_create_injects_session_user_id(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """登录用户创建工单，body 里的 user_id（伪造）被忽略，写入的是 session user_id。"""
        user = _register(client, "alice")
        # 前端传伪造 user_id="FAKE"
        resp = client.post(
            "/api/tickets",
            json={"content": "测试工单内容长度至少八个字符", "user_id": "FAKE"},
        )
        assert resp.status_code == 200, resp.text
        ticket_id = resp.json()["ticket_id"]

        # DB 里存的应该是 session user_id，不是 "FAKE"
        ticket = client.portal.call(app.state.db_manager.get_ticket, ticket_id)
        assert ticket["user_id"] == user["user_id"]
        assert ticket["user_id"] != "FAKE"

    def test_create_structured_employee_ticket_persists_service_metadata(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """员工提单包含服务类型和关键材料元数据，并由后端保存。"""
        user = _register(client, "alice")

        resp = client.post(
            "/api/tickets",
            json={
                "service_type": "account_access",
                "content": "我无法登录企业邮箱，请协助恢复账号访问。",
                "key_materials": {
                    "system": "企业邮箱",
                    "account": "alice@example.com",
                },
            },
        )

        assert resp.status_code == 200, resp.text
        ticket = client.portal.call(
            app.state.db_manager.get_ticket, resp.json()["ticket_id"]
        )
        assert ticket["user_id"] == user["user_id"]
        assert ticket["service_type"] == "account_access"
        assert ticket["key_materials"] == {
            "system": "企业邮箱",
            "account": "alice@example.com",
        }

    def test_create_rejects_blank_problem_description(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """空问题描述直接拒绝，且不创建工单。"""
        _register(client, "alice")

        resp = client.post(
            "/api/tickets",
            json={"service_type": "account_access", "content": "   "},
        )

        assert resp.status_code == 422
        tickets = client.portal.call(app.state.db_manager.list_tickets)
        assert tickets == []


class TestListTicketsIsolation:
    """GET /tickets 列表接口的 user 隔离。"""

    def test_user_sees_only_own_tickets(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """user 角色：列表只返回自己的工单。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        # 当前 session 是 bob（注册 bob 后自动登录）
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-alice-1", alice["user_id"])
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        items = resp.json()
        ticket_ids = [t["ticket_id"] for t in items]
        assert "TK-alice-1" in ticket_ids
        assert "TK-bob-1" not in ticket_ids

    def test_admin_sees_all_tickets(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """admin 角色：列表返回所有用户的工单。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-alice-1", alice["user_id"])
        _seed_ticket(app, "TK-bob-1", bob["user_id"])

        # 升 alice 为 admin 并重新登录
        _promote(client, app, alice["user_id"], "admin")

        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        items = resp.json()
        ticket_ids = [t["ticket_id"] for t in items]
        assert "TK-alice-1" in ticket_ids
        assert "TK-bob-1" in ticket_ids

    def test_developer_cannot_list_business_tickets(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """developer 角色属于系统运维端，不开放业务工单列表入口。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-alice-1", alice["user_id"])
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "developer")

        resp = client.get("/api/tickets")
        assert resp.status_code == 403


class TestTicketDetailIsolation:
    """GET /tickets/{id} 详情接口的 user 隔离。"""

    def test_user_views_own_ticket_200(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _seed_ticket(app, "TK-alice-1", alice["user_id"])

        resp = client.get("/api/tickets/TK-alice-1")
        assert resp.status_code == 200

    def test_user_views_other_user_ticket_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.get("/api/tickets/TK-bob-1")
        assert resp.status_code == 403

    def test_admin_views_other_user_ticket_200(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "admin")

        resp = client.get("/api/tickets/TK-bob-1")
        assert resp.status_code == 200

    def test_developer_views_other_user_ticket_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "developer")

        resp = client.get("/api/tickets/TK-bob-1")
        assert resp.status_code == 403

    def test_user_detail_sanitizes_internal_processing_result(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """user 视角不返回知识库命中、相似度等内部检索文本。"""
        alice = _register(client, "alice")
        raw_result = (
            "您好，知识库命中了相关资料，但还没有覆盖到完全精确的业务细则。"
            "可先参考以下处理建议：\n\n"
            "知识库参考：检索到以下知识片段：1. 标题: 未命名文档；分类: 未分类；"
            "相似度: 0.40 内容: 浏览器提示证书过期。证书未续期。中间证书缺失。"
            "定时任务未执行。查看 acme.sh 日志。\n\n"
            "建议先核对：\n"
            "1. 确认证书有效期。\n"
            "2. 检查定时任务是否执行。\n\n"
            "需要人工确认：具体后台入口。"
        )
        _seed_ticket(
            app,
            "TK-alice-internal-result",
            alice["user_id"],
            content=(
                "【问题标题】SSL证书过期咨询\n"
                "【问题类型】技术问题\n"
                "【Agent判断】用户询问证书续签，置信度 0.95\n"
                "【原始描述】你好，我想咨询一下SSL证书过期的处理流程。"
            ),
            processing_result=raw_result,
        )

        resp = client.get("/api/tickets/TK-alice-internal-result")
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == "你好，我想咨询一下SSL证书过期的处理流程。"
        assert "问题类型" not in body["content"]
        assert "置信度" not in body["content"]
        result = body["processing_result"]
        assert "证书过期" in result
        assert "acme.sh" in result
        assert "知识库命中" not in result
        assert "知识库参考" not in result
        assert "检索到以下知识片段" not in result
        assert "相似度" not in result
        assert "分类:" not in result
        assert "需要人工确认" not in result
        assert body["review_score"] is None
        assert body["references"] == []

    def test_user_detail_answers_company_name_without_internal_context(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """员工问公司名称时，只返回可读答案，不暴露内部检索和旧模板。"""
        alice = _register(client, "alice")
        raw_result = (
            "您好，已根据现有资料整理出一组可先核对的方向。目前还缺少可直接确认最终答案的具体业务细则，"
            "可以先按以下方向核对：\n\n"
            "可参考的资料要点：| P3 | 单个员工常规问题 | 4 工作小时响应 | "
            "工单提交人默认为公司内部员工,服务台负责人工审核兜底,系统运维管理端负责查看 Trace、状态机、RAG、"
            "Prompt 和 Token。 本知识库模拟公司为“云舟科技有限公司”。 |\n\n"
            "1. 确认产品或平台、应用类型、账号权限与本次咨询对象是否一致。\n"
            "2. 对接或配置类问题，优先核对 Key/Secret、应用标识、白名单、服务开通状态和接口返回码。\n"
            "需要人工确认：具体业务细则。"
        )
        _seed_ticket(
            app,
            "TK-alice-company-name",
            alice["user_id"],
            content=(
                "【问题标题】公司名称咨询\n"
                "【问题类型】咨询问询\n"
                "【Agent判断】用户询问公司名称，置信度 0.95\n"
                "【原始描述】你好公司叫啥呀"
            ),
            processing_result=raw_result,
        )

        resp = client.get("/api/tickets/TK-alice-company-name")
        assert resp.status_code == 200
        result = resp.json()["processing_result"]
        assert result == "您好，公司名称是云舟科技有限公司。"
        assert "|" not in result
        assert "Trace" not in result
        assert "RAG" not in result
        assert "Prompt" not in result
        assert "Token" not in result
        assert "Key/Secret" not in result
        assert "白名单" not in result

    def test_user_detail_hides_reference_document_names(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """员工侧历史结果清洗不展示参考文档、文件名和知识库来源。"""
        alice = _register(client, "alice")
        raw_result = (
            "知识库参考：检索到以下知识片段：1. 标题: company-service-handbook.md；分类: inquiry；"
            "相似度: 0.91 内容: company-service-handbook.md 云舟科技员工服务总览 inquiry - "
            "私人快递建议寄到个人住址,公司收发室优先处理公司业务件。"
            "公司业务件收件人需写清部门、姓名、手机号和楼层。"
        )
        _seed_ticket(
            app,
            "TK-alice-reference-source",
            alice["user_id"],
            content="【原始描述】请问私人快递可以寄到公司吗？",
            processing_result=raw_result,
        )

        resp = client.get("/api/tickets/TK-alice-reference-source")
        assert resp.status_code == 200
        result = resp.json()["processing_result"]
        assert "company-service-handbook.md" not in result
        assert "参考文档" not in result
        assert "可参考的资料要点" not in result
        assert "知识库" not in result
        assert "inquiry" not in result
        assert "相似度" not in result
        assert "私人快递" in result
        assert "收发室" in result

    def test_admin_detail_keeps_internal_processing_result(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """admin 视角保留原始处理结果用于排查。"""
        alice = _register(client, "alice")
        raw_result = "知识库参考：检索到以下知识片段：相似度: 0.40 内容: 证书未续期。"
        _seed_ticket(
            app,
            "TK-alice-admin-raw",
            alice["user_id"],
            processing_result=raw_result,
        )
        _promote(client, app, alice["user_id"], "admin")

        resp = client.get("/api/tickets/TK-alice-admin-raw")
        assert resp.status_code == 200
        assert resp.json()["processing_result"] == raw_result


class TestMessagesAndFeedbackIsolation:
    """消息列表 / 补充 / 反馈接口的 user 隔离。"""

    def test_user_lists_other_user_messages_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.get("/api/tickets/TK-bob-1/messages")
        assert resp.status_code == 403

    def test_user_posts_message_to_other_user_ticket_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        # 工单处于 waiting_user_input 状态才有补充意义，但 403 应该在状态校验之前
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.post(
            "/api/tickets/TK-bob-1/messages",
            json={"content": "补充信息"},
        )
        assert resp.status_code == 403

    def test_user_submits_feedback_for_other_user_ticket_403(
        self, client: TestClient, app: FastAPI
    ) -> None:
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _login(client, "alice")

        resp = client.post(
            "/api/tickets/TK-bob-1/feedback",
            json={"satisfied": False},
        )
        assert resp.status_code == 403

    def test_admin_cannot_submit_feedback_for_user_ticket(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """admin 可查看工单，但不能代替用户提交满意度反馈。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "admin")

        resp = client.post(
            "/api/tickets/TK-bob-1/feedback",
            json={"satisfied": False},
        )
        assert resp.status_code == 403

    def test_developer_cannot_submit_feedback_for_user_ticket(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """developer 不开放员工反馈接口。"""
        alice = _register(client, "alice")
        _logout_then_register_other(client, "bob")
        bob = client.portal.call(
            app.state.db_manager.get_user_by_username, "bob"
        )
        _seed_ticket(app, "TK-bob-1", bob["user_id"])
        _promote(client, app, alice["user_id"], "developer")

        resp = client.post(
            "/api/tickets/TK-bob-1/feedback",
            json={"satisfied": False},
        )
        assert resp.status_code == 403

    def test_completed_ticket_accepts_one_satisfied_feedback(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """completed 工单允许所属员工提交一次满意反馈。"""
        alice = _register(client, "alice")
        _seed_ticket(app, "TK-feedback-satisfied", alice["user_id"], status="completed")

        resp = client.post(
            "/api/tickets/TK-feedback-satisfied/feedback",
            json={"satisfied": True},
        )
        duplicate = client.post(
            "/api/tickets/TK-feedback-satisfied/feedback",
            json={"satisfied": True},
        )

        assert resp.status_code == 200, resp.text
        assert duplicate.status_code == 409
        ticket = client.portal.call(
            app.state.db_manager.get_ticket, "TK-feedback-satisfied"
        )
        assert ticket["satisfied"] == 1

    def test_dissatisfied_feedback_requires_reason_and_creates_user_request_review(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """不满意反馈必须带原因，并创建 user_request 人工复核。"""
        alice = _register(client, "alice")
        _seed_ticket(app, "TK-feedback-appeal", alice["user_id"], status="completed")

        missing_reason = client.post(
            "/api/tickets/TK-feedback-appeal/feedback",
            json={"satisfied": False},
        )
        resp = client.post(
            "/api/tickets/TK-feedback-appeal/feedback",
            json={"satisfied": False, "reason": "处理建议无法解决企业邮箱登录问题"},
        )

        assert missing_reason.status_code == 422
        assert resp.status_code == 200, resp.text
        review = client.portal.call(
            app.state.db_manager.get_pending_review_by_ticket, "TK-feedback-appeal"
        )
        assert review is not None
        assert review["trigger_type"] == "user_request"
        assert "企业邮箱登录问题" in review["trigger_reason"]


class TestDemoModeBypass:
    """演示模式（auth_enabled=false）下全放行。"""

    @pytest.fixture(autouse=True)
    def _disable_auth(self, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")

    def test_demo_mode_user_views_any_ticket_200(
        self, client: TestClient, app: FastAPI
    ) -> None:
        # 演示模式不要求登录；直接植入工单访问
        _seed_ticket(app, "TK-demo-1", "any-user-id")
        resp = client.get("/api/tickets/TK-demo-1")
        assert resp.status_code == 200


# ============================================================
# 辅助：注销当前用户 + 注册新用户（不保持原 session）
# ============================================================


def _logout_then_register_other(client: TestClient, username: str) -> None:
    """注销当前用户，再注册新用户（会自动登录新用户）。"""
    client.post("/api/auth/logout")
    _register(client, username)
