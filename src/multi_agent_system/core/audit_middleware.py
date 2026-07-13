"""A-07 操作日志审计中间件（纯 ASGI 实现）。

拦截所有 admin 写操作（POST/PATCH/DELETE 到 /api/admin/* + POST /api/reviews/*/decision），
**响应成功（2xx）后**异步写入 audit_logs。

设计：
- 用纯 ASGI middleware（不用 BaseHTTPMiddleware）：因为 BaseHTTPMiddleware
  在 dispatch 中 await request.body() 会让下游路由再读不到 body（FastAPI
  知名 issue）。纯 ASGI 形式可以重写 receive callable 让下游能再读一次。
- 仅记录成功响应，避免噪声
- action 通过路径推断（详见 _ACTION_RULES）
- detail 字段过滤密码类字段
- detail JSON 序列化后超 4KB 截断 + 标记 truncated=true

action 推断覆盖 7 类（与 tasks.md 6.4 对齐）：
  POST   /api/reviews/{id}/decision               -> review_decision
  PATCH  /api/admin/users/{id}                     -> user_role_change / user_ban / user_unban
  PATCH  /api/admin/users/{id}/quota               -> quota_update
  DELETE /api/admin/knowledge/{doc_id}             -> knowledge_delete
  POST   /api/admin/knowledge/{doc_id}/rollback    -> knowledge_rollback
  POST   /api/admin/prompts/{agent}/versions/{v}/activate -> prompt_activate
  POST   /api/admin/prompts/{agent}/rollback              -> prompt_rollback
  POST   /api/admin/users/{id}/reset_password      -> password_reset
"""

import json
import re
from typing import Any, Callable, Iterable

from loguru import logger
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["AuditMiddleware", "ACTION_LABELS"]


# 密码 / 凭据类字段，detail 中不写入明文
_SENSITIVE_FIELDS = frozenset(
    {
        "password",
        "old_password",
        "new_password",
        "current_password",
        "password_confirm",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "api_key",
    }
)


# detail JSON 序列化后最大字节数（4KB）
_DETAIL_MAX_BYTES = 4 * 1024


# 路径模式 → action（顺序很重要：长路径优先匹配）
_ACTION_RULES: list[tuple[str, str, str]] = [
    # 审核决策
    (
        "POST",
        r"^/api/reviews/(?P<target_id>[^/]+)/decision$",
        "review_decision",
    ),
    # 用户管理 - 配额
    (
        "PATCH",
        r"^/api/admin/users/(?P<target_id>[^/]+)/quota$",
        "quota_update",
    ),
    # 用户管理 - 重置密码
    (
        "POST",
        r"^/api/admin/users/(?P<target_id>[^/]+)/reset_password$",
        "password_reset",
    ),
    # 用户管理 - 角色 / 状态（按 body 内容细分）
    (
        "PATCH",
        r"^/api/admin/users/(?P<target_id>[^/]+)/?$",
        "user_update",
    ),
    # 知识库 - 回滚（先于 delete 匹配）
    (
        "POST",
        r"^/api/admin/knowledge/(?P<target_id>[^/]+)/rollback$",
        "knowledge_rollback",
    ),
    # 知识库 - 删除
    (
        "DELETE",
        r"^/api/admin/knowledge/(?P<target_id>[^/]+)/?$",
        "knowledge_delete",
    ),
    # Prompt 版本激活
    (
        "POST",
        r"^/api/admin/prompts/(?P<agent>[^/]+)/versions/(?P<version>\d+)/activate$",
        "prompt_activate",
    ),
    (
        "POST",
        r"^/api/admin/prompts/(?P<target_id>[^/]+)/activate$",
        "prompt_activate",
    ),
    (
        "POST",
        r"^/api/admin/prompts/(?P<target_id>[^/]+)/rollback$",
        "prompt_rollback",
    ),
]


# 中间件暴露给前端的 action 标签映射
ACTION_LABELS: dict[str, str] = {
    "review_decision": "审核决策",
    "user_role_change": "用户角色变更",
    "user_ban": "用户封禁",
    "user_unban": "用户解封",
    "quota_update": "配额调整",
    "password_reset": "密码重置",
    "knowledge_delete": "知识库删除",
    "knowledge_rollback": "知识库回滚",
    "prompt_activate": "Prompt 激活",
    "prompt_rollback": "Prompt 回滚",
}


def _match_action(method: str, path: str) -> tuple[str, str | None] | None:
    """匹配 (method, path) 到 (action, target_id)。"""
    for rule_method, pattern, action in _ACTION_RULES:
        if rule_method != method:
            continue
        m = re.match(pattern, path)
        if m:
            groupdict = m.groupdict()
            target_id = groupdict.get("target_id")
            if action == "prompt_activate" and groupdict.get("agent"):
                target_id = f"{groupdict['agent']}:{groupdict.get('version')}"
            return action, target_id
    return None


def _resolve_target_type(action: str) -> str | None:
    """从 action 推断 target_type。"""
    if action in {
        "user_role_change",
        "user_ban",
        "user_unban",
        "user_update",
        "quota_update",
        "password_reset",
    }:
        return "user"
    if action in {"knowledge_delete", "knowledge_rollback"}:
        return "knowledge"
    if action == "review_decision":
        return "review"
    if action in {"prompt_activate", "prompt_rollback"}:
        return "prompt"
    return None


def _refine_user_action(action: str, body: dict[str, Any] | None) -> str:
    """对 user_update 做二级推断：根据 body 区分 role_change / ban / unban。"""
    if action != "user_update" or not body:
        return action
    if body.get("role") is not None:
        return "user_role_change"
    status = body.get("status")
    if status == "banned":
        return "user_ban"
    if status == "active":
        return "user_unban"
    return action


