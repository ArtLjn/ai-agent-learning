"""API 端点测试。

使用 FastAPI TestClient 测试工单 CRUD、知识库上传和统计接口，
通过 mock 外部依赖（workflow、知识库工具）隔离 API 层逻辑。
"""

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from src.multi_agent_system.api.routes import router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.tools.analytics import AnalyticsTool
from src.multi_agent_system.tools.db_query import DBQueryTool
from tests.conftest import TEST_DATABASE_URL


class _FakeWorkflow:
    """测试用工作流，提供符合 routes._run_workflow 预期的异步迭代接口。"""

    async def astream(self, state):
        yield {
            "process": {
                "status": "processing",
                "processing_result": "测试处理完成",
                "references": ["测试知识库片段"],
            }
        }
        yield {"complete": {"status": "completed"}}


class _FakeTicketIntentAgent:
    """测试用意图理解 Agent，返回确定的结构化工单。"""

    async def extract(self, content: str) -> dict:
        return {
            "title": "系统无法登录",
            "category": "technical",
            "priority": "P1",
            "impact": "部分用户受影响",
            "expectation": "尽快恢复登录",
            "contact": "ops@example.com",
            "occurred_at": "今天 10:15",
            "confidence": 0.92,
            "reason": "测试固定结果",
            "content": (
                "【问题标题】系统无法登录\n"
                "【问题类型】技术支持\n"
                "【紧急程度】P1 高\n"
                "【影响范围】部分用户受影响\n"
                "【期望处理】尽快恢复登录\n"
                "【联系方式】ops@example.com\n"
                f"【原始描述】{content}"
            ),
        }


@pytest.fixture
def app():
    """构建测试用 FastAPI 应用，db_manager 在 lifespan 内创建。"""
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        db_manager = DatabaseManager(database_url=TEST_DATABASE_URL)
        await db_manager.initialize()
        await db_manager.truncate_all()
        db_tool = DBQueryTool(db_manager=db_manager)
        analytics_tool = AnalyticsTool(db_manager=db_manager)
        mock_knowledge_tool = MagicMock()
        application.state.db_manager = db_manager
        application.state.db_tool = db_tool
        application.state.settings = Settings()
        application.state.workflow = _FakeWorkflow()
        application.state.ticket_intent_agent = _FakeTicketIntentAgent()
        application.state.knowledge_tool = mock_knowledge_tool
        application.state.analytics_tool = analytics_tool
        yield
        await db_manager.close()

    application = FastAPI(lifespan=lifespan)
    application.add_middleware(SessionMiddleware, secret_key="test-session-secret-32-chars-or-more")
    application.include_router(router, prefix="/api")
    return application


@pytest.fixture(autouse=True)
def _disable_auth(monkeypatch):
    """reviews/knowledge/settings 路由带 require_role，演示模式视为 admin 放行。"""
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def client(app):
    """创建 TestClient 实例，with 触发 lifespan。"""
    with TestClient(app) as c:
        app.state._portal = c.portal
        yield c


