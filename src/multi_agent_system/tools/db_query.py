"""数据库查询工具，基于 SQLite 持久化存储工单和用户数据。"""

from datetime import datetime
from typing import Any

from loguru import logger

from src.multi_agent_system.core.database import DatabaseManager, get_db_manager

__all__ = ["DBQueryTool"]


class DBQueryTool:
    """SQLite 数据库查询工具。

    提供工单 CRUD、用户查询和历史记录功能。
    支持传入外部 DatabaseManager 或自动获取全局实例。

    Args:
        db_manager: 数据库管理器实例，为 None 时自动获取全局实例
    """

    def __init__(self, db_manager: DatabaseManager | None = None) -> None:
        self._db = db_manager

    async def _get_db(self) -> DatabaseManager:
        if self._db is not None:
            return self._db
        return await get_db_manager()

    async def save_ticket(self, ticket_data: dict[str, Any]) -> None:
        db = await self._get_db()
        await db.save_ticket(ticket_data)
        logger.debug(f"已保存工单: {ticket_data.get('ticket_id')}")

    async def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        db = await self._get_db()
        return await db.get_ticket(ticket_id)

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        db = await self._get_db()
        return await db.get_user(user_id)

    async def get_ticket_history(self, user_id: str, limit: int = 10) -> list[dict[str, Any]]:
        db = await self._get_db()
        return await db.get_user_tickets(user_id, limit)

    async def get_similar_tickets(self, category: str, limit: int = 5) -> list[dict[str, Any]]:
        db = await self._get_db()
        return await db.list_tickets(category=category, limit=limit)

    async def list_tickets(
        self,
        status: str | None = None,
        category: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        db = await self._get_db()
        return await db.list_tickets(
            status=status,
            category=category,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )

    async def ensure_user(self, user_id: str, name: str = "") -> dict[str, Any]:
        db = await self._get_db()
        user = await db.get_user(user_id)
        if user is None:
            user = {
                "user_id": user_id,
                "name": name,
                "vip_level": 0,
                "total_tickets": 0,
            }
            await db.save_user(user)
        return user

    async def update_user_stats(self, user_id: str, satisfaction: bool | None = None) -> None:
        db = await self._get_db()
        user = await db.get_user(user_id)
        if user is None:
            return

        total = user.get("total_tickets", 0) + 1
        user["total_tickets"] = total
        user["last_contact"] = datetime.now().isoformat()

        if satisfaction is not None:
            current_avg = user.get("avg_satisfaction", 0.0) or 0.0
            # Simple rolling average
            user["avg_satisfaction"] = (current_avg * (total - 1) + (1.0 if satisfaction else 0.0)) / total

        await db.save_user(user)
