"""ReAct 模式 ProcessorAgent：多步推理 + 动态工具调用。"""

import json
import re
from typing import TYPE_CHECKING, Any

from loguru import logger
from openai import APIConnectionError, APIError, AuthenticationError, RateLimitError

from src.multi_agent_system.config import Settings
from src.multi_agent_system.core import CachedLLMClient, fallback_registry, track_agent_execution, with_retry
from src.multi_agent_system.core.context_manager import ContextManager
from src.multi_agent_system.core.exceptions import NonRetryableError, RetryableError
from src.multi_agent_system.core.json_parser import parse_json_response
from src.multi_agent_system.core.memory import MemoryManager
from src.multi_agent_system.core.trace import current_trace_id
from src.multi_agent_system.tools.rag_client import RagChunk, RagClient, RagServiceUnavailable

if TYPE_CHECKING:
    from src.multi_agent_system.core.tool_base import ToolRegistry

__all__ = ["ReActProcessorAgent"]

# D-02：从 prompts/process.j2 加载（含 jinja2 占位符；DB active 版本可覆盖）
from jinja2 import Template

from src.multi_agent_system.prompts import get_prompt_template

_REACT_SYSTEM_PROMPT = get_prompt_template("process")

_QUERY_NORMALIZATION_RULES: tuple[tuple[str, str], ...] = (
    ("优惠卷", "优惠券"),
)
_FINAL_ANSWER_KEYS = ("Final Answer", "final_answer", "finalAnswer")
_THOUGHT_KEYS = ("Thought", "thought")
_TEXT_FINAL_ANSWER_RE = re.compile(
    r"(?:^|\n)\s*Final Answer\s*[:：]\s*(?P<answer>.+)",
    re.IGNORECASE | re.DOTALL,
)
_JSON_FINAL_ANSWER_RE = re.compile(
    r'["\'](?:Final Answer|final_answer|finalAnswer)["\']\s*:\s*"(?P<answer>(?:\\.|[^"\\])*)"',
    re.DOTALL,
)


