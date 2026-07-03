"""Async database manager (SQLAlchemy 2.0) — 兼容 SQLite 测试与 MySQL 生产。

设计要点：
- 所有公共方法签名与返回 dict 形状保持稳定，外部调用方无需改动
- 引擎层用 SQLAlchemy AsyncEngine + async_sessionmaker 抹平方言差异
- connection() 上下文管理器返回 _RawConnShim，兼容 trace.py/evaluation.py 中的
  原始 SQL + `?` 占位符写法（内部转命名占位符 + text()）
"""

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator

from loguru import logger
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from src.multi_agent_system.models.db import (
    Base,
    CheckpointORM,
    HumanReviewORM,
    PatternORM,
    SpanORM,
    TicketMessageORM,
    TicketORM,
    TraceORM,
    UserORM,
)

__all__ = ["DatabaseManager", "get_db_manager", "reset_db_manager"]


# ============================================================
# 原始 SQL 适配层：让旧代码 `await conn.execute(sql_with_?, params)` 继续工作
# ============================================================

_PLACEHOLDER_RE = re.compile(r"\?")


def _convert_placeholders(sql: str, params: Any) -> tuple[str, dict[str, Any]]:
    """把 SQLite `?` 占位符 + list 入参转成 SQLAlchemy 命名占位符 + dict。"""
    if params is None:
        return sql, {}
    if isinstance(params, dict):
        return sql, params

    params_list = list(params)
    if "?" not in sql:
        return sql, {f"p{i}": v for i, v in enumerate(params_list)}

    def _replace(_m: re.Match, _counter: list[int] = [0]) -> str:
        idx = _counter[0]
        _counter[0] += 1
        return f":p{idx}"

    new_sql = _PLACEHOLDER_RE.sub(_replace, sql)
    return new_sql, {f"p{i}": v for i, v in enumerate(params_list)}


class _DictRow(dict):
    """支持 row["col"]、row[0] 整数索引、dict(row) 三种访问方式。"""

    def __init__(self, mapping) -> None:
        super().__init__(mapping)
        # 保留列顺序，便于 row[0] 整数索引
        self._keys = list(mapping.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)

    @classmethod
    def from_row(cls, row: Row | None) -> "_DictRow | None":
        if row is None:
            return None
        return cls(row._mapping)


class _RawResultShim:
    """包装 SQLAlchemy CursorResult，提供 aiosqlite 风格的 fetch 接口。"""

    def __init__(self, result: Any) -> None:
        self._result = result
        self.rowcount: int = getattr(result, "rowcount", -1)

    async def fetchone(self) -> _DictRow | None:
        return _DictRow.from_row(self._result.fetchone())

    async def fetchall(self) -> list[_DictRow]:
        return [_DictRow(r._mapping) for r in self._result.fetchall()]


