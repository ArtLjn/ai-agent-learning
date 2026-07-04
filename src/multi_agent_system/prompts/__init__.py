"""Prompt 模板包（D-02 单一数据源）。

5 个 Agent 的 system prompt 用 Jinja2 (.j2) 文件管理：
- 启动时 prompts/loader.py 读取 .j2 文件原文，作为 v1 seed 到 prompt_versions 表
- 运行时 5 个 Agent 用 jinja2.Template(...).render(**vars) 渲染
- DB 中如有 active 版本（用户改过）则覆盖代码默认

文件清单：
- classifier.j2                      ClassifierAgent（无占位符）
- intent.j2                          TicketIntentAgent（无占位符）
- process.j2                         ReActProcessorAgent（{{ tools_description }} / {{ ticket_info }} / {{ user_context }}）
- review.j2                          ReviewerAgent（{{ content }} / {{ category }} / {{ processing_result }}）
- coordinator_suggest_decision.j2    CoordinatorAgent 主决策（{{ ticket_id }} / {{ trigger_type }} 等）

不在版本管理范围的小型格式化 prompt（仍保留在 coordinator.py 内）：
- _ESCALATE_PROMPT / _FAILURE_PROMPT / _REPORT_PROMPT
"""

from src.multi_agent_system.prompts.loader import (
    DEFAULT_PROMPTS,
    PROMPT_FILES,
    get_prompt_template,
    render_prompt,
)

__all__ = [
    "DEFAULT_PROMPTS",
    "PROMPT_FILES",
    "get_prompt_template",
    "render_prompt",
]