class ReActProcessorAgent:
    """ReAct 模式工单处理 Agent：多步推理 + 动态工具调用。

    通过 Thought-Action-Observation 循环处理复杂工单，
    支持查知识库、查用户历史、查用户信息等多种工具。

    Args:
        model: 模型名称
        tool_registry: 工具注册表
        knowledge_tool: 知识库检索工具（兼容旧接口）
        api_key: API 密钥
        base_url: API 基础地址
        max_iterations: ReAct 最大迭代次数，默认 10
    """

    def __init__(
        self,
        model: str,
        tool_registry: "ToolRegistry | None" = None,
        knowledge_tool: Any = None,  # backward compat
        api_key: str | None = None,
        base_url: str | None = None,
        task_type: str = "process",
        max_iterations: int = 10,
        client: CachedLLMClient | None = None,
        rag_client: RagClient | None = None,
    ) -> None:
        self._model = model
        self._tool_registry = tool_registry
        self._knowledge_tool = knowledge_tool
        self._api_key = api_key
        self._base_url = base_url
        self._task_type = task_type
        self._max_iterations = max_iterations
        self._client: CachedLLMClient | None = client
        self._context_manager = ContextManager()
        # v2.0：优先用 rag-service（HTTP 客户端），失败时降级到 KnowledgeSearchTool
        self._rag_client = rag_client
        # D-02：DB 覆盖的 prompt 模板（None 表示用代码默认 _REACT_SYSTEM_PROMPT）
        self._prompt_override: str | None = None

    def set_prompt_override(self, template: str | None) -> None:
        """注入 DB 中的 active prompt 模板；传 None 还原代码默认。

        注意：ReAct prompt 含 {tools_description}/{ticket_info}/{user_context}
        占位符，覆盖时需保留这些占位符以避免 .format() 失败。
        """
        self._prompt_override = template

    @property
    def client(self) -> CachedLLMClient:
        """延迟初始化带缓存的 LLM 客户端。"""
        if self._client is None:
            settings = Settings()
            self._client = CachedLLMClient(
                api_key=self._api_key or settings.llm_api_key,
                base_url=self._base_url or settings.llm_base_url,
                model=self._model,
            )
        return self._client

    @track_agent_execution("processor")
    async def process(
        self,
        content: str,
        category: str,
        priority: str,
        context: str = "",
        user_id: str | None = None,
        memory: MemoryManager | None = None,
    ) -> dict:
        """处理工单，生成解决方案（ReAct 循环）。

        保持与原始 ProcessorAgent 的接口兼容。

        Args:
            content: 工单内容文本
            category: 工单分类
            priority: 优先级
            context: 额外上下文信息
            user_id: 用户 ID（用于加载长期记忆）
            memory: 记忆管理器（用于记录 ReAct 步骤）

        Returns:
            包含 result 和 references 的字典
        """
        return await self._process_by_react(
            content, category, priority, context, user_id, memory
        )

    @with_retry(
        max_retries=3,
        backoff_base=2.0,
        retryable_exceptions=(APIError, APIConnectionError, RateLimitError, RetryableError),
        fallback=lambda self, content, category, priority, context="", user_id=None, memory=None: self._fallback_with_knowledge(
            content, category, priority
        ),
    )
    async def _process_by_react(
        self,
        content: str,
        category: str,
        priority: str,
        context: str = "",
        user_id: str | None = None,
        memory: MemoryManager | None = None,
    ) -> dict:
        """通过 ReAct 循环处理工单。"""
        references: list[str] = []
        knowledge_context = await self._prefetch_knowledge(content, category)
        if knowledge_context:
            references.append(knowledge_context)
            context = f"{context}\n知识库预检索结果:\n{knowledge_context}".strip()

        # Build ticket info
        ticket_info = f"内容: {content}\n分类: {category}\n优先级: {priority}"
        if context:
            ticket_info += f"\n附加上下文: {context}"

        # Load user context
        user_context_str = "无"
        if memory and user_id:
            user_ctx = await memory.load_user_context(user_id)
            if user_ctx:
                user_context_str = self._context_manager.build_system_context(
                    {"ticket_id": "", "category": category, "priority": priority},
                    user_ctx,
                )

        # Build tools description
        tools_description = "无可用工具"
        if self._tool_registry:
            schemas = self._tool_registry.get_schemas()
            if schemas:
                parts = []
                for s in schemas:
                    params = s["parameters"]["properties"]
                    param_desc = ", ".join(f"{k}({v.get('type', 'any')})" for k, v in params.items())
                    parts.append(f"- {s['name']}: {s['description']} 参数: {param_desc}")
                tools_description = "\n".join(parts)

        system_prompt = Template(
            self._prompt_override if self._prompt_override else _REACT_SYSTEM_PROMPT
        ).render(
            tools_description=tools_description,
            ticket_info=ticket_info,
            user_context=user_context_str,
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请处理以下工单：\n{content}"},
        ]

        # ReAct loop
        no_action_count = 0
        last_no_action_signature = ""
        for iteration in range(self._max_iterations):
            logger.info(f"[ReAct] Iteration {iteration + 1}/{self._max_iterations}")

            # 为每轮迭代创建 span（如有活跃 trace）
            iter_span = self._get_react_iter_span(iteration)

            async with iter_span:
                # Trim context before each call
                messages = self._context_manager.trim_messages(messages)

                try:
                    response = await self.client.chat_completions_create(
                        messages=messages,
                        temperature=0.3,
                        task_type=self._task_type,
                    )
                except AuthenticationError as e:
                    raise NonRetryableError(f"API 认证失败: {e}", cause=e)
                except (APIError, APIConnectionError, RateLimitError) as e:
                    raise RetryableError(f"API 调用失败: {e}", cause=e)

                raw = response.choices[0].message.content or ""
                logger.info(f"[ReAct] LLM response: {raw[:200]}...")

                parsed_json: dict[str, Any] | None = None

                # Try to parse JSON/markdown-code-block result (backward compat)
                try:
                    parsed = parse_json_response(raw)
                    if isinstance(parsed, dict):
                        parsed_json = parsed
                    if parsed_json and "result" in parsed_json:
                        iter_span.set_output({
                            "raw_response": raw,
                            "json_result": parsed_json.get("result", ""),
                        })
                        merged_references = self._merge_references(
                            references,
                            parsed_json.get("references", []),
                        )
                        return {
                            "result": parsed_json.get("result", ""),
                            "references": merged_references,
                        }
                except json.JSONDecodeError:
                    pass

                final_answer = self._extract_final_answer(raw, parsed_json)
                if final_answer:
                    thought = self._extract_thought(raw, parsed_json)

                    if memory:
                        memory.add_thought(f"Completed in {iteration + 1} iterations", iteration)

                    iter_span.set_output({
                        "thought": thought,
                        "raw_response": raw,
                        "final_answer": final_answer,
                        "iterations": iteration + 1,
                    })
                    return {
                        "result": final_answer,
                        "references": references,
                    }

                # Parse Thought and Action
                thought = self._extract_thought(raw, parsed_json)
                action = self._extract_action(raw, parsed_json)

                if memory:
                    memory.add_thought(thought or f"Iteration {iteration + 1}", iteration)

                if action:
                    no_action_count = 0
                    last_no_action_signature = ""
                    tool_name = action.get("tool", "")
                    params = action.get("params", {})
                    observation = ""

                    if memory:
                        memory.add_action(tool_name, params, iteration)

                    # 工具调用 span
                    tool_span = self._get_tool_span(tool_name, params)
                    async with tool_span:
                        observation = await self._execute_tool(tool_name, params)
                        tool_span.set_output({
                            "observation": str(observation),
                            "observation_length": len(str(observation)),
                        })
                    if tool_name == "search_knowledge" and observation:
                        references.append(str(observation))

                    if memory:
                        memory.add_observation(str(observation), iteration)

                    # Add to conversation
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": f"Observation: {observation}",
                    })
                    iter_span.set_output({
                        "thought": thought,
                        "action": action,
                        "observation": str(observation),
                        "raw_response": raw,
                    })
                else:
                    no_action_count += 1
                    signature = self._response_signature(raw)
                    is_repeated = bool(signature and signature == last_no_action_signature)
                    last_no_action_signature = signature

                    if no_action_count >= 2 or is_repeated:
                        answer = self._build_convergence_answer(
                            category,
                            priority,
                            references,
                            thought,
                        )
                        iter_span.set_output({
                            "thought": thought,
                            "raw_response": raw,
                            "observation": "连续未识别到工具调用或最终答案，已触发收敛兜底。",
                            "final_answer": answer,
                            "iterations": iteration + 1,
                            "converged": True,
                        })
                        logger.warning(
                            f"[ReAct] Converged after {iteration + 1} no-action iterations"
                        )
                        return {
                            "result": answer,
                            "references": references,
                        }

                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": "Observation: 未识别到工具调用，请继续思考或直接给出 Final Answer。",
                    })
                    iter_span.set_output({
                        "thought": thought,
                        "raw_response": raw,
                        "observation": "未识别到工具调用，请继续思考或直接给出 Final Answer。",
                    })

                iter_span.set_metadata({"thought": thought, "has_action": action is not None})

        # Max iterations reached
        logger.warning(f"[ReAct] Max iterations ({self._max_iterations}) reached")
        return {
            "result": "问题较复杂，已尝试多次推理仍未解决，建议升级至人工处理。",
            "references": references,
        }

    async def _prefetch_knowledge(self, content: str, category: str) -> str:
        """处理类工单先做一次知识库检索，保证 RAG 稳定进入上下文。

        v2.0 路径优先级：
        1. RagClient（HTTP 调用 rag-service /retrieve + /rerank）
        2. KnowledgeSearchTool（admin 上传仍走它写 Qdrant；同时 ReAct 兜底）
        3. 都不可用时返回空字符串（无知识增强）
        """
        query = self._normalize_knowledge_query(content)

        # 路径 1：rag-service HTTP 客户端
        if self._rag_client is not None:
            result = await self._prefetch_via_rag_client(query)
            if result:
                return result
            # RagClient 失败且未抛错时（fallback_enabled=False 时返回空）继续走 fallback

        # 路径 2：兼容旧 KnowledgeSearchTool（admin 上传走它）
        if self._tool_registry and "search_knowledge" in self._tool_registry:
            settings = Settings()
            return await self._execute_tool(
                "search_knowledge",
                {
                    "query": query,
                    "top_k": settings.qdrant_top_k,
                    "score_threshold": settings.qdrant_score_threshold,
                },
            )

        return ""

    async def _prefetch_via_rag_client(self, query: str) -> str:
        """通过 RagClient 调 rag-service 检索 + 重排，并写 rag_stats 到 trace span。

        失败时（RagServiceUnavailable）返回空字符串，让上层降级到 search_knowledge。
        """
        settings = Settings()
        collection = settings.rag_service_collection or "default"
        retrieve_top_k = max(settings.qdrant_top_k, 5)
        rerank_top_k = settings.qdrant_top_k

        span = self._get_tool_span("knowledge_search", {"query": query, "via": "rag_client"})
        async with span:
            try:
                chunks, debug = await self._rag_client.retrieve(
                    query=query,
                    mode="hybrid",
                    top_k=retrieve_top_k,
                    collection=collection,
                )
                rag_service_reachable = True
                actual_mode = debug.get("actual_mode", "hybrid")
            except RagServiceUnavailable as e:
                logger.warning(f"[ReAct] RagClient retrieve failed, degrading: {e}")
                span.set_status("fallback")
                span.set_metadata({"rag_stats": {
                    "hit_count": 0,
                    "top_score": 0.0,
                    "retrieval_mode": "hybrid",
                    "rag_service_reachable": False,
                    "error": str(e),
                }})
                return ""

            retrieve_hit_count = len(chunks)
            retrieve_top_score = chunks[0].score if chunks else 0.0
            rerank_error = ""
            if chunks:
                try:
                    chunks = await self._rag_client.rerank(
                        query=query,
                        chunks=chunks,
                        top_k=rerank_top_k,
                    )
                except RagServiceUnavailable as e:
                    logger.warning(f"[ReAct] RagClient rerank failed, degrading: {e}")
                    chunks = []
                    rerank_error = str(e)

            top_score = chunks[0].score if chunks else 0.0
            retrieved_docs = [
                {
                    "title": (c.metadata or {}).get("title") or "未命名文档",
                    "category": (c.metadata or {}).get("category") or "未分类",
                    "score": round(c.score, 4),
                    "preview": (c.content or "")[:160],
                }
                for c in chunks
            ]
            span.set_metadata({"rag_stats": {
                "hit_count": len(chunks),
                "top_score": round(top_score, 4),
                "retrieval_mode": actual_mode,
                "rag_service_reachable": rag_service_reachable,
                "retrieve_hit_count": retrieve_hit_count,
                "retrieve_top_score": round(retrieve_top_score, 4),
                "rerank_applied": bool(retrieve_hit_count and not rerank_error),
                "rerank_hit_count": len(chunks),
                "rerank_top_k": rerank_top_k,
                "rerank_error": rerank_error,
                "degraded": bool(debug.get("warning") or rerank_error),
                "warning": debug.get("warning"),
                "query": query,
                "retrieved_docs": retrieved_docs,
            }})
            span.set_output({
                "hit_count": len(chunks),
                "retrieved_docs": retrieved_docs,
            })

            if not chunks:
                return ""
            return self._format_rag_chunks(query, chunks)

    @staticmethod
    def _format_rag_chunks(query: str, chunks: list[RagChunk]) -> str:
        """把 RagClient 返回的片段格式化为 LLM 可读上下文。"""
        lines = ["检索到以下知识片段："]
        for index, chunk in enumerate(chunks, start=1):
            metadata = chunk.metadata or {}
            title = metadata.get("title") or "未命名文档"
            category = metadata.get("category") or "未分类"
            score = chunk.score
            content = chunk.content
            lines.append(
                f"{index}. 标题: {title}；分类: {category}；"
                f"相似度: {score:.2f}\n内容: {content}"
            )
        return "\n".join(lines)

    async def _fallback_with_knowledge(
        self,
        content: str,
        category: str,
        priority: str,
    ) -> dict:
        """处理模型不可用时，优先用知识库检索结果生成基础答复。"""
        knowledge_context = await self._prefetch_knowledge(content, category)
        if self._is_valid_reference(knowledge_context):
            return {
                "result": self._build_related_knowledge_guidance(knowledge_context),
                "references": [knowledge_context],
            }
        return await fallback_registry.execute(
            "processor.generate_solution", content, category, priority
        )

    def _normalize_knowledge_query(self, content: str) -> str:
        """规范化检索 query，修正常见业务词错别字。"""
        normalized = content
        for source, target in _QUERY_NORMALIZATION_RULES:
            normalized = normalized.replace(source, target)
        return normalized

    def _merge_references(self, *groups: object) -> list[str]:
        """合并工具和模型返回的引用，保留顺序并去重。"""
        merged: list[str] = []
        seen: set[str] = set()
        for group in groups:
            if not isinstance(group, list):
                continue
            for item in group:
                value = str(item)
                if value and value not in seen:
                    merged.append(value)
                    seen.add(value)
        return merged

    def _extract_final_answer(self, text: str, parsed: dict[str, Any] | None = None) -> str:
        """从严格 JSON、半结构化 JSON 或 ReAct 文本中提取最终答案。"""
        if parsed:
            final_answer = self._extract_json_final_answer(parsed)
            if final_answer:
                return final_answer

        json_like_match = _JSON_FINAL_ANSWER_RE.search(text)
        if json_like_match:
            return self._decode_json_string_value(json_like_match.group("answer"))

        text_match = _TEXT_FINAL_ANSWER_RE.search(text)
        if text_match:
            return text_match.group("answer").strip().strip('"\'')

        return ""

    def _extract_json_final_answer(self, parsed: dict[str, Any]) -> str:
        """兼容模型把 ReAct 结果包进 JSON 字段的情况。"""
        for key in _FINAL_ANSWER_KEYS:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _decode_json_string_value(self, value: str) -> str:
        """解码从半结构化 JSON 字段中截取的字符串值。"""
        try:
            decoded = json.loads(f'"{value}"')
        except json.JSONDecodeError:
            decoded = (
                value.replace(r"\\", "\\")
                .replace(r"\"", '"')
                .replace(r"\n", "\n")
                .replace(r"\t", "\t")
            )
        return str(decoded).strip()

    def _extract_thought(self, text: str, parsed: dict[str, Any] | None = None) -> str:
        """从响应中提取 Thought。"""
        if parsed:
            for key in _THOUGHT_KEYS:
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        thought = self._extract_json_string_field(text, _THOUGHT_KEYS)
        if thought:
            return thought
        match = re.search(r"Thought:\s*(.+?)(?=\nAction:|\nFinal Answer:|$)", text, re.DOTALL)
        return match.group(1).strip() if match else ""

    def _extract_json_string_field(self, text: str, keys: tuple[str, ...]) -> str:
        """从非严格 JSON 文本中提取简单字符串字段。"""
        key_pattern = "|".join(re.escape(key) for key in keys)
        match = re.search(
            rf'["\'](?:{key_pattern})["\']\s*:\s*"(?P<value>(?:\\.|[^"\\])*)"',
            text,
            re.DOTALL,
        )
        if not match:
            return ""
        return self._decode_json_string_value(match.group("value"))

    def _response_signature(self, text: str) -> str:
        """生成响应签名，用于识别重复空转。"""
        normalized = re.sub(r"\s+", "", text)
        return normalized[:500]

    def _build_convergence_answer(
        self,
        category: str,
        priority: str,
        references: list[str],
        thought: str,
    ) -> str:
        """连续无动作时基于已有上下文生成兜底答复，避免 ReAct 空转。"""
        valid_references = [
            reference for reference in references
            if self._is_valid_reference(reference)
        ]
        if valid_references:
            return self._build_related_knowledge_guidance(valid_references[0])

        if thought:
            return (
                "您好，已收到您的工单。系统已完成初步分析："
                f"{thought}\n\n"
                "请补充具体操作路径、异常截图或相关账号信息，便于继续定位处理。"
            )

        return (
            "您好，已收到您的工单。"
            f"当前分类为 {category}，优先级为 {priority}。"
            "请补充具体现象、操作路径和截图，我们会据此继续核查处理。"
        )

    def _build_related_knowledge_guidance(self, reference: str) -> str:
        """基于相关知识命中生成可展示答复，避免把部分命中误判为完全未知。"""
        reference_text = self._build_user_visible_reference_summary(reference)
        return (
            "您好，已根据现有资料整理出一组可先核对的方向。"
            "目前还缺少可直接确认最终答案的具体业务细则，可以先按以下方向核对：\n\n"
            f"{reference_text}\n\n"
            "1. 确认产品或平台、应用类型、账号权限与本次咨询对象是否一致。\n"
            "2. 对接或配置类问题，优先核对 Key/Secret、应用标识、白名单、服务开通状态和接口返回码。\n"
            "3. 流程或规则类问题，优先核对适用账号范围、入口路径、审批要求和最新业务规则。\n\n"
            "如仍无法确认，请补充具体平台入口、账号权限、截图或内部规则说明，我们会继续核对。"
        )

    def _compact_reference(self, reference: str, max_length: int = 800) -> str:
        """压缩知识库引用，避免把过长检索上下文原样塞进答复。"""
        compacted = re.sub(r"\s+", " ", reference).strip()
        if len(compacted) <= max_length:
            return compacted
        return f"{compacted[:max_length].rstrip()}..."

    def _build_user_visible_reference_summary(self, reference: str, max_length: int = 420) -> str:
        """把检索上下文改写成用户可读摘要，不暴露相似度、标题、分类等内部字段。"""
        text = self._compact_reference(reference, max_length=1600)
        text = re.sub(r"检索到以下知识片段[:：]?", "", text)
        text = re.sub(r"\b\d+\.\s*标题:\s*[^；。]+；\s*分类:\s*[^；。]+；\s*相似度:\s*\d+(?:\.\d+)?\s*内容:\s*", "\n", text)
        text = re.sub(r"标题:\s*[^；。]+；\s*分类:\s*[^；。]+；\s*相似度:\s*\d+(?:\.\d+)?\s*内容:\s*", "", text)
        text = re.sub(r"相似度:\s*\d+(?:\.\d+)?", "", text)
        text = re.sub(r"\s+", " ", text).strip(" ；。")
        if not text:
            text = "可参考现有资料中的流程、配置项和规则说明。"
        if len(text) > max_length:
            text = f"{text[:max_length].rstrip()}..."
        return f"可参考的资料要点：{text}"

    def _is_valid_reference(self, reference: object) -> bool:
        """判断引用是否是真实知识命中，而不是空结果提示。"""
        text = str(reference or "").strip()
        return bool(text and "未检索到相关知识片段" not in text)

    def _extract_action(
        self,
        text: str,
        parsed: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """从响应中提取 Action JSON。"""
        if parsed:
            for key in ("Action", "action"):
                action = parsed.get(key)
                if isinstance(action, dict):
                    tool_name = action.get("tool") or action.get("name")
                    params = action.get("params") or action.get("arguments") or {}
                    if isinstance(tool_name, str) and isinstance(params, dict):
                        return {"tool": tool_name, "params": params}

        # Try JSON format first
        json_match = re.search(r"Action:\s*(\{.+?\})", text, re.DOTALL)
        if json_match:
            try:
                return parse_json_response(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try legacy format: Action: tool_name(params_json)
        legacy_match = re.search(r"Action:\s*(\w+)\((.*?)\)", text, re.DOTALL)
        if legacy_match:
            tool_name = legacy_match.group(1)
            params_str = legacy_match.group(2).strip().strip('"\'')
            try:
                params = parse_json_response(params_str)
                if isinstance(params, dict):
                    return {"tool": tool_name, "params": params}
            except json.JSONDecodeError:
                pass
            return {"tool": tool_name, "params": {"query": params_str}}

        return None

    async def _execute_tool(self, tool_name: str, params: dict[str, Any]) -> str:
        """执行工具调用，含校验和降级。"""
        if not self._tool_registry or tool_name not in self._tool_registry:
            return f"错误: 工具 '{tool_name}' 未注册"

        tool = self._tool_registry.get(tool_name)
        assert tool is not None

        # Validate params
        try:
            validated = tool.validate_params(params)
        except Exception as e:
            error_msg = tool.format_validation_error(e) if hasattr(e, "errors") else str(e)
            return f"参数错误: {error_msg}"

        # Execute
        try:
            result = await tool.execute(**validated.model_dump())
            return str(result)
        except Exception as e:
            logger.warning(f"[ReAct] Tool {tool_name} failed: {e}, trying fallback")
            try:
                fallback_result = await tool.fallback(**validated.model_dump())
                return str(fallback_result)
            except Exception as fb_e:
                return f"工具执行失败: {e}; 降级也失败: {fb_e}"

    def _get_react_iter_span(self, iteration: int):
        """获取 ReAct 迭代 span context manager。"""
        if current_trace_id.get() is None:
            return _NoOpSpan()
        from src.multi_agent_system.workflow.graph import _trace_manager
        if _trace_manager is None:
            return _NoOpSpan()
        return _trace_manager.start_span(
            f"react_iter_{iteration + 1}",
            "react_iter",
            input_data={"iteration": iteration + 1},
        )

    def _get_tool_span(self, tool_name: str, params: dict):
        """获取工具调用 span context manager。"""
        from src.multi_agent_system.workflow.graph import _trace_manager
        if _trace_manager is None:
            return _NoOpSpan()
        return _trace_manager.start_span(
            tool_name,
            "tool_call",
            input_data={"tool": tool_name, "params": params},
        )

    @staticmethod
    def _fallback_process(content: str, category: str, priority: str) -> dict:
        """LLM 调用失败时的降级处理方案。

        Args:
            content: 工单内容
            category: 工单分类
            priority: 优先级

        Returns:
            降级处理结果字典
        """
        from src.multi_agent_system.models.ticket import TicketCategory

        result_map = {
            TicketCategory.TECHNICAL.value: f"已排查技术问题，生成解决方案（优先级: {priority}）",
            TicketCategory.BILLING.value: f"已核实账单信息，生成处理方案（优先级: {priority}）",
        }
        result = result_map.get(
            category, f"已处理工单（分类: {category}, 优先级: {priority}）"
        )

        return {
            "result": result,
            "references": [],
        }

    @staticmethod
    def create_from_settings(
        tool_registry: "ToolRegistry | None" = None,
        knowledge_tool: Any = None,
        rag_client: RagClient | None = None,
    ) -> "ReActProcessorAgent":
        """从 Settings 创建 ReActProcessorAgent 实例。"""
        settings = Settings()
        return ReActProcessorAgent(
            model=settings.llm_model,
            tool_registry=tool_registry,
            knowledge_tool=knowledge_tool,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            max_iterations=settings.max_react_iterations,
            rag_client=rag_client,
        )


class _NoOpSpan:
    """无 trace 时的空操作 span。"""

    span_id = ""
    trace_id = ""

    def set_output(self, data):  # noqa: ANN001, ANN202
        pass

    def set_metadata(self, data):  # noqa: ANN001, ANN202
        pass

    def set_status(self, status):  # noqa: ANN001, ANN202
        pass

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *args):  # noqa: ANN002, ANN204
        return False


# 模块级降级注册
fallback_registry.register("processor.generate_solution", ReActProcessorAgent._fallback_process)
