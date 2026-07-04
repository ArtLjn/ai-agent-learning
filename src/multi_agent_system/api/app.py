"""FastAPI 应用主模块，管理应用生命周期和路由注册。"""

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse, FileResponse, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

from src.multi_agent_system.agents.classifier import ClassifierAgent
from src.multi_agent_system.agents.coordinator import CoordinatorAgent
from src.multi_agent_system.agents.processor import ReActProcessorAgent
from src.multi_agent_system.agents.reviewer import ReviewerAgent
from src.multi_agent_system.agents.ticket_intent import TicketIntentAgent
from src.multi_agent_system.api.auth_routes import router as auth_router
from src.multi_agent_system.api.admin_audit import router as admin_audit_router
from src.multi_agent_system.api.admin_config import router as admin_config_router
from src.multi_agent_system.api.admin_prompts import router as admin_prompts_router
from src.multi_agent_system.api.admin_stats import router as admin_stats_router
from src.multi_agent_system.api.admin_trace import router as admin_trace_router
from src.multi_agent_system.api.admin_users import router as admin_users_router
from src.multi_agent_system.api.user_routes import router as user_router
from src.multi_agent_system.config import Settings
from src.multi_agent_system.core.auth import require_login
from src.multi_agent_system.tools.analytics import AnalyticsTool
from src.multi_agent_system.tools.db_query import DBQueryTool
from src.multi_agent_system.tools.knowledge_search import KnowledgeSearchTool
from src.multi_agent_system.tools.knowledge_tool_adapter import register_knowledge_tool
from src.multi_agent_system.tools.notification import NotificationTool
from src.multi_agent_system.workflow.graph import build_ticket_graph

