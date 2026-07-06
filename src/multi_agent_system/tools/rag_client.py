"""rag-service HTTP 客户端封装（v2.0 主系统作为 rag-service 客户端）。

对接 rag-service 的 /retrieve + /rerank API（详见 11 号设计文档）。
所有失败情况统一抛 RagServiceUnavailable，调用方走无知识增强降级路径。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.multi_agent_system.config import Settings

__all__ = ["RagChunk", "RagClient", "RagServiceUnavailable"]


class RagServiceUnavailable(Exception):
    """rag-service 不可用（网络异常 / 5xx / 超时）。调用方应走降级路径。"""


class RagChunk(BaseModel):
    """rag-service 检索/重排返回的单条片段（对齐 rag-service RetrieveResult/RerankResult）。"""

    id: str | None = None
    content: str = ""
    score: float = 0.0
    chunk_index: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagClient:
    """rag-service HTTP 客户端：封装 /retrieve + /rerank 调用。

    Args:
        base_url: rag-service 地址（默认 http://localhost:8001）
        timeout_seconds: 单次请求超时（默认 10s）
        retry: 网络错误重试次数（默认 1 次；5xx 不重试）
        fallback_enabled: 失败时是否走降级路径（True 时 raise RagServiceUnavailable）
        api_key: rag-service API Key（默认从 Settings.rag_service_api_key 读）；
            非空时所有请求带 X-API-Key header，空字符串时不带
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        retry: int | None = None,
        fallback_enabled: bool | None = None,
        api_key: str | None = None,
    ) -> None:
        settings = Settings()
        self._base_url = (base_url or settings.rag_service_url).rstrip("/")
        self._timeout = timeout_seconds or settings.rag_service_timeout_seconds
        self._ingest_timeout = settings.rag_service_ingest_timeout_seconds
        self._retry = retry if retry is not None else settings.rag_service_retry
        self._fallback_enabled = (
            fallback_enabled
            if fallback_enabled is not None
            else settings.rag_service_fallback_enabled
        )
        self._api_key = (
            api_key if api_key is not None else settings.rag_service_api_key
        )
        self._client: httpx.AsyncClient | None = None

    def _build_headers(self) -> dict[str, str]:
        """构造请求头。api_key 非空时带 X-API-Key，否则返回空 dict。"""
        if self._api_key:
            return {"X-API-Key": self._api_key}
        return {}

    @property
    def _http(self) -> httpx.AsyncClient:
        """延迟初始化 httpx.AsyncClient。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """关闭底层 httpx 连接池。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def retrieve(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 5,
        collection: str = "default",
        filters: dict[str, Any] | None = None,
        use_hyde: bool = False,
    ) -> tuple[list[RagChunk], dict[str, Any]]:
        """调用 rag-service /retrieve，返回 (chunks, debug_info)。

        Args:
            query: 查询语句
            mode: vector | bm25 | hybrid
            top_k: 返回数量
            collection: 目标 collection
            filters: 元数据过滤（可选）
            use_hyde: 是否启用 HyDE 查询改写

        Returns:
            (chunks, debug_info) 元组。debug_info 含 actual_mode / query_vector_dim。

        Raises:
            RagServiceUnavailable: 网络异常 / 5xx / 超时
        """
        payload: dict[str, Any] = {
            "query": query,
            "collection": collection,
            "mode": mode,
            "top_k": top_k,
            "use_hyde": use_hyde,
        }
        if filters:
            payload["filters"] = filters

        data = await self._post_with_retry("/retrieve", payload)
        results = data.get("results") or []
        chunks = [self._parse_retrieve_item(item) for item in results]
        debug = {
            "actual_mode": data.get("actual_mode", mode),
            "query_vector_dim": data.get("query_vector_dim"),
            "warning": data.get("warning"),
            "retrieval_mode": mode,
        }
        logger.info(
            f"[RagClient] /retrieve mode={mode} hits={len(chunks)} "
            f"top_score={chunks[0].score if chunks else 0.0:.4f}"
        )
        return chunks, debug

    async def rerank(
        self,
        query: str,
        chunks: list[RagChunk],
        top_k: int = 3,
    ) -> list[RagChunk]:
        """调用 rag-service /rerank，对 chunks 做精排。

        Args:
            query: 原始查询
            chunks: 待重排的片段列表
            top_k: 重排后保留数量

        Returns:
            重排后的 RagChunk 列表（按相关性降序）。

        Raises:
            RagServiceUnavailable: 网络异常 / 5xx / 超时
        """
        if not chunks:
            return []

        documents = [
            {
                "content": c.content,
                "metadata": c.metadata,
                "doc_id": c.id,
                "chunk_index": c.chunk_index,
            }
            for c in chunks
        ]
        payload = {
            "query": query,
            "documents": documents,
            "top_k": top_k,
        }
        data = await self._post_with_retry("/rerank", payload)
        results = data.get("results") or []
        reranked = [self._parse_rerank_item(item) for item in results]
        logger.info(f"[RagClient] /rerank in={len(chunks)} out={len(reranked)}")
        return reranked

    async def health(self) -> dict[str, Any]:
        """调用 rag-service /health（轻量探活）。

        Returns:
            rag-service /health 响应 dict。失败时抛 RagServiceUnavailable。
        """
        try:
            response = await self._http.get(
                f"{self._base_url}/health",
                headers=self._build_headers(),
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RagServiceUnavailable(f"rag-service /health failed: {e}") from e

    async def ingest_text(
        self,
        text: str,
        collection: str,
        source: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """调用 rag-service /ingest（text 模式），写入指定 collection。

        rag-service /ingest 接收 multipart form（非 JSON），见 rag-service/app/api/ingest.py。

        Args:
            text: 文档原始文本（markdown / plain text）
            collection: 目标 collection（如 ticket_knowledge）
            source: 文档来源标识（可选）
            category: 业务类别（可选）

        Returns:
            rag-service data 字段，含 doc_id / chunk_count / collection / action。

        Raises:
            RagServiceUnavailable: 网络异常 / 5xx / 超时
        """
        form_data: dict[str, str] = {
            "text": text,
            "collection": collection,
        }
        if source:
            form_data["source"] = source
        if category:
            form_data["category"] = category
        return await self._post_with_retry("/ingest", data=form_data)

    async def ingest_file(
        self,
        file_bytes: bytes,
        filename: str,
        collection: str,
        source: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """调用 rag-service /ingest（file 模式），上传 PDF/MD/TXT 文件入库。

        Args:
            file_bytes: 文件二进制内容
            filename: 文件名（含扩展名，用于文件类型探测）
            collection: 目标 collection
            source: 文档来源标识（可选，缺省回退到 filename）
            category: 业务类别（可选）

        Returns:
            rag-service data 字段，含 doc_id / chunk_count / collection / action。

        Raises:
            RagServiceUnavailable: 网络异常 / 5xx / 超时
        """
        url = f"{self._base_url}/ingest"
        headers = self._build_headers()
        # 文件类型按扩展名映射（rag-service 也会探测，但显式传更稳）
        ext_to_type = {
            ".pdf": "application/pdf",
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".txt": "text/plain",
        }
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        content_type = ext_to_type.get(ext, "application/octet-stream")
        files = {"file": (filename, file_bytes, content_type)}
        data: dict[str, str] = {"collection": collection}
        if source:
            data["source"] = source
        if category:
            data["category"] = category

        # 文件入库可能包含 PDF 解析、远程 MinerU、Embedding 与 Qdrant 写入。
        # 一旦请求已到达服务端，客户端超时后重试会造成同一文件被重复解析/写入。
        attempts = 1
        body: dict[str, Any] | None = None
        for attempt in range(attempts):
            try:
                response = await self._http.post(
                    url,
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=self._ingest_timeout,
                )
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPStatusError as e:
                raise RagServiceUnavailable(
                    f"rag-service /ingest returned {e.response.status_code}"
                ) from e
            except (httpx.HTTPError, ValueError) as e:
                if attempt < attempts - 1:
                    logger.warning(
                        f"[RagClient] /ingest attempt {attempt + 1} failed: {e}, retrying"
                    )
                    await asyncio.sleep(0.5)
                    continue
                raise RagServiceUnavailable(
                    f"rag-service /ingest unreachable after {attempts} attempts: {e}"
                ) from e

        if body.get("code") != "OK":
            raise RagServiceUnavailable(
                f"rag-service /ingest returned code={body.get('code')} "
                f"message={body.get('message')}"
            )
        result_data = body.get("data")
        if not isinstance(result_data, dict):
            raise RagServiceUnavailable(
                f"rag-service /ingest returned non-dict data: {type(result_data).__name__}"
            )
        return result_data

    async def list_documents(
        self,
        collection: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """调用 GET /collections/{collection}/documents 拉文档元数据列表。

        Args:
            collection: 目标 collection
            page: 页码（1-based）
            page_size: 每页文档数（rag-service 限制 1-100）

        Returns:
            rag-service data 字段，含 total / page / page_size / documents[]。
            documents 各项为 DocumentRecord（doc_id/source/category/chunk_count/...）。

        Raises:
            RagServiceUnavailable: 网络异常 / 5xx / 超时
        """
        url = f"{self._base_url}/collections/{collection}/documents"
        headers = self._build_headers()
        params = {"page": page, "page_size": page_size}
        try:
            response = await self._http.get(url, params=params, headers=headers)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RagServiceUnavailable(f"rag-service list_documents failed: {e}") from e

        if body.get("code") != "OK":
            raise RagServiceUnavailable(
                f"rag-service list_documents returned code={body.get('code')} "
                f"message={body.get('message')}"
            )
        result_data = body.get("data")
        if not isinstance(result_data, dict):
            raise RagServiceUnavailable(
                f"rag-service list_documents returned non-dict data: {type(result_data).__name__}"
            )
        return result_data

    async def delete_document(
        self,
        collection: str,
        doc_id: str,
    ) -> dict[str, Any]:
        """调用 DELETE /collections/{collection}/documents/{doc_id} 删文档。

        rag-service 会同时清 Qdrant points 与 SQLite metadata。

        Args:
            collection: 目标 collection
            doc_id: 文档 ID

        Returns:
            rag-service data 字段，含 doc_id / collection / metadata_removed / points_removed。

        Raises:
            RagServiceUnavailable: 网络异常 / 5xx / 超时
        """
        url = f"{self._base_url}/collections/{collection}/documents/{doc_id}"
        headers = self._build_headers()
        try:
            response = await self._http.delete(url, headers=headers)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RagServiceUnavailable(f"rag-service delete_document failed: {e}") from e

        if body.get("code") != "OK":
            raise RagServiceUnavailable(
                f"rag-service delete_document returned code={body.get('code')} "
                f"message={body.get('message')}"
            )
        result_data = body.get("data")
        if not isinstance(result_data, dict):
            raise RagServiceUnavailable(
                f"rag-service delete_document returned non-dict data: {type(result_data).__name__}"
            )
        return result_data

    async def latest_evaluation(self) -> dict[str, Any]:
        """调用 GET /evaluation/latest 获取最新 RAG 评测报告。"""
        url = f"{self._base_url}/evaluation/latest"
        headers = self._build_headers()
        try:
            response = await self._http.get(url, headers=headers)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RagServiceUnavailable(f"rag-service latest_evaluation failed: {e}") from e

        if body.get("code") != "OK":
            raise RagServiceUnavailable(
                f"rag-service latest_evaluation returned code={body.get('code')} "
                f"message={body.get('message')}"
            )
        result_data = body.get("data")
        if not isinstance(result_data, dict):
            raise RagServiceUnavailable(
                f"rag-service latest_evaluation returned non-dict data: {type(result_data).__name__}"
            )
        return result_data

    async def run_evaluation(
        self,
        *,
        mode: str = "hybrid",
        top_k: int = 10,
        k_values: list[int] | None = None,
    ) -> dict[str, Any]:
        """调用 POST /evaluation/run 运行 RAG 召回评测。"""
        payload = {
            "mode": mode,
            "top_k": top_k,
            "k_values": k_values or [1, 3, 5, 10],
        }
        return await self._post_with_retry("/evaluation/run", payload)

    async def _post_with_retry(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST 带 retry。仅对网络错误重试；4xx/5xx 不重试。

        payload 非 None 时走 JSON 模式；data 非 None 时走 multipart form 模式。
        两者互斥，至少传一个。
        """
        url = f"{self._base_url}{path}"
        headers = self._build_headers()
        attempts = self._retry + 1
        body: dict[str, Any] | None = None
        for attempt in range(attempts):
            try:
                response = await self._http.post(
                    url, json=payload, data=data, headers=headers
                )
                response.raise_for_status()
                body = response.json()
            except httpx.HTTPStatusError as e:
                # 4xx/5xx 直接失败，不重试
                raise RagServiceUnavailable(
                    f"rag-service {path} returned {e.response.status_code}"
                ) from e
            except (httpx.HTTPError, ValueError) as e:
                if attempt < attempts - 1:
                    logger.warning(
                        f"[RagClient] {path} attempt {attempt + 1} failed: {e}, retrying"
                    )
                    await asyncio.sleep(0.5)
                    continue
                raise RagServiceUnavailable(
                    f"rag-service {path} unreachable after {attempts} attempts: {e}"
                ) from e

        if body.get("code") != "OK":
            raise RagServiceUnavailable(
                f"rag-service {path} returned code={body.get('code')} "
                f"message={body.get('message')}"
            )
        result_data = body.get("data")
        if not isinstance(result_data, dict):
            raise RagServiceUnavailable(
                f"rag-service {path} returned non-dict data: {type(result_data).__name__}"
            )
        return result_data

    @staticmethod
    def _parse_retrieve_item(item: dict[str, Any]) -> RagChunk:
        """解析 /retrieve 单条结果。"""
        metadata = item.get("metadata") or {}
        return RagChunk(
            id=item.get("doc_id") or metadata.get("doc_id"),
            content=item.get("content") or "",
            score=float(item.get("score") or 0.0),
            chunk_index=int(item.get("chunk_index") or metadata.get("chunk_index") or 0),
            metadata=metadata,
        )

    @staticmethod
    def _parse_rerank_item(item: dict[str, Any]) -> RagChunk:
        """解析 /rerank 单条结果。"""
        metadata = item.get("metadata") or {}
        return RagChunk(
            id=metadata.get("doc_id"),
            content=item.get("content") or "",
            score=float(item.get("score") or 0.0),
            chunk_index=int(metadata.get("chunk_index") or 0),
            metadata=metadata,
        )

    @staticmethod
    def create_from_settings() -> "RagClient":
        """从 Settings 创建默认 RagClient 实例。"""
        return RagClient()
