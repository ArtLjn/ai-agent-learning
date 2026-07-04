"""全局配置模块。

加载优先级（高 → 低）：
    环境变量  >  config.yaml  >  字段默认值

config.yaml 是项目配置的「单一真相源」，把所有可调参数和凭据集中管理；
该文件不入 git，模板见 config.yaml.example。
如需在容器或不同部署环境中覆盖个别字段，可设置同名环境变量（大写）。
"""

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

__all__ = ["Settings"]


class Settings(BaseSettings):
    """多 Agent 工单处理系统统一配置。

    所有字段均可通过 config.yaml 或同名环境变量（大写）覆盖。
    字段名与 config.yaml 中的键一一对应（扁平结构）。
    """

    # LLM (Chat) — 默认 Ollama Cloud，Embedding — HomeUbuntu 本地
    llm_base_url: str = "https://ollama.com/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "gemma3:12b"
    llm_temperature: float = 0.1

    embedding_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    embedding_api_key: str = ""
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 3072

    # Qdrant 向量数据库配置
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""  # 留空则不启用 auth；真实 key 放 .env，不入 git
    qdrant_collection: str = "knowledge_base"
    qdrant_batch_size: int = 100
    qdrant_top_k: int = 3
    qdrant_score_threshold: float = 0.5

    # rag-service 配置（v2.0：主系统作为 rag-service 客户端，详见 11 号文档）
    rag_service_url: str = "http://localhost:8001"
    rag_service_timeout_seconds: int = 10
    rag_service_retry: int = 1
    rag_service_fallback_enabled: bool = True

    # 文档分块（RAG ingestion）
    chunk_size: int = 512
    chunk_overlap: int = 64

    # 缓存配置
    cache_enabled: bool = True
    cache_max_size: int = 512
    cache_ttl: int = 300  # 秒

    # 上下文管理
    context_max_messages: int = 20
    context_summary_max_tokens: int = 200

    # 处理策略
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    review_threshold: float = 0.7
    workflow_node_delay_seconds: float = 0.8
    checkpoint_ttl: int = 86400  # 24 hours in seconds

    # 人工审核工作台
    # review_timeout_threshold：审核等待超时阈值（秒），前端"超时"标记视觉提示用
    # ai_suggestion_high_confidence_threshold：AI 建议高置信度阈值，前端高亮推荐用
    review_timeout_threshold: int = 1800
    ai_suggestion_high_confidence_threshold: float = 0.7

    # ReAct 配置
    max_react_iterations: int = 10

    # 并发配置
    max_concurrency: int = 5

    # 模型路由配置（Ollama Cloud 可用模型）
    model_routes: dict[str, str] = {
        "classify": "gemma3:12b",
        "process": "gemma3:12b",
        "review": "gemma3:12b",
        "report": "gemma3:27b",
        "default": "gemma3:12b",
    }
    fallback_model: str = "gemma3:12b"

    # HTTP 客户端默认超时（秒）
    http_timeout: int = 30

    # 数据库
    # SQLAlchemy 异步 URL，格式：mysql+aiomysql://user:password@host:port/dbname?charset=utf8mb4
    database_url: str = "mysql+aiomysql://root:Ljnnb666@43.155.217.74:3306/ai_agent_learning?charset=utf8mb4"

    # 可观测性
    trace_max_history: int = 1000

    # API 服务配置
    api_host: str = "0.0.0.0"
    api_port: int = 9001

    # 鉴权配置
    auth_enabled: bool = True
    auth_username: str = "admin"
    auth_password_hash: str = ""
    auth_session_secret: str = "change-me-to-a-random-32-char-string-please"

    model_config = SettingsConfigDict(
        yaml_file="config.yaml",
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """优先级：构造参数 > 环境变量 > config.yaml > 字段默认值。

        dotenv_settings 保留参数签名兼容，未启用 .env 加载。
        """
        return (
            init_settings,
            env_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )
