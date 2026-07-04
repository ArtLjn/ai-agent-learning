"""D-02 prompt_seeder 测试。

覆盖：
- 全新 DB：seed_default_prompts 写入 5 个 Agent 各 1 行 v1 + is_active=true
- 已有版本：seed 跳过（不重复写入）
- 部分有版本：只 seed 缺失的 Agent
- 写入的 v1 template 与 DEFAULT_PROMPTS 一致
- 多次调用幂等
"""

import pytest

from src.multi_agent_system.core.database import DatabaseManager
from src.multi_agent_system.core.prompt_seeder import seed_default_prompts
from src.multi_agent_system.models.prompt_version import ALLOWED_AGENT_NAMES
from src.multi_agent_system.prompts import DEFAULT_PROMPTS


# ============================================================
# 主流程：全新 DB seed 5 个 Agent
# ============================================================


@pytest.mark.asyncio
async def test_seed_writes_v1_for_all_5_agents(db_manager: DatabaseManager) -> None:
    """全新 DB：seed 后 5 个 Agent 各 1 行 v1 + is_active=true。"""
    written = await seed_default_prompts(db_manager)
    assert set(written.keys()) == set(ALLOWED_AGENT_NAMES)
    assert all(v == 1 for v in written.values())

    for agent_name in ALLOWED_AGENT_NAMES:
        active = await db_manager.get_active_prompt(agent_name)
        assert active is not None, f"{agent_name} 应有 active 版本"
        assert active["version"] == 1
        assert active["is_active"] is True
        assert active["template"] == DEFAULT_PROMPTS[agent_name]


@pytest.mark.asyncio
async def test_seed_skips_when_versions_exist(db_manager: DatabaseManager) -> None:
    """已有版本时 seed 跳过：先 seed → 再 seed → 第二次返回空 dict。"""
    await seed_default_prompts(db_manager)
    written_again = await seed_default_prompts(db_manager)
    assert written_again == {}


@pytest.mark.asyncio
async def test_seed_only_fills_missing_agents(db_manager: DatabaseManager) -> None:
    """部分 Agent 已有版本时，只 seed 缺失的。"""
    # 手动给 classify 写一个版本
    await db_manager.create_prompt_version(
        agent_name="classify", template="custom", activate=True
    )

    written = await seed_default_prompts(db_manager)
    # classify 已存在，不 seed；其余 4 个 seed
    assert "classify" not in written
    assert set(written.keys()) == {"intent", "process", "review", "coordinator"}
    assert all(v == 1 for v in written.values())

    # classify 保留自定义 template（未被覆盖）
    active = await db_manager.get_active_prompt("classify")
    assert active["template"] == "custom"


@pytest.mark.asyncio
async def test_seed_idempotent_after_user_creates_v2(
    db_manager: DatabaseManager,
) -> None:
    """用户创建 v2 后再启动（再调 seed）不会把 v2 重置或写入 v1。"""
    await seed_default_prompts(db_manager)
    await db_manager.create_prompt_version(
        agent_name="review", template="user v2", activate=True
    )

    written = await seed_default_prompts(db_manager)
    assert written == {}

    active = await db_manager.get_active_prompt("review")
    assert active["version"] == 2
    assert active["template"] == "user v2"


# ============================================================
# .j2 加载与 jinja2 渲染
# ============================================================


def test_default_prompts_dict_matches_5_agents() -> None:
    """DEFAULT_PROMPTS 必须覆盖 5 个 Agent 且非空。"""
    assert set(DEFAULT_PROMPTS.keys()) == set(ALLOWED_AGENT_NAMES)
    for name, template in DEFAULT_PROMPTS.items():
        assert isinstance(template, str)
        assert len(template) > 0, f"{name} prompt 为空"


def test_process_prompt_renders_with_jinja_placeholders() -> None:
    """process.j2 含 {{ tools_description }} 等占位符，jinja2 渲染后正确替换。"""
    from src.multi_agent_system.prompts import render_prompt

    rendered = render_prompt(
        "process",
        tools_description="- tool_a: 工具 A",
        ticket_info="内容: 测试",
        user_context="无",
    )
    assert "- tool_a: 工具 A" in rendered
    assert "内容: 测试" in rendered
    # 未渲染的占位符不应残留
    assert "{{ tools_description }}" not in rendered
    assert "{{ ticket_info }}" not in rendered


def test_review_prompt_renders_correctly() -> None:
    """review.j2 的 3 个占位符都能渲染。"""
    from src.multi_agent_system.prompts import render_prompt

    rendered = render_prompt(
        "review",
        content="工单内容 X",
        category="technical",
        processing_result="处理结果 Y",
    )
    assert "工单内容 X" in rendered
    assert "处理结果 Y" in rendered
    assert "{{ content }}" not in rendered


def test_classifier_prompt_no_placeholders() -> None:
    """classifier.j2 无占位符；jinja2 渲染后内容等价（允许末尾换行差异）。"""
    from src.multi_agent_system.prompts import get_prompt_template, render_prompt

    template = get_prompt_template("classify")
    rendered = render_prompt("classify")
    # jinja2 默认 keep_trailing_newline=False 会去掉末尾换行，比较时 strip
    assert rendered.strip() == template.strip()
    # 不应残留任何 jinja2 占位符
    assert "{{" not in rendered
    assert "{%" not in rendered
