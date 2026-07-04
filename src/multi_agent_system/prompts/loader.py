"""Jinja2 prompt loader.

提供两个核心函数：
- get_prompt_template(name) -> str：读取 .j2 文件原文（含 {{ ... }}）
- render_prompt(name, **vars) -> str：jinja2 渲染后返回完整 prompt

5 个 Agent 名 → .j2 文件名映射通过 PROMPT_FILES 字典维护。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Template

__all__ = [
    "PROMPT_FILES",
    "DEFAULT_PROMPTS",
    "get_prompt_template",
    "render_prompt",
]


# 5 个 Agent 主 prompt（D-02 版本管理范围）
PROMPT_FILES: dict[str, str] = {
    "intent": "intent.j2",
    "classify": "classifier.j2",
    "process": "process.j2",
    "review": "review.j2",
    "coordinator": "coordinator_suggest_decision.j2",
}


_PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def get_prompt_template(name: str) -> str:
    """读取指定 prompt 的 .j2 原文（不渲染）。

    Raises:
        KeyError: name 不在 PROMPT_FILES 中
        FileNotFoundError: .j2 文件缺失（部署时漏拷）
    """
    if name not in PROMPT_FILES:
        raise KeyError(
            f"未知 prompt name: {name}，合法值: {sorted(PROMPT_FILES.keys())}"
        )
    file_path = _PROMPTS_DIR / PROMPT_FILES[name]
    if not file_path.is_file():
        raise FileNotFoundError(f"prompt 模板文件缺失: {file_path}")
    return file_path.read_text(encoding="utf-8")


def render_prompt(name: str, **variables: object) -> str:
    """渲染指定 prompt 模板。

    Args:
        name: 5 个 Agent 之一
        **variables: jinja2 变量

    Returns:
        渲染后的 prompt 字符串

    未知变量不会报错（jinja2 默认 Undefined → 空字符串），但调用方应传全。
    """
    template_text = get_prompt_template(name)
    return Template(template_text).render(**variables)


# 启动时一次性把 5 个 .j2 原文加载到内存（seeder 用）
DEFAULT_PROMPTS: dict[str, str] = {name: get_prompt_template(name) for name in PROMPT_FILES}
