"""Prompt 加载器：启动时从 prompt_versions 表读取 active 模板注入 Agent。

5 个 Agent 名映射：
- intent → TicketIntentAgent
- classify → ClassifierAgent
- process → ReActProcessorAgent
- review → ReviewerAgent
- coordinator → CoordinatorAgent（仅覆盖 _SUGGEST_DECISION_PROMPT）

无 active 版本时 Agent 保留代码默认，不抛错。
"""

from __future__ import annotations

import logging
from typing import Any

from src.multi_agent_system.models.prompt_version import ALLOWED_AGENT_NAMES

logger = logging.getLogger(__name__)

__all__ = ["load_active_prompts"]


async def load_active_prompts(db_manager: Any, agents: dict[str, Any]) -> None:
    """启动时把 prompt_versions 表中的 active 模板注入到对应 Agent。

    Args:
        db_manager: DatabaseManager 实例
        agents: dict，键为 5 个 agent_name 之一，值为 Agent 实例
            （要求实现 set_prompt_override(template: str | None) 方法）

    不会抛异常：DB 错误或 Agent 缺失都跳过，保留代码默认。
    """
    for agent_name in ALLOWED_AGENT_NAMES:
        agent = agents.get(agent_name)
        if agent is None:
            continue
        try:
            active = await db_manager.get_active_prompt(agent_name)
        except Exception as e:
            logger.warning(
                f"[PromptLoader] 读取 {agent_name} active prompt 失败: {e}，保留代码默认"
            )
            continue
        if active is None:
            # 无激活版本：保留代码默认（不调用 set_prompt_override）
            continue
        template = active.get("template")
        if not template:
            continue
        try:
            agent.set_prompt_override(template)
            logger.info(
                f"[PromptLoader] {agent_name} 已加载 active prompt v{active.get('version')}"
            )
        except Exception as e:
            logger.warning(
                f"[PromptLoader] 注入 {agent_name} prompt 失败: {e}，保留代码默认"
            )
