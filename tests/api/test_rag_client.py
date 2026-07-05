"""RagClient HTTP 客户端单元测试。

mock httpx 调用，验证：
- /retrieve 正常返回 RagChunk 列表
- 网络异常 / 5xx → RagServiceUnavailable
- /rerank 重排逻辑
- /health 探活
- ReActProcessorAgent 在 RagClient 失败时走降级（无知识增强）路径
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.multi_agent_system.agents.processor_react import ReActProcessorAgent
from src.multi_agent_system.tools.rag_client import (
    RagChunk,
    RagClient,
    RagServiceUnavailable,
)


def _mock_response(
    status_code: int = 200,
    json_data: dict | None = None,
) -> MagicMock:
    """构造模拟的 httpx.Response。"""
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = json_data or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    else:
        response.raise_for_status.return_value = None
    return response


def _build_retrieve_response() -> dict:
    """构造 rag-service /retrieve 标准响应（对齐 retrieve.py 的 ApiResponse）。"""
    return {
        "code": "OK",
        "message": "ok",
        "data": {
            "results": [
                {
                    "content": "工单处理标准流程：1. 接收 2. 分类 3. 处理 4. 审核",
                    "score": 0.85,
                    "doc_id": "doc-001",
                    "chunk_index": 0,
                    "metadata": {
                        "doc_id": "doc-001",
                        "title": "工单处理手册",
                        "category": "technical",
                        "source": "manual.pdf",
                    },
                },
                {
                    "content": "技术类工单需要先排查登录、网络、配置问题",
                    "score": 0.72,
                    "doc_id": "doc-001",
                    "chunk_index": 2,
                    "metadata": {
                        "doc_id": "doc-001",
                        "title": "工单处理手册",
                        "category": "technical",
                    },
                },
            ],
            "actual_mode": "hybrid",
            "query_vector_dim": 1024,
        },
        "warning": None,
    }


def _build_rerank_response() -> dict:
    """构造 rag-service /rerank 标准响应。"""
    return {
        "code": "OK",
        "message": "ok",
        "data": {
            "results": [
                {
                    "content": "技术类工单需要先排查登录、网络、配置问题",
                    "score": 0.92,
                    "original_index": 1,
                    "metadata": {
                        "doc_id": "doc-001",
                        "title": "工单处理手册",
                        "category": "technical",
                    },
                },
                {
                    "content": "工单处理标准流程：1. 接收 2. 分类 3. 处理 4. 审核",
                    "score": 0.88,
                    "original_index": 0,
                    "metadata": {
                        "doc_id": "doc-001",
                        "title": "工单处理手册",
                        "category": "technical",
                    },
                },
            ]
        },
        "warning": None,
    }


class TestRagClientRetrieve:
    """/retrieve 调用测试。"""

    @pytest.mark.asyncio
    async def test_retrieve_returns_chunks_and_debug_info(self) -> None:
        """正常调用返回 RagChunk 列表 + actual_mode 等 debug 信息。"""
        client = RagClient(base_url="http://rag-service:8001")
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(200, _build_retrieve_response())
        client._client = mock_http

        chunks, debug = await client.retrieve(
            query="工单怎么处理",
            mode="hybrid",
            top_k=5,
            collection="knowledge_base",
        )

        assert len(chunks) == 2
        assert isinstance(chunks[0], RagChunk)
        assert chunks[0].content.startswith("工单处理标准流程")
        assert chunks[0].score == 0.85
        assert chunks[0].id == "doc-001"
        assert chunks[0].metadata["title"] == "工单处理手册"
        assert debug["actual_mode"] == "hybrid"
        assert debug["query_vector_dim"] == 1024
        await client.close()

    @pytest.mark.asyncio
    async def test_retrieve_5xx_raises_rag_service_unavailable(self) -> None:
        """5xx 响应直接抛 RagServiceUnavailable（不重试）。"""
        client = RagClient(base_url="http://rag-service:8001", retry=0)
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(503, {"detail": "Qdrant down"})
        client._client = mock_http

        with pytest.raises(RagServiceUnavailable, match="503"):
            await client.retrieve(query="q", collection="c")

        # 5xx 不重试，只调用一次
        assert mock_http.post.call_count == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_retrieve_network_error_retries_then_raises(self) -> None:
        """网络错误重试 retry 次后抛 RagServiceUnavailable。"""
        client = RagClient(base_url="http://rag-service:8001", retry=2)
        mock_http = AsyncMock()
        mock_http.post.side_effect = httpx.ConnectError("connection refused")
        client._client = mock_http

        with pytest.raises(RagServiceUnavailable, match="unreachable"):
            await client.retrieve(query="q", collection="c")

        # 重试 2 次 + 初次 1 次 = 3 次
        assert mock_http.post.call_count == 3
        await client.close()

    @pytest.mark.asyncio
    async def test_retrieve_failed_code_raises(self) -> None:
        """响应 code != OK 时抛 RagServiceUnavailable。"""
        client = RagClient(base_url="http://rag-service:8001", retry=0)
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(
            200,
            {"code": "FAILED", "message": "QDRANT_UNAVAILABLE", "data": None},
        )
        client._client = mock_http

        with pytest.raises(RagServiceUnavailable, match="code=FAILED"):
            await client.retrieve(query="q", collection="c")
        await client.close()


class TestRagClientRerank:
    """/rerank 调用测试。"""

    @pytest.mark.asyncio
    async def test_rerank_returns_reordered_chunks(self) -> None:
        """正常调用返回按相关性降序的 RagChunk 列表。"""
        client = RagClient(base_url="http://rag-service:8001")
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(200, _build_rerank_response())
        client._client = mock_http

        input_chunks = [
            RagChunk(content="A", score=0.5, id="a"),
            RagChunk(content="B", score=0.4, id="b"),
        ]
        reranked = await client.rerank(query="q", chunks=input_chunks, top_k=2)

        assert len(reranked) == 2
        assert reranked[0].score == 0.92
        assert reranked[0].content.startswith("技术类工单")
        await client.close()

    @pytest.mark.asyncio
    async def test_rerank_empty_input_returns_empty(self) -> None:
        """空 chunks 列表不调 /rerank，直接返回空。"""
        client = RagClient(base_url="http://rag-service:8001")
        mock_http = AsyncMock()
        client._client = mock_http

        result = await client.rerank(query="q", chunks=[], top_k=3)
        assert result == []
        mock_http.post.assert_not_called()
        await client.close()


class TestRagClientHealth:
    """/health 探活测试。"""

    @pytest.mark.asyncio
    async def test_health_ok(self) -> None:
        """/health 200 返回 ok 状态。"""
        client = RagClient(base_url="http://rag-service:8001")
        mock_http = AsyncMock()
        mock_http.get.return_value = _mock_response(
            200,
            {"status": "ok", "components": {"qdrant": "ok", "embedder": "ok"}},
        )
        client._client = mock_http

        result = await client.health()
        assert result["status"] == "ok"
        assert result["components"]["qdrant"] == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_health_unreachable_raises(self) -> None:
        """/health 网络异常抛 RagServiceUnavailable。"""
        client = RagClient(base_url="http://rag-service:8001")
        mock_http = AsyncMock()
        mock_http.get.side_effect = httpx.ConnectError("refused")
        client._client = mock_http

        with pytest.raises(RagServiceUnavailable):
            await client.health()
        await client.close()


class TestRagClientApiKeyHeader:
    """X-API-Key header 注入测试（C2 补丁：生产 rag-service 必填）。"""

    @pytest.mark.asyncio
    async def test_retrieve_includes_api_key_header_when_configured(self) -> None:
        """api_key 非空时，/retrieve 请求带 X-API-Key header。"""
        client = RagClient(
            base_url="http://rag-service:8001",
            api_key="test-secret-key-1234",
        )
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(200, _build_retrieve_response())
        client._client = mock_http

        await client.retrieve(query="q", collection="c")

        # 验证 POST 调用时传了 X-API-Key header
        assert mock_http.post.called
        _, kwargs = mock_http.post.call_args
        headers = kwargs.get("headers") or {}
        assert headers.get("X-API-Key") == "test-secret-key-1234"
        await client.close()

    @pytest.mark.asyncio
    async def test_retrieve_omits_api_key_header_when_empty(self) -> None:
        """api_key 为空时，/retrieve 请求不带 X-API-Key header（兼容本地开发）。"""
        client = RagClient(base_url="http://rag-service:8001", api_key="")
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(200, _build_retrieve_response())
        client._client = mock_http

        await client.retrieve(query="q", collection="c")

        assert mock_http.post.called
        _, kwargs = mock_http.post.call_args
        headers = kwargs.get("headers") or {}
        assert "X-API-Key" not in headers
        await client.close()


class TestRagClientIngestText:
    """/ingest text 模式测试（admin 知识库双写目标）。"""

    @pytest.mark.asyncio
    async def test_ingest_text_posts_multipart_form_with_api_key(self) -> None:
        """ingest_text 用 multipart form 提交，带 X-API-Key，字段含 text/collection/source/category。"""
        client = RagClient(
            base_url="http://rag-service:8001",
            api_key="test-secret-key",
        )
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(
            200,
            {
                "code": "OK",
                "message": "ok",
                "data": {
                    "doc_id": "abc12345",
                    "chunk_count": 4,
                    "collection": "ticket_knowledge",
                    "action": "created",
                },
            },
        )
        client._client = mock_http

        result = await client.ingest_text(
            text="# 工单排查手册\n步骤 1...",
            collection="ticket_knowledge",
            source="admin-upload:工单排查手册",
            category="technical",
        )

        # 验证返回 data 字段
        assert result["doc_id"] == "abc12345"
        assert result["chunk_count"] == 4
        assert result["collection"] == "ticket_knowledge"

        # 验证 POST 调用：data=（form 模式），json=None，带 X-API-Key
        assert mock_http.post.called
        _, kwargs = mock_http.post.call_args
        assert kwargs.get("json") is None
        form = kwargs.get("data") or {}
        assert form["text"].startswith("# 工单排查手册")
        assert form["collection"] == "ticket_knowledge"
        assert form["source"] == "admin-upload:工单排查手册"
        assert form["category"] == "technical"
        headers = kwargs.get("headers") or {}
        assert headers.get("X-API-Key") == "test-secret-key"
        await client.close()

    @pytest.mark.asyncio
    async def test_ingest_text_5xx_raises_rag_service_unavailable(self) -> None:
        """5xx 响应抛 RagServiceUnavailable。"""
        client = RagClient(base_url="http://rag-service:8001", retry=0)
        mock_http = AsyncMock()
        mock_http.post.return_value = _mock_response(503, {"detail": "Qdrant down"})
        client._client = mock_http

        with pytest.raises(RagServiceUnavailable, match="503"):
            await client.ingest_text(text="x", collection="ticket_knowledge")
        await client.close()


class TestReActProcessorDegradeOnRagFailure:
    """ReActProcessorAgent 在 RagClient 失败时降级到无知识增强路径。"""

    @pytest.mark.asyncio
    async def test_prefetch_via_rag_client_failure_returns_empty(self) -> None:
        """RagClient.retrieve 失败时，_prefetch_knowledge 返回空字符串。

        ReAct 主循环仍能继续生成 solution（无知识增强）。
        """
        rag_client = RagClient(base_url="http://rag-service:8001", retry=0)
        # mock retrieve 抛 RagServiceUnavailable
        rag_client._client = AsyncMock()
        rag_client._client.post.side_effect = httpx.ConnectError("refused")

        # mock LLM 客户端避免真实 API 调用
        mock_llm = MagicMock()
        mock_llm.chat_completions_create = AsyncMock(return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content="Final Answer: 已处理"))],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ))

        with patch("src.multi_agent_system.config.Settings") as mock_settings_cls:
            mock_settings = MagicMock()
            mock_settings.llm_api_key = "test"
            mock_settings.llm_base_url = "http://test"
            mock_settings.llm_model = "test-model"
            mock_settings.max_react_iterations = 2
            mock_settings.qdrant_top_k = 3
            mock_settings.qdrant_score_threshold = 0.5
            mock_settings.qdrant_collection = "knowledge_base"
            mock_settings_cls.return_value = mock_settings

            agent = ReActProcessorAgent(
                model="test-model",
                rag_client=rag_client,
                client=mock_llm,
            )
            # 直接调 _prefetch_knowledge，确认失败时返回空（降级路径）
            result = await agent._prefetch_knowledge("登录失败", "technical")
            assert result == ""

        await rag_client.close()