class _RawConnShim:
    """包装 AsyncConnection，提供 `?` 占位符 SQL + cursor 风格 API。

    用于兼容 trace.py / evaluation.py 中已有的原始 SQL 写法。
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: Any = None) -> _RawResultShim:
        new_sql, params_dict = _convert_placeholders(sql, params)
        result = await self._conn.execute(text(new_sql), params_dict)
        return _RawResultShim(result)

    async def executemany(self, sql: str, params_seq: list[Any]) -> _RawResultShim:
        converted = [_convert_placeholders(sql, p)[1] for p in params_seq]
        result = await self._conn.execute(text(sql), converted)
        return _RawResultShim(result)

    async def commit(self) -> None:
        await self._conn.commit()


# ============================================================
# DatabaseManager
# ============================================================


class DatabaseManager:
    """基于 SQLAlchemy 2.0 async 的数据库管理器（仅 MySQL）。"""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None

    async def initialize(self) -> None:
        """创建引擎并建表（幂等）+ 增量迁移。"""
        self._engine = create_async_engine(
            self._url,
            pool_recycle=3600,
        )
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False, autoflush=False
        )
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._apply_schema_migrations(conn)
        logger.info(f"[Database] Initialized: {self._url.split('@')[-1]}")

    async def _apply_schema_migrations(self, conn: AsyncConnection) -> None:
        """增量列迁移：ORM 新增列在已有表上不会自动 ALTER，这里幂等补上。

        v2.0 给 users 表加了 8 列（username/password_hash/nickname/contact/
        preferred_categories/status/created_at/role），生产 MySQL 里旧表只有
        v1.x 的 7 列，启动时检查 information_schema 缺哪列才 ALTER，
        新建库（如测试 sqlite）由 create_all 直接建好，这里跳过。
        """
        migrations = [
            ("users", "username", "VARCHAR(32)"),
            ("users", "password_hash", "VARCHAR(255)"),
            ("users", "nickname", "VARCHAR(32)"),
            ("users", "contact", "VARCHAR(128)"),
            ("users", "preferred_categories", "TEXT"),
            ("users", "status", "VARCHAR(16) NOT NULL DEFAULT 'active'"),
            ("users", "created_at", "DATETIME"),
            ("users", "role", "VARCHAR(16) NOT NULL DEFAULT 'user'"),
        ]

        # 取当前库的现有列，避免逐列查询
        url = make_url(self._url)
        # sqlite 无 information_schema，测试库由 create_all 建好所有列，跳过迁移
        if url.drivername.startswith("sqlite"):
            return
        db_name = url.database or None
        if not db_name:
            return

        existing_rows = await conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = :db"
        ), {"db": db_name})
        existing = {(r[0], r[1]) for r in existing_rows.fetchall()}

        applied = 0
        for table, col, dtype in migrations:
            if (table, col) in existing:
                continue
            # 给 username 加唯一索引（创建列时一并加，避免漏 INDEX）
            extra = ""
            if col == "username":
                extra = ", ADD UNIQUE INDEX idx_users_username (username)"
            elif col == "role":
                extra = ", ADD INDEX idx_users_role (role)"
            await conn.execute(text(
                f"ALTER TABLE `{table}` ADD COLUMN `{col}` {dtype}{extra}"
            ))
            applied += 1
            logger.info(f"[Database] Migrated: {table}.{col} ({dtype})")

        if applied:
            logger.info(f"[Database] Applied {applied} column migration(s)")

    async def truncate_all(self) -> None:
        """清空所有业务表数据（保留表结构）。供测试 fixture 隔离使用。"""
        if self._engine is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        url = make_url(self._url)
        database_name = url.database or ""
        if "test" not in database_name.lower():
            raise RuntimeError(
                f"拒绝清空非测试数据库: {database_name or '<unknown>'}"
            )
        table_names = list(reversed(sorted(Base.metadata.tables.keys())))
        async with self._engine.begin() as conn:
            if url.drivername.startswith("mysql"):
                # MySQL 的 TRUNCATE 一次只能清一张表
                await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
                for t in table_names:
                    await conn.execute(text(f"TRUNCATE TABLE `{t}`"))
                await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
            else:
                await conn.execute(text("PRAGMA foreign_keys = OFF"))
                for t in table_names:
                    await conn.execute(text(f'DELETE FROM "{t}"'))
                await conn.execute(text("PRAGMA foreign_keys = ON"))
        logger.debug(f"[Database] Truncated {len(table_names)} tables")

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[_RawConnShim, None]:
        """对外暴露原始 SQL 接口（兼容旧调用方）。"""
        if self._engine is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        async with self._engine.connect() as conn:
            yield _RawConnShim(conn)

    async def close(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            logger.info("[Database] Engine disposed")

    # ============================================================
    # 内部工具
    # ============================================================

    def _session(self):
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        return self._session_factory()

    @staticmethod
    def _orm_to_dict(obj: Any) -> dict[str, Any]:
        if obj is None:
            return {}
        out: dict[str, Any] = {}
        # 用 mapper.columns 拿到 Python 属性名（key）→ DB 列名（column.name）映射。
        # 直接用 __table__.columns 时 c.key 与 c.name 同值，遇到列名 "metadata"
        # 这种 SQLAlchemy 保留属性会被 Base.metadata 遮蔽。
        for attr_name, column in obj.__mapper__.columns.items():
            v = getattr(obj, attr_name)
            # datetime 序列化为 "YYYY-MM-DD HH:MM:SS" 字符串，保持与原 aiosqlite
            # 返回的 TIMESTAMP 行为一致，避免调用方 created_at[:10] 等切片操作失效
            if isinstance(v, datetime):
                v = v.strftime("%Y-%m-%d %H:%M:%S")
            out[column.name] = v
        return out

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        """容错地把 str/datetime 转成 datetime。"""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                # 兼容 "2026-06-27T12:34:56" 与 "2026-06-27 12:34:56"
                return datetime.fromisoformat(value.replace("T", " "))
            except ValueError:
                return None
        return None

    # ============================================================
    # Ticket CRUD
    # ============================================================

    async def save_ticket(self, ticket_data: dict[str, Any]) -> None:
        """保存或更新工单。

        若 ticket_data 中不含 references 字段，保留既有 references_json。
        """
        async with self._session() as session:
            ticket_id = ticket_data.get("ticket_id")
            existing = await session.get(TicketORM, ticket_id) if ticket_id else None

            references_in_dict = "references" in ticket_data
            if existing is None:
                # INSERT 分支
                references_value = ticket_data.get("references")
                references_json = (
                    json.dumps(references_value or [], ensure_ascii=False)
                    if references_in_dict
                    else None
                )
                data = {
                    "ticket_id": ticket_data.get("ticket_id"),
                    "user_id": ticket_data.get("user_id"),
                    "content": ticket_data.get("content"),
                    "category": ticket_data.get("category"),
                    "priority": ticket_data.get("priority"),
                    "processing_result": ticket_data.get("processing_result"),
                    "references_json": references_json,
                    "review_score": ticket_data.get("review_score"),
                    "retry_count": ticket_data.get("retry_count", 0),
                    "status": ticket_data.get("status", "received"),
                    "error": ticket_data.get("error"),
                    "resolved_at": self._parse_datetime(ticket_data.get("resolved_at")),
                    "satisfied": ticket_data.get("satisfied"),
                    "token_count": ticket_data.get("token_count", 0),
                    "tool_call_count": ticket_data.get("tool_call_count", 0),
                    "total_duration": ticket_data.get("total_duration", 0.0),
                    "created_at": self._parse_datetime(ticket_data.get("created_at")),
                }
                session.add(TicketORM(**data))
            else:
                # UPDATE 分支
                fields = [
                    "user_id", "content", "category", "priority",
                    "processing_result", "review_score", "retry_count", "status",
                    "error", "resolved_at", "satisfied", "token_count",
                    "tool_call_count", "total_duration",
                ]
                for k in fields:
                    if k in ticket_data:
                        v = ticket_data[k]
                        if k == "resolved_at":
                            v = self._parse_datetime(v)
                        setattr(existing, k, v)
                if references_in_dict:
                    references_value = ticket_data.get("references")
                    existing.references_json = json.dumps(
                        references_value or [], ensure_ascii=False
                    )
                if "created_at" in ticket_data:
                    existing.created_at = self._parse_datetime(ticket_data["created_at"])
            await session.commit()

    async def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            obj = await session.get(TicketORM, ticket_id)
            return self._orm_to_dict(obj) if obj else None

    async def list_tickets(
        self,
        status: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            stmt = select(TicketORM)
            if status:
                stmt = stmt.where(TicketORM.status == status)
            if category:
                stmt = stmt.where(TicketORM.category == category)
            stmt = stmt.order_by(TicketORM.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return [self._orm_to_dict(o) for o in result.scalars().all()]

    # ============================================================
    # User CRUD
    # ============================================================

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            obj = await session.get(UserORM, user_id)
            return self._orm_to_dict(obj) if obj else None

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """按用户名查用户，用于注册唯一性校验和登录。"""
        async with self._session() as session:
            stmt = select(UserORM).where(UserORM.username == username)
            obj = await session.scalar(stmt)
            return self._orm_to_dict(obj) if obj else None

    async def save_user(self, user_data: dict[str, Any]) -> None:
        async with self._session() as session:
            user_id = user_data.get("user_id")
            existing = await session.get(UserORM, user_id) if user_id else None
            data = {
                "name": user_data.get("name"),
                "vip_level": user_data.get("vip_level", 0),
                "preferred_category": user_data.get("preferred_category"),
                "avg_satisfaction": user_data.get("avg_satisfaction"),
                "total_tickets": user_data.get("total_tickets", 0),
                "last_contact": self._parse_datetime(user_data.get("last_contact")),
            }
            if existing is None:
                session.add(UserORM(user_id=user_id, **data))
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
            await session.commit()

    async def create_registered_user(self, user_data: dict[str, Any]) -> dict[str, Any]:
        """创建自助注册用户。username 唯一约束冲突时抛 IntegrityError。"""
        from sqlalchemy.exc import IntegrityError

        preferred = user_data.get("preferred_categories") or []
        preferred_json = (
            json.dumps(preferred, ensure_ascii=False) if preferred else None
        )
        async with self._session() as session:
            obj = UserORM(
                user_id=user_data["user_id"],
                username=user_data["username"],
                password_hash=user_data["password_hash"],
                nickname=user_data.get("nickname") or user_data["username"],
                contact=user_data.get("contact"),
                preferred_categories=preferred_json,
                vip_level=0,
                status="active",
                # 注册接口不允许选角色，强制 user；提权路径只走管理员后台
                role="user",
                created_at=datetime.utcnow(),
            )
            session.add(obj)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise

        created = await self.get_user(user_data["user_id"])
        assert created is not None, "刚创建的用户必须能查到"
        return created

    async def update_user_profile(
        self, user_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """更新 nickname/contact/preferred_categories；其他字段忽略。"""
        allowed = {"nickname", "contact", "preferred_categories"}
        valid = {k: v for k, v in updates.items() if k in allowed}
        if "preferred_categories" in valid:
            value = valid["preferred_categories"]
            if not isinstance(value, list):
                raise ValueError("preferred_categories 必须是数组")
            valid["preferred_categories"] = (
                json.dumps(value, ensure_ascii=False) if value else None
            )
        async with self._session() as session:
            obj = await session.get(UserORM, user_id)
            if obj is None:
                return None
            for k, v in valid.items():
                setattr(obj, k, v)
            await session.commit()
        return await self.get_user(user_id)

    async def update_user_password(self, user_id: str, password_hash: str) -> None:
        """写入新的密码哈希。"""
        async with self._session() as session:
            obj = await session.get(UserORM, user_id)
            if obj is None:
                return
            obj.password_hash = password_hash
            await session.commit()

    async def update_user_role(self, user_id: str, role: str) -> dict[str, Any] | None:
        """更新用户角色（user/admin/developer）。供 A-04 管理后台调用。"""
        async with self._session() as session:
            obj = await session.get(UserORM, user_id)
            if obj is None:
                return None
            obj.role = role
            await session.commit()
        return await self.get_user(user_id)

    async def list_users_by_role(self, role: str) -> list[dict[str, Any]]:
        """按角色查用户列表。"""
        async with self._session() as session:
            stmt = (
                select(UserORM)
                .where(UserORM.role == role)
                .order_by(UserORM.created_at.desc())
            )
            result = await session.execute(stmt)
            return [self._orm_to_dict(o) for o in result.scalars().all()]

    async def list_users_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        status_filter: str | None = None,
        role_filter: str | None = None,
        keyword: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """分页 + 筛选查用户列表。返回 (items, total)。

        - status_filter: active / banned
        - role_filter: user / admin / developer
        - keyword: 模糊匹配 username 或 nickname（不区分大小写）
        """
        async with self._session() as session:
            stmt = select(UserORM)
            count_stmt = select(func.count(UserORM.user_id))

            if status_filter:
                stmt = stmt.where(UserORM.status == status_filter)
                count_stmt = count_stmt.where(UserORM.status == status_filter)
            if role_filter:
                stmt = stmt.where(UserORM.role == role_filter)
                count_stmt = count_stmt.where(UserORM.role == role_filter)
            if keyword:
                pattern = f"%{keyword}%"
                stmt = stmt.where(
                    or_(
                        UserORM.username.ilike(pattern),
                        UserORM.nickname.ilike(pattern),
                    )
                )
                count_stmt = count_stmt.where(
                    or_(
                        UserORM.username.ilike(pattern),
                        UserORM.nickname.ilike(pattern),
                    )
                )

            total = await session.scalar(count_stmt) or 0
            stmt = (
                stmt.order_by(UserORM.created_at.desc())
                .offset(max(0, (page - 1) * page_size))
                .limit(page_size)
            )
            result = await session.execute(stmt)
            items = [self._orm_to_dict(o) for o in result.scalars().all()]
            return items, int(total)

    async def update_user_status(self, user_id: str, status: str) -> dict[str, Any] | None:
        """更新用户状态（active/banned）。供 A-04 管理后台调用。"""
        async with self._session() as session:
            obj = await session.get(UserORM, user_id)
            if obj is None:
                return None
            obj.status = status
            await session.commit()
        return await self.get_user(user_id)

    async def get_user_tickets(
        self, user_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            stmt = (
                select(TicketORM)
                .where(TicketORM.user_id == user_id)
                .order_by(TicketORM.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._orm_to_dict(o) for o in result.scalars().all()]

    # ============================================================
    # Checkpoint CRUD
    # ============================================================

    async def save_checkpoint(
        self,
        checkpoint_id: str,
        ticket_id: str,
        state: dict[str, Any],
        expires_at: str,
    ) -> None:
        async with self._session() as session:
            existing = await session.scalar(
                select(CheckpointORM).where(CheckpointORM.ticket_id == ticket_id)
            )
            expires_dt = self._parse_datetime(expires_at)
            state_json = json.dumps(state, ensure_ascii=False)
            if existing is None:
                session.add(
                    CheckpointORM(
                        checkpoint_id=checkpoint_id,
                        ticket_id=ticket_id,
                        state_json=state_json,
                        expires_at=expires_dt,
                    )
                )
            else:
                existing.checkpoint_id = checkpoint_id
                existing.state_json = state_json
                existing.expires_at = expires_dt
            await session.commit()

    async def get_checkpoint(self, ticket_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            now = datetime.utcnow()
            stmt = select(CheckpointORM).where(
                CheckpointORM.ticket_id == ticket_id,
                CheckpointORM.expires_at > now,
            )
            obj = await session.scalar(stmt)
            if not obj:
                return None
            result = self._orm_to_dict(obj)
            result["state"] = json.loads(result["state_json"])
            return result

    async def list_active_checkpoints(self) -> list[dict[str, Any]]:
        async with self._session() as session:
            now = datetime.utcnow()
            stmt = select(CheckpointORM).where(CheckpointORM.expires_at > now)
            result = await session.execute(stmt)
            out = []
            for obj in result.scalars().all():
                d = self._orm_to_dict(obj)
                d["state"] = json.loads(d["state_json"])
                out.append(d)
            return out

    async def delete_checkpoint(self, ticket_id: str) -> None:
        async with self._session() as session:
            await session.execute(
                CheckpointORM.__table__.delete().where(
                    CheckpointORM.ticket_id == ticket_id
                )
            )
            await session.commit()

    async def cleanup_expired_checkpoints(self) -> int:
        async with self._session() as session:
            now = datetime.utcnow()
            result = await session.execute(
                CheckpointORM.__table__.delete().where(CheckpointORM.expires_at <= now)
            )
            await session.commit()
            return result.rowcount or 0

    # ============================================================
    # Pattern CRUD
    # ============================================================

    async def get_pattern(self, category: str) -> dict[str, Any] | None:
        async with self._session() as session:
            stmt = (
                select(PatternORM)
                .where(PatternORM.category == category)
                .order_by(PatternORM.usage_count.desc())
                .limit(1)
            )
            obj = await session.scalar(stmt)
            return self._orm_to_dict(obj) if obj else None

    async def save_pattern(self, pattern_data: dict[str, Any]) -> None:
        async with self._session() as session:
            pid = pattern_data.get("pattern_id")
            existing = await session.get(PatternORM, pid) if pid else None
            data = {
                "category": pattern_data.get("category"),
                "keywords": pattern_data.get("keywords"),
                "solution_template": pattern_data.get("solution_template"),
                "success_rate": pattern_data.get("success_rate", 0.0),
                "usage_count": pattern_data.get("usage_count", 0),
            }
            if existing is None:
                session.add(PatternORM(pattern_id=pid, **data))
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
            await session.commit()

    # ============================================================
    # Trace & Span CRUD
    # ============================================================

    _TRACE_COLS = [
        "trace_id", "ticket_id", "status", "start_time",
        "end_time", "duration", "total_tokens", "total_tool_calls",
        "node_count", "error",
    ]
    _SPAN_COLS = [
        "span_id", "trace_id", "parent_span_id", "span_type",
        "name", "status", "input_data", "output_data",
        "start_time", "end_time", "duration", "metadata",
    ]

    async def save_trace(self, trace_data: dict[str, Any]) -> None:
        async with self._session() as session:
            tid = trace_data.get("trace_id")
            existing = await session.get(TraceORM, tid) if tid else None
            data = {c: trace_data.get(c) for c in self._TRACE_COLS if c != "trace_id"}
            if existing is None:
                session.add(TraceORM(trace_id=tid, **data))
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
            await session.commit()

    async def get_trace_by_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            stmt = (
                select(TraceORM, TicketORM)
                .outerjoin(TicketORM, TicketORM.ticket_id == TraceORM.ticket_id)
                .where(TraceORM.ticket_id == ticket_id)
                .order_by(TraceORM.start_time.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
            if not row:
                return None
            trace_obj, ticket_obj = row
            result = self._orm_to_dict(trace_obj)
            if ticket_obj:
                result.update({
                    "ticket_content": ticket_obj.content,
                    "ticket_category": ticket_obj.category,
                    "ticket_priority": ticket_obj.priority,
                    "ticket_result": ticket_obj.processing_result,
                    "ticket_review_score": ticket_obj.review_score,
                    "ticket_references_json": ticket_obj.references_json,
                })
            return result

    async def list_traces(
        self,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            stmt = (
                select(TraceORM, TicketORM)
                .outerjoin(TicketORM, TicketORM.ticket_id == TraceORM.ticket_id)
            )
            if status:
                stmt = stmt.where(TraceORM.status == status)
            stmt = stmt.order_by(TraceORM.start_time.desc()).limit(limit).offset(offset)
            rows = (await session.execute(stmt)).all()
            out = []
            for trace_obj, ticket_obj in rows:
                d = self._orm_to_dict(trace_obj)
                if ticket_obj:
                    d.update({
                        "ticket_content": ticket_obj.content,
                        "ticket_category": ticket_obj.category,
                        "ticket_priority": ticket_obj.priority,
                        "ticket_result": ticket_obj.processing_result,
                        "ticket_review_score": ticket_obj.review_score,
                        "ticket_references_json": ticket_obj.references_json,
                    })
                out.append(d)
            return out

    async def count_traces(self, status: str | None = None) -> int:
        async with self._session() as session:
            stmt = select(func.count()).select_from(TraceORM)
            if status:
                stmt = stmt.where(TraceORM.status == status)
            return int((await session.execute(stmt)).scalar() or 0)

    async def get_trace_stats(self, trace_id: str) -> dict[str, Any] | None:
        async with self._session() as session:
            trace_obj = await session.get(TraceORM, trace_id)
            if not trace_obj:
                return None
            result = self._orm_to_dict(trace_obj)

            # 按 span_type 聚合
            stmt = (
                select(
                    SpanORM.span_type,
                    func.count().label("count"),
                    func.avg(SpanORM.duration).label("avg_duration"),
                    func.max(SpanORM.duration).label("max_duration"),
                )
                .where(SpanORM.trace_id == trace_id, SpanORM.duration.is_not(None))
                .group_by(SpanORM.span_type)
            )
            by_type = {}
            for r in (await session.execute(stmt)).all():
                by_type[r.span_type] = {
                    "count": r.count,
                    "avg_duration": round(float(r.avg_duration), 4) if r.avg_duration else 0,
                    "max_duration": round(float(r.max_duration), 4) if r.max_duration else 0,
                }
            result["by_type"] = by_type

            # 最慢的 5 个 span
            slow_stmt = (
                select(SpanORM.name, SpanORM.span_type, SpanORM.duration)
                .where(SpanORM.trace_id == trace_id, SpanORM.duration.is_not(None))
                .order_by(SpanORM.duration.desc())
                .limit(5)
            )
            result["slowest_spans"] = [
                {"name": r.name, "span_type": r.span_type, "duration": r.duration}
                for r in (await session.execute(slow_stmt)).all()
            ]
            return result

    async def save_span(self, span_data: dict[str, Any]) -> None:
        async with self._session() as session:
            sid = span_data.get("span_id")
            existing = await session.get(SpanORM, sid) if sid else None
            data = {c: span_data.get(c) for c in self._SPAN_COLS if c != "span_id"}
            # DB 列名 metadata → ORM 属性名 metadata_（避免与 Base.metadata 冲突）
            if "metadata" in data:
                data["metadata_"] = data.pop("metadata")
            if existing is None:
                session.add(SpanORM(span_id=sid, **data))
            else:
                for k, v in data.items():
                    setattr(existing, k, v)
            await session.commit()

    async def update_span(self, span_id: str, updates: dict[str, Any]) -> None:
        async with self._session() as session:
            obj = await session.get(SpanORM, span_id)
            if obj is None:
                return
            for k, v in updates.items():
                if k == "metadata":
                    setattr(obj, "metadata_", v)
                else:
                    setattr(obj, k, v)
            await session.commit()

    async def get_spans_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        async with self._session() as session:
            stmt = (
                select(SpanORM)
                .where(SpanORM.trace_id == trace_id)
                .order_by(SpanORM.start_time)
            )
            result = await session.execute(stmt)
            return [self._orm_to_dict(o) for o in result.scalars().all()]

    # ============================================================
    # Human Review CRUD
    # ============================================================

    async def create_pending_review(self, review: dict[str, Any]) -> None:
        ai_suggestion_raw = review.get("ai_suggestion")
        if isinstance(ai_suggestion_raw, dict):
            ai_suggestion_json = json.dumps(ai_suggestion_raw, ensure_ascii=False)
        elif ai_suggestion_raw is None:
            ai_suggestion_json = None
        else:
            ai_suggestion_json = str(ai_suggestion_raw)

        async with self._session() as session:
            obj = HumanReviewORM(
                review_id=review.get("review_id"),
                ticket_id=review.get("ticket_id"),
                trigger_type=review.get("trigger_type"),
                trigger_reason=review.get("trigger_reason"),
                ai_suggestion=ai_suggestion_json,
                status="pending",
                created_at=self._parse_datetime(review.get("created_at")) or datetime.utcnow(),
            )
            session.add(obj)
            await session.commit()

    async def get_pending_review_by_ticket(
        self, ticket_id: str
    ) -> dict[str, Any] | None:
        async with self._session() as session:
            stmt = (
                select(HumanReviewORM)
                .where(HumanReviewORM.ticket_id == ticket_id)
                .order_by(HumanReviewORM.created_at.desc())
                .limit(1)
            )
            obj = await session.scalar(stmt)
            return self._orm_to_dict(obj) if obj else None

    async def update_review_decision(
        self, review_id: str, updates: dict[str, Any]
    ) -> None:
        allowed = {
            "decision", "decision_reason", "rewritten_result",
            "reviewer_id", "status", "decided_at",
        }
        unknown = set(updates.keys()) - allowed
        if unknown:
            logger.warning(
                f"update_review_decision 收到未知字段: {unknown}，将被忽略"
            )
        valid_updates = {k: v for k, v in updates.items() if k in allowed}
        if not valid_updates:
            logger.warning(
                f"update_review_decision 无有效更新字段, review_id={review_id}"
            )
            return
        async with self._session() as session:
            obj = await session.get(HumanReviewORM, review_id)
            if obj is None:
                logger.warning(f"update_review_decision 未找到 review_id={review_id}")
                return
            for k, v in valid_updates.items():
                if k == "decided_at":
                    v = self._parse_datetime(v)
                setattr(obj, k, v)
            await session.commit()

    async def list_pending_reviews(
        self,
        status: str | None = None,
        trigger_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            stmt = select(HumanReviewORM)
            if status:
                stmt = stmt.where(HumanReviewORM.status == status)
            if trigger_type:
                stmt = stmt.where(HumanReviewORM.trigger_type == trigger_type)
            stmt = stmt.order_by(HumanReviewORM.created_at.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return [self._orm_to_dict(o) for o in result.scalars().all()]

    async def list_reviews_by_ticket(self, ticket_id: str) -> list[dict[str, Any]]:
        async with self._session() as session:
            stmt = (
                select(HumanReviewORM)
                .where(HumanReviewORM.ticket_id == ticket_id)
                .order_by(HumanReviewORM.created_at.asc())
            )
            result = await session.execute(stmt)
            return [self._orm_to_dict(o) for o in result.scalars().all()]

    # ============================================================
    # Ticket Message CRUD
    # ============================================================

    async def create_ticket_message(self, message_data: dict[str, Any]) -> None:
        async with self._session() as session:
            metadata = message_data.get("metadata") or {}
            created_at = (
                self._parse_datetime(message_data.get("created_at"))
                or datetime.now()
            )
            session.add(TicketMessageORM(
                message_id=message_data["message_id"],
                ticket_id=message_data["ticket_id"],
                sender_type=message_data["sender_type"],
                sender_id=message_data.get("sender_id"),
                content=message_data["content"],
                metadata_json=json.dumps(metadata, ensure_ascii=False),
                created_at=created_at,
            ))
            await session.commit()

    async def list_ticket_messages(
        self,
        ticket_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with self._session() as session:
            stmt = (
                select(TicketMessageORM)
                .where(TicketMessageORM.ticket_id == ticket_id)
                .order_by(TicketMessageORM.created_at.asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = []
            for obj in result.scalars().all():
                row = self._orm_to_dict(obj)
                raw = row.pop("metadata_json", None)
                try:
                    row["metadata"] = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    row["metadata"] = {}
                rows.append(row)
            return rows

    async def get_review_stats(self) -> dict[str, Any]:
        async with self._session() as session:
            total = int((await session.execute(
                select(func.count()).select_from(HumanReviewORM)
            )).scalar() or 0)
            pending = int((await session.execute(
                select(func.count()).select_from(HumanReviewORM)
                .where(HumanReviewORM.status == "pending")
            )).scalar() or 0)
            decided = int((await session.execute(
                select(func.count()).select_from(HumanReviewORM)
                .where(HumanReviewORM.status == "decided")
            )).scalar() or 0)

            stmt_decision = (
                select(HumanReviewORM.decision, func.count().label("cnt"))
                .where(HumanReviewORM.decision.is_not(None))
                .group_by(HumanReviewORM.decision)
            )
            by_decision = {
                r.decision: r.cnt
                for r in (await session.execute(stmt_decision)).all()
            }

            stmt_trigger = (
                select(HumanReviewORM.trigger_type, func.count().label("cnt"))
                .group_by(HumanReviewORM.trigger_type)
            )
            by_trigger = {
                r.trigger_type: r.cnt
                for r in (await session.execute(stmt_trigger)).all()
            }

        return {
            "total": total,
            "pending": pending,
            "decided": decided,
            "by_decision": by_decision,
            "by_trigger": by_trigger,
        }

    async def list_pending_reviews_with_tickets(
        self,
        trigger_type: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """联表查询 pending 审核单 + 工单快照。"""
        priority_order = case(
            (TicketORM.priority == "P0", 0),
            (TicketORM.priority == "P1", 1),
            (TicketORM.priority == "P2", 2),
            (TicketORM.priority == "P3", 3),
            else_=9,
        )
        async with self._session() as session:
            stmt = (
                select(HumanReviewORM, TicketORM)
                .outerjoin(TicketORM, TicketORM.ticket_id == HumanReviewORM.ticket_id)
                .where(HumanReviewORM.status == "pending")
            )
            if trigger_type:
                stmt = stmt.where(HumanReviewORM.trigger_type == trigger_type)
            if category:
                stmt = stmt.where(TicketORM.category == category)
            if priority:
                stmt = stmt.where(TicketORM.priority == priority)
            stmt = stmt.order_by(priority_order.asc(), HumanReviewORM.created_at.asc())
            stmt = stmt.limit(limit).offset(offset)
            rows = (await session.execute(stmt)).all()
            out = []
            for hr, tk in rows:
                d = self._orm_to_dict(hr)
                d["ticket_content"] = tk.content if tk else None
                d["ticket_category"] = tk.category if tk else None
                d["ticket_priority"] = tk.priority if tk else None
                out.append(d)
            return out

    async def count_pending_reviews(
        self,
        trigger_type: str | None = None,
        category: str | None = None,
        priority: str | None = None,
    ) -> int:
        async with self._session() as session:
            stmt = (
                select(func.count())
                .select_from(HumanReviewORM)
                .outerjoin(TicketORM, TicketORM.ticket_id == HumanReviewORM.ticket_id)
                .where(HumanReviewORM.status == "pending")
            )
            if trigger_type:
                stmt = stmt.where(HumanReviewORM.trigger_type == trigger_type)
            if category:
                stmt = stmt.where(TicketORM.category == category)
            if priority:
                stmt = stmt.where(TicketORM.priority == priority)
            return int((await session.execute(stmt)).scalar() or 0)

    async def get_review_workbench_stats(self) -> dict[str, Any]:
        """审核工作台统计。

        平均决策时长通过 Python 端计算，避免跨方言日期函数差异。
        """
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

        async with self._session() as session:
            pending_count = int((await session.execute(
                select(func.count()).select_from(HumanReviewORM)
                .where(HumanReviewORM.status == "pending")
            )).scalar() or 0)

            decided_today = int((await session.execute(
                select(func.count()).select_from(HumanReviewORM)
                .where(
                    HumanReviewORM.status == "decided",
                    HumanReviewORM.decided_at >= today_start,
                )
            )).scalar() or 0)

            decision_distribution = {
                r.decision: int(r.cnt)
                for r in (await session.execute(
                    select(HumanReviewORM.decision, func.count().label("cnt"))
                    .where(
                        HumanReviewORM.status == "decided",
                        HumanReviewORM.decision.is_not(None),
                    )
                    .group_by(HumanReviewORM.decision)
                )).all()
            }

            # 决策时长：Python 端算
            decided_rows = (await session.execute(
                select(HumanReviewORM.decided_at, HumanReviewORM.created_at)
                .where(
                    HumanReviewORM.status == "decided",
                    HumanReviewORM.decided_at.is_not(None),
                )
            )).all()
            durations = [
                (r.decided_at - r.created_at).total_seconds()
                for r in decided_rows
                if r.decided_at and r.created_at
            ]
            avg_decision_seconds = (
                int(sum(durations) / len(durations)) if durations else 0
            )

            # AI 采纳率
            adoption_rows = (await session.execute(
                select(HumanReviewORM.decision, HumanReviewORM.ai_suggestion)
                .where(
                    HumanReviewORM.status == "decided",
                    HumanReviewORM.decision.is_not(None),
                    HumanReviewORM.ai_suggestion.is_not(None),
                )
            )).all()
            adopted = 0
            comparable = 0
            for r in adoption_rows:
                try:
                    suggestion = json.loads(r.ai_suggestion)
                except (json.JSONDecodeError, TypeError):
                    continue
                recommended = (
                    suggestion.get("recommended_decision")
                    if isinstance(suggestion, dict)
                    else None
                )
                if not recommended:
                    continue
                comparable += 1
                if r.decision == recommended:
                    adopted += 1
            ai_adoption_rate = (
                round(adopted / comparable, 4) if comparable > 0 else 0.0
            )

        return {
            "pending_count": pending_count,
            "decided_today": decided_today,
            "decision_distribution": decision_distribution,
            "avg_decision_seconds": avg_decision_seconds,
            "ai_adoption_rate": ai_adoption_rate,
        }

    # ============================================================
    # Analytics
    # ============================================================

    async def get_category_distribution(self) -> dict[str, int]:
        async with self._session() as session:
            stmt = (
                select(TicketORM.category, func.count().label("cnt"))
                .group_by(TicketORM.category)
            )
            return {
                (r.category or "uncategorized"): r.cnt
                for r in (await session.execute(stmt)).all()
            }

    async def get_priority_distribution(self) -> dict[str, int]:
        async with self._session() as session:
            stmt = (
                select(TicketORM.priority, func.count().label("cnt"))
                .group_by(TicketORM.priority)
            )
            return {
                (r.priority or "unassigned"): r.cnt
                for r in (await session.execute(stmt)).all()
            }

    async def get_resolution_stats(self) -> dict[str, Any]:
        async with self._session() as session:
            total = int((await session.execute(
                select(func.count()).select_from(TicketORM)
            )).scalar() or 0)
            completed = int((await session.execute(
                select(func.count()).select_from(TicketORM)
                .where(TicketORM.status == "completed")
            )).scalar() or 0)
            failed = int((await session.execute(
                select(func.count()).select_from(TicketORM)
                .where(TicketORM.status == "failed")
            )).scalar() or 0)
            avg_retries = (await session.execute(
                select(func.avg(TicketORM.retry_count))
            )).scalar()
            success_rate = completed / total if total > 0 else 0.0

            return {
                "total": total,
                "completed": completed,
                "failed": failed,
                "avg_retries": round(float(avg_retries or 0), 2),
                "success_rate": round(success_rate, 4),
            }


# ============================================================
# Global singleton
# ============================================================

_db_manager_instance: DatabaseManager | None = None


async def get_db_manager() -> DatabaseManager:
    global _db_manager_instance
    if _db_manager_instance is None:
        from src.multi_agent_system.config import Settings
        _db_manager_instance = DatabaseManager(database_url=Settings().database_url)
        await _db_manager_instance.initialize()
    return _db_manager_instance


def reset_db_manager() -> None:
    global _db_manager_instance
    _db_manager_instance = None
