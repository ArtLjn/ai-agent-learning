"""Prompt 默认版本种子：启动时把 .j2 文件原文写入 prompt_versions 表 v1。

策略：
- 对 5 个 Agent 各检查是否已有版本记录
- 无任何记录（首次启动 / 全新库）→ 写入 DEFAULT_PROMPTS[agent] 作为 v1 + activate=true
- 已有记录 → 跳过（保留用户通过 D-02 接口创建/激活的版本）

幂等：多次调用不会重复写入（DB 唯一约束 (agent_name, version) + 应用层检查）。
"""

from __future__ import annotations

import logging
from typing import Any

from src.multi_agent_system.models.prompt_version import ALLOWED_AGENT_NAMES
from src.multi_agent_system.prompts import DEFAULT_PROMPTS

logger = logging.getLogger(__name__)

__all__ = ["seed_default_prompts"]


async def seed_default_prompts(db_manager: Any) -> dict[str, int]:
    """启动时把代码默认 prompt 作为 v1 写入 DB（仅当 DB 无任何版本时）。

    Returns:
        dict[agent_name, version]：本次写入的版本（已有则不在返回值中）
    """
    written: dict[str, int] = {}
    for agent_name in ALLOWED_AGENT_NAMES:
        try:
            existing = await db_manager.list_prompt_versions(
                agent_name=agent_name, page=1, page_size=1
            )
        except Exception as e:
            logger.warning(
                f"[PromptSeeder] 检查 {agent_name} 现有版本失败: {e}，跳过 seed"
            )
            continue

        if existing["total"] > 0:
            # 已有版本（用户/上次启动写过），不覆盖
            continue

        template = DEFAULT_PROMPTS.get(agent_name)
        if not template:
            continue

        try:
            record = await db_manager.create_prompt_version(
                agent_name=agent_name,
                template=template,
                note="代码默认初始版本（自动 seed）",
                activate=True,
            )
            written[agent_name] = record["version"]
            logger.info(
                f"[PromptSeeder] {agent_name} 写入默认 v{record['version']} 并激活"
            )
        except Exception as e:
            logger.warning(
                f"[PromptSeeder] 写入 {agent_name} 默认版本失败: {e}，跳过"
            )

    return written