def _filter_sensitive(body: dict[str, Any] | None) -> dict[str, Any] | None:
    """过滤密码类字段，返回安全副本。"""
    if not body or not isinstance(body, dict):
        return body
    return {k: v for k, v in body.items() if k.lower() not in _SENSITIVE_FIELDS}


def _truncate_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """detail JSON 超 4KB 截断 + 标记 truncated=true。"""
    try:
        s = json.dumps(detail, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"_serialization_error": True}
    if len(s.encode("utf-8")) <= _DETAIL_MAX_BYTES:
        return detail
    return {
        "_truncated": True,
        "_original_size": len(s),
        "_preview": s[:_DETAIL_MAX_BYTES],
    }


def _resolve_admin(scope: Scope) -> tuple[str | None, str | None]:
    """从 ASGI scope 取 admin_id / username。

    session 结构由 auth_routes.py 写入：session["user"] = {user_id, username, role}。
    演示模式 / 兜底管理员路径可能没有 user_id，admin_id 返回 None。
    """
    session = scope.get("session") or {}
    user_dict = session.get("user") or {}
    if not isinstance(user_dict, dict):
        return None, None
    admin_id = user_dict.get("user_id")
    admin_username = user_dict.get("username") or "anonymous"
    return admin_id, admin_username


def _resolve_ip(scope: Scope) -> str | None:
    """优先取 X-Forwarded-For 第一个 IP，回退到 client.host。"""
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers") or []
    for raw_key, raw_value in headers:
        if raw_key.lower() == b"x-forwarded-for":
            try:
                decoded = raw_value.decode("latin-1")
            except UnicodeDecodeError:
                continue
            return decoded.split(",")[0].strip() or None
    client = scope.get("client")
    # ASGI spec: client 是 (host, port) tuple
    if client and isinstance(client, (tuple, list)) and len(client) >= 1:
        host = client[0]
        if host:
            return str(host)
    return None


def parse_audit_request(
    method: str, path: str, body: dict[str, Any] | None
) -> tuple[str, str | None, str | None] | None:
    """供测试断言：返回 (action, target_id, target_type)。"""
    match = _match_action(method, path)
    if match is None:
        return None
    raw_action, target_id = match
    action = _refine_user_action(raw_action, body)
    target_type = _resolve_target_type(action)
    return action, target_id, target_type


def filter_sensitive_keys(keys: Iterable[str]) -> list[str]:
    """供测试断言：返回会被过滤的 key 列表。"""
    return [k for k in keys if k.lower() in _SENSITIVE_FIELDS]


class AuditMiddleware:
    """纯 ASGI 中间件：捕获 admin 写操作并写入 audit_logs。

    用法：
        app.add_middleware(AuditMiddleware, db_manager_getter=lambda: app.state.db_manager)

    Note: Starlette 的 add_middleware 要求类有 __init__(app, **kwargs) 签名。
    """

    def __init__(
        self,
        app: ASGIApp,
        db_manager_getter: Callable[[], Any],
    ) -> None:
        self.app = app
        self._get_db_manager = db_manager_getter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        match = _match_action(method, path)

        # 不在审计范围 → 直通
        if match is None:
            await self.app(scope, receive, send)
            return

        # 缓存请求 body + 重写 receive 让下游能再读
        body_chunks: list[bytes] = []
        more_body = True

        async def receive_cached() -> Message:
            nonlocal more_body
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                more_body = message.get("more_body", False)
            return message

        # 缓存响应状态码
        response_status: list[int] = [0]

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_status[0] = message.get("status", 0)
            await send(message)

        # 调下游
        await self.app(scope, receive_cached, send_wrapper)

        # 仅记录成功响应（2xx）
        status_code = response_status[0]
        if not (200 <= status_code < 300):
            return

        # 解析 body
        body_bytes = b"".join(body_chunks)
        body_dict: dict[str, Any] | None = None
        if body_bytes:
            try:
                parsed = json.loads(body_bytes.decode("utf-8"))
                if isinstance(parsed, dict):
                    body_dict = parsed
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_dict = None

        await self._write_audit_log(scope=scope, match=match, body_dict=body_dict)

    async def _write_audit_log(
        self,
        *,
        scope: Scope,
        match: tuple[str, str | None],
        body_dict: dict[str, Any] | None,
    ) -> None:
        raw_action, target_id = match
        action = _refine_user_action(raw_action, body_dict)
        target_type = _resolve_target_type(action)

        admin_id, admin_username = _resolve_admin(scope)
        ip = _resolve_ip(scope)

        safe_detail = _filter_sensitive(body_dict)
        truncated_detail = _truncate_detail(safe_detail or {})

        db_manager = self._get_db_manager()
        if db_manager is None:
            logger.debug("[audit] db_manager 未就绪，跳过")
            return

        try:
            await db_manager.insert_audit_log(
                admin_id=admin_id,
                admin_username=admin_username,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=truncated_detail,
                ip=ip,
            )
            logger.debug(
                f"[audit] 写入 action={action} target={target_type}/{target_id} "
                f"admin={admin_username}"
            )
        except Exception as e:
            # 审计失败不影响业务响应（仅记录日志）
            logger.warning(f"[audit] 写入失败（不影响业务）: {e}")


def is_audited_path(method: str, path: str) -> bool:
    """供测试断言：判断 (method, path) 是否会被中间件捕获。"""
    return _match_action(method, path) is not None