class TestTicketAPI:
    """工单 API 测试。"""

    def test_create_ticket(self, client):
        """POST /api/tickets 创建工单。"""
        response = client.post(
            "/api/tickets",
            json={"content": "系统报错", "user_id": "U001"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "ticket_id" in data
        assert data["status"] == "received"

    def test_create_ticket_without_user_id(self, client):
        """POST /api/tickets 不传 user_id 也能成功创建。"""
        response = client.post(
            "/api/tickets",
            json={"content": "咨询问题"},
        )

        assert response.status_code == 200
        assert "ticket_id" in response.json()

    def test_create_ticket_uses_intent_agent(self, client):
        """POST /api/tickets 使用 Agent 理解自然语言并保存结构化字段。"""
        response = client.post(
            "/api/tickets",
            json={"content": "今天 10:15 系统无法登录，请尽快恢复", "user_id": "U007"},
        )

        assert response.status_code == 200
        data = response.json()
        ticket = client.app.state._portal.call(
            client.app.state.db_tool.get_ticket,
            data["ticket_id"],
        )
        assert ticket["category"] == "technical"
        assert ticket["priority"] == "P1"
        assert "【原始描述】今天 10:15 系统无法登录，请尽快恢复" in ticket["content"]

    def test_get_ticket(self, client):
        """GET /api/tickets/{id} 查询工单详情。"""
        ticket_id = "T-QUERY-001"
        client.app.state._portal.call(client.app.state.db_tool.save_ticket, {
            "ticket_id": ticket_id,
            "content": "查询测试",
            "status": "completed",
            "references": ["测试知识库片段"],
        })

        # 通过 API 查询
        response = client.get(f"/api/tickets/{ticket_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["ticket_id"] == ticket_id
        assert data["content"] == "查询测试"
        assert data["references"] == ["测试知识库片段"]

    def test_get_ticket_not_found(self, client):
        """GET /api/tickets/{id} 查询不存在的工单返回 404。"""
        response = client.get("/api/tickets/nonexistent_id")

        assert response.status_code == 404

    def test_list_tickets(self, client):
        """GET /api/tickets 返回工单列表。"""
        # 创建两个工单
        client.post("/api/tickets", json={"content": "工单1"})
        client.post("/api/tickets", json={"content": "工单2"})

        response = client.get("/api/tickets")

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2

    def test_list_tickets_with_filter(self, client):
        """GET /api/tickets?status=completed 按状态过滤。"""
        # 创建一个工单并通过 workflow 模拟完成
        create_resp = client.post("/api/tickets", json={"content": "已完成工单"})
        ticket_id = create_resp.json()["ticket_id"]

        # 直接修改数据库中的状态为 completed
        db_tool = client.app.state.db_tool
        client.app.state._portal.call(db_tool.save_ticket, {
            "ticket_id": ticket_id,
            "content": "已完成工单",
            "status": "completed",
            "category": "technical",
            "created_at": datetime.now().isoformat(),
        })

        response = client.get("/api/tickets?status=completed")

        assert response.status_code == 200
        data = response.json()
        assert all(t["status"] == "completed" for t in data)

    def test_list_tickets_with_category_filter(self, client):
        """GET /api/tickets?category=technical 按分类过滤。"""
        db_tool = client.app.state.db_tool
        client.app.state._portal.call(db_tool.save_ticket, {
            "ticket_id": "T020",
            "content": "技术工单",
            "status": "completed",
            "category": "technical",
            "created_at": datetime.now().isoformat(),
        })
        client.app.state._portal.call(db_tool.save_ticket, {
            "ticket_id": "T021",
            "content": "账务工单",
            "status": "completed",
            "category": "billing",
            "created_at": datetime.now().isoformat(),
        })

        response = client.get("/api/tickets?category=technical")

        assert response.status_code == 200
        data = response.json()
        assert all(t["category"] == "technical" for t in data)


class TestKnowledgeAPI:
    """知识库 API 测试。"""

    def test_upload_knowledge(self, client):
        """POST /api/knowledge 上传知识库文档。"""
        client.app.state.knowledge_tool.add_documents = MagicMock(return_value=3)

        response = client.post(
            "/api/knowledge",
            json={"title": "测试文档", "content": "这是测试内容"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["chunks_added"] == 3

    def test_list_knowledge(self, client):
        """GET /api/knowledge 返回已上传文档列表。"""
        client.app.state.knowledge_tool.list_documents = MagicMock(
            return_value={
                "documents": [
                    {
                        "id": "doc-1",
                        "title": "登录故障手册",
                        "category": "technical",
                        "content": "ERR-5001 处理步骤",
                        "preview": "ERR-5001 处理步骤",
                        "chunk_count": 1,
                        "chunks": [],
                    }
                ],
                "count": 1,
                "next_offset": None,
            }
        )

        response = client.get("/api/knowledge")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["documents"][0]["title"] == "登录故障手册"

    def test_generate_mock_ticket_uses_llm_with_random_knowledge(self, client):
        """GET /api/tickets/mock-question 基于知识库片段调用 LLM 生成模拟问题。"""
        client.app.state.knowledge_tool.list_documents = MagicMock(
            return_value={
                "documents": [
                    {
                        "id": "doc-1",
                        "title": "登录故障手册",
                        "category": "technical",
                        "content": "二次验证丢失时需要检查备用码和 TOTP 时间同步。",
                        "preview": "二次验证丢失时需要检查备用码和 TOTP 时间同步。",
                        "chunk_count": 1,
                        "chunks": [],
                    }
                ],
                "count": 1,
                "next_offset": None,
            }
        )
        llm_response = MagicMock()
        llm_response.choices = [
            MagicMock(
                message=MagicMock(
                    content="我换了手机后二次验证收不到验证码了，请帮我恢复登录。"
                )
            )
        ]

        with patch(
            "src.multi_agent_system.api.routes.CachedLLMClient"
        ) as client_cls:
            client_cls.return_value.chat_completions_create = AsyncMock(
                return_value=llm_response
            )
            response = client.get("/api/tickets/mock-question")

        assert response.status_code == 200
        data = response.json()
        assert data["prompt"] == "我换了手机后二次验证收不到验证码了，请帮我恢复登录。"
        assert data["generation_mode"] == "llm"
        assert data["knowledge_title"] == "登录故障手册"
        client_cls.return_value.chat_completions_create.assert_awaited_once()

    def test_generate_mock_ticket_passes_selected_category_to_llm(self, client):
        """GET /api/tickets/mock-question?category=inquiry 会约束 LLM 生成咨询类问题。"""
        client.app.state.knowledge_tool.list_documents = MagicMock(
            return_value={
                "documents": [
                    {
                        "id": "doc-1",
                        "title": "报表导出说明",
                        "category": "technical",
                        "content": "用户可以在工单列表右上角导出本月报表。",
                        "preview": "用户可以在工单列表右上角导出本月报表。",
                        "chunk_count": 1,
                        "chunks": [],
                    }
                ],
                "count": 1,
                "next_offset": None,
            }
        )
        llm_response = MagicMock()
        llm_response.choices = [
            MagicMock(message=MagicMock(content="我想咨询本月工单报表在哪里导出？"))
        ]

        with patch(
            "src.multi_agent_system.api.routes.CachedLLMClient"
        ) as client_cls:
            client_cls.return_value.chat_completions_create = AsyncMock(
                return_value=llm_response
            )
            response = client.get("/api/tickets/mock-question?category=inquiry")

        assert response.status_code == 200
        data = response.json()
        assert data["category"] == "inquiry"
        call_kwargs = client_cls.return_value.chat_completions_create.await_args.kwargs
        prompt_text = "\n".join(message["content"] for message in call_kwargs["messages"])
        assert "咨询类" in prompt_text
        assert "避免生成系统故障、P0、投诉或退款语气" in prompt_text

    def test_generate_mock_ticket_fallback_uses_selected_category_without_knowledge(self, client):
        """知识库为空时，选咨询类型也应返回咨询类兜底问题。"""
        client.app.state.knowledge_tool.list_documents = MagicMock(
            return_value={"documents": [], "count": 0, "next_offset": None}
        )

        response = client.get("/api/tickets/mock-question?category=inquiry")

        assert response.status_code == 200
        data = response.json()
        assert data["generation_mode"] == "fallback"
        assert data["category"] == "inquiry"
        assert "咨询" in data["prompt"]

    def test_upload_knowledge_missing_fields(self, client):
        """POST /api/knowledge 缺少必填字段返回 400。"""
        response = client.post(
            "/api/knowledge",
            json={"title": "只有标题"},
        )

        assert response.status_code == 400

    def test_upload_knowledge_no_tool(self, client):
        """知识库工具不可用时返回 503。"""
        client.app.state.knowledge_tool = None

        response = client.post(
            "/api/knowledge",
            json={"title": "测试", "content": "内容"},
        )

        assert response.status_code == 503


class TestAnalyticsAPI:
    """统计 API 测试。"""

    def test_analytics(self, client):
        """GET /api/analytics 返回统计数据。"""
        db_tool = client.app.state.db_tool
        client.app.state._portal.call(db_tool.save_ticket, {
            "ticket_id": "A001",
            "content": "技术问题",
            "category": "technical",
            "priority": "P1",
            "status": "completed",
            "review_score": 0.9,
            "retry_count": 0,
            "created_at": datetime.now().isoformat(),
        })

        response = client.get("/api/analytics")

        assert response.status_code == 200
        data = response.json()
        assert "category_distribution" in data
        assert "priority_distribution" in data
        assert "resolution_stats" in data
        assert "daily_stats" in data

    def test_analytics_empty(self, client):
        """无工单数据时统计接口正常返回。"""
        response = client.get("/api/analytics")

        assert response.status_code == 200
        data = response.json()
        assert data["resolution_stats"]["total"] == 0


class TestSettingsAPI:
    """系统设置 API 测试。"""

    def test_settings_summary(self, client):
        """GET /api/settings 返回前端设置页所需的只读配置摘要。"""
        response = client.get("/api/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["llm_model"]
        assert "llm_api_key_configured" in data
        assert "knowledge_available" in data
        assert "model_routes" in data
        assert "max_react_iterations" in data