__all__ = ["app"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动时初始化数据库、Agent 和工作流，关闭时清理。"""
    logger.info("🚀 多Agent工单处理系统启动")

    settings = Settings()

    # Initialize database
    from src.multi_agent_system.core.database import get_db_manager

    db_manager = await get_db_manager()
    app.state.db_manager = db_manager

    # Initialize base tools
    db_tool = DBQueryTool(db_manager=db_manager)
    notification_tool = NotificationTool()
    analytics_tool = AnalyticsTool(db_manager=db_manager)

    # Try to initialize knowledge base tool
    knowledge_tool = None
    try:
        knowledge_tool = KnowledgeSearchTool.create_from_settings()
        knowledge_tool.ensure_collection()
        logger.info("知识库工具初始化成功")
    except Exception as e:
        knowledge_tool = None
        logger.warning(f"知识库工具初始化失败（不影响核心功能）: {e}")

    # v2.0：初始化 rag-service HTTP 客户端（失败不阻塞，ReActProcessorAgent 走降级）
    from src.multi_agent_system.tools.rag_client import RagClient

    rag_client = RagClient.create_from_settings()
    logger.info(
        f"rag-service 客户端初始化（base_url={rag_client._base_url}, "
        f"timeout={rag_client._timeout}s, retry={rag_client._retry}）"
    )

    # Initialize memory manager
    from src.multi_agent_system.core.memory import MemoryManager

    memory_manager = MemoryManager(db_manager=db_manager)

    # Initialize trace manager
    from src.multi_agent_system.core.trace import TraceManager

    trace_manager = TraceManager(db_manager=db_manager)

    # Initialize tool registry and register tools
    from src.multi_agent_system.core.tool_base import ToolRegistry

    tool_registry = ToolRegistry()
    register_knowledge_tool(tool_registry, knowledge_tool)

    # Initialize Agents
    classifier = ClassifierAgent.create_from_settings()
    ticket_intent_agent = TicketIntentAgent.create_from_settings()
    processor = ReActProcessorAgent.create_from_settings(
        tool_registry=tool_registry,
        knowledge_tool=knowledge_tool,
        rag_client=rag_client,
    )
    reviewer = ReviewerAgent.create_from_settings()
    coordinator = CoordinatorAgent.create_from_settings(
        notification_tool=notification_tool,
        knowledge_tool=knowledge_tool,
    )

    # Build workflow
    agents = {
        "classifier": classifier,
        "processor": processor,
        "reviewer": reviewer,
        "coordinator": coordinator,
    }
    workflow = build_ticket_graph(
        settings=settings,
        agents=agents,
        trace_manager=trace_manager,
        db_manager=db_manager,
    )

    # Store in app state
    app.state.settings = settings
    app.state.db_manager = db_manager
    app.state.db_tool = db_tool
    app.state.notification_tool = notification_tool
    app.state.analytics_tool = analytics_tool
    app.state.knowledge_tool = knowledge_tool
    app.state.rag_client = rag_client
    app.state.memory_manager = memory_manager
    app.state.trace_manager = trace_manager
    app.state.tool_registry = tool_registry
    app.state.classifier = classifier
    app.state.ticket_intent_agent = ticket_intent_agent
    app.state.processor = processor
    app.state.reviewer = reviewer
    app.state.coordinator = coordinator
    app.state.workflow = workflow

    # D-02：加载 prompt_versions 表中 active 模板覆盖代码默认
    from src.multi_agent_system.core.prompt_loader import load_active_prompts

    await load_active_prompts(
        db_manager,
        {
            "intent": ticket_intent_agent,
            "classify": classifier,
            "process": processor,
            "review": reviewer,
            "coordinator": coordinator,
        },
    )

    # Restore unfinished checkpoints
    checkpoints = await db_manager.list_active_checkpoints()
    if checkpoints:
        logger.info(f"发现 {len(checkpoints)} 个未完成的检查点（恢复功能待实现）")

    logger.info("应用初始化完成")

    yield

    # Cleanup
    logger.info("🛑 应用关闭中，清理资源...")
    from src.multi_agent_system.core.cache import reset_cache

    reset_cache()
    await db_manager.close()
    logger.info("✅ 资源清理完成")


class MetricsMiddleware(BaseHTTPMiddleware):
    """记录请求延迟和错误率的中间件。"""

    async def dispatch(self, request: Request, call_next):
        """处理请求并记录指标。"""
        from src.multi_agent_system.core.metrics import metrics_collector

        metrics_collector.active_requests.inc()
        start = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration = time.time() - start
            endpoint = request.url.path
            metrics_collector.record_http_request(
                method=request.method,
                endpoint=endpoint,
                status_code=status_code,
                duration_seconds=duration,
            )
            metrics_collector.active_requests.dec()
            metrics_collector.update_uptime()


app = FastAPI(
    title="多Agent工单处理系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(MetricsMiddleware)

# 自定义 CORS 中间件：HTTP 正常添加跨域头，WebSocket 直接放行
# Starlette 自带的 CORSMiddleware 会拦截 WebSocket 的 Origin 检查导致 403


class _CORSAllowAll:
    """ASGI 中间件：允许所有来源的 HTTP 和 WebSocket 请求。"""

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "websocket":
            # WebSocket 直通，不做 Origin 检查
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http":
            # 处理预检请求
            if scope.get("method") == "OPTIONS":
                from starlette.responses import Response

                response = Response(
                    status_code=204,
                    headers={
                        "access-control-allow-origin": "*",
                        "access-control-allow-methods": "*",
                        "access-control-allow-headers": "*",
                        "access-control-max-age": "86400",
                    },
                )
                await response(scope, receive, send)
                return

            # 给正常响应注入 CORS 头
            async def _send_with_cors(message):  # noqa: ANN001
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"access-control-allow-origin", b"*"))
                    headers.append((b"access-control-allow-methods", b"*"))
                    headers.append((b"access-control-allow-headers", b"*"))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, _send_with_cors)
            return

        await self.app(scope, receive, send)


app.add_middleware(_CORSAllowAll)

# A-07 操作日志审计中间件：在 Session 之后执行（更内层）才能读到 scope["session"]。
# 用 lambda 延迟取 app.state.db_manager（lifespan 启动后才挂载）。
from src.multi_agent_system.core.audit_middleware import AuditMiddleware  # noqa: E402


def _get_db_manager_for_audit():
    """延迟取 db_manager；测试环境无 app.state 时返回 None 安全跳过。"""
    return getattr(app.state, "db_manager", None)


app.add_middleware(AuditMiddleware, db_manager_getter=_get_db_manager_for_audit)

# Session 中间件（cookie-based，签名用 auth_session_secret）
_settings_for_mw = Settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings_for_mw.auth_session_secret,
    session_cookie="agentdesk_session",
    max_age=86400 * 7,  # 7 天
    same_site="lax",
    https_only=True,
)

# 鉴权路由（公开：login/register/logout/me）
app.include_router(auth_router, prefix="/api")

# 用户自助路由（要求登录）
app.include_router(user_router, prefix="/api", dependencies=[Depends(require_login)])

# 管理员路由（要求 admin 角色，auth_enabled=false 演示模式视为 admin 放行）
app.include_router(admin_users_router, prefix="/api")
app.include_router(admin_config_router, prefix="/api")
app.include_router(admin_audit_router, prefix="/api")
app.include_router(admin_prompts_router, prefix="/api")
app.include_router(admin_stats_router, prefix="/api")
app.include_router(admin_trace_router, prefix="/api")

# 业务路由（全部要求登录，auth_enabled=false 时自动放行）
from src.multi_agent_system.api.routes import router  # noqa: E402

app.include_router(router, prefix="/api", dependencies=[Depends(require_login)])


@app.get("/health")
async def health_check() -> dict:
    """健康检查端点。"""
    from src.multi_agent_system.core.cache import _get_llm_cache
    from src.multi_agent_system.core.model_router import get_model_router

    cache = _get_llm_cache()
    cache_stats = cache.get_stats() if cache else {"enabled": False}
    router = get_model_router()

    return {
        "status": "healthy",
        "version": "1.0.0",
        "cache": cache_stats,
        "routes": router.get_stats() if router else {},
        "timestamp": time.time(),
    }


@app.get("/metrics")
async def metrics() -> dict:
    """性能指标端点（JSON 格式）。"""
    from src.multi_agent_system.core.metrics import metrics_collector
    return metrics_collector.get_stats()


@app.get("/prometheus")
async def prometheus_metrics():
    """Prometheus 指标抓取端点（标准 exposition 格式）。"""
    from starlette.responses import Response
    from src.multi_agent_system.core.metrics import generate_latest, CONTENT_TYPE_LATEST, metrics_collector

    metrics_collector.update_uptime()
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# ============================================================
# React SPA 静态文件托管（必须放在所有 API 路由之后）
# ============================================================
_WEB_DIST = Path(__file__).parent.parent.parent.parent / "web" / "dist"
_WEB_INDEX = _WEB_DIST / "index.html"

# 挂载 assets 目录（正确 MIME 类型）
_ASSETS_DIR = _WEB_DIST / "assets"
if _ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="static_assets")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def _spa_index() -> Response:
    """React SPA 首页。"""
    if _WEB_INDEX.exists():
        return HTMLResponse(_WEB_INDEX.read_text(encoding="utf-8"))
    legacy_html = Path(__file__).parent.parent / "web" / "index.html"
    if legacy_html.exists():
        return HTMLResponse(legacy_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>No frontend. Run <code>cd web && npm run build</code></h1>", status_code=404)


@app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
async def _spa_fallback(path: str) -> Response:
    """SPA 路由回退：dist 中的静态文件直接返回，其余交给 React Router。"""
    static_file = _WEB_DIST / path
    if static_file.is_file():
        return FileResponse(str(static_file))
    if _WEB_INDEX.exists():
        return HTMLResponse(_WEB_INDEX.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Not Found</h1>", status_code=404)
