"""集中管理运行时配置，并在生产环境对安全关键项执行失败即停止。"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    jwt_secret_key: str | None
    jwt_issuer: str | None
    jwt_audience: str | None
    request_max_bytes: int
    chat_max_characters: int
    rate_limit_per_minute: int
    database_url: str | None
    order_service_base_url: str | None
    order_service_token: str | None
    knowledge_version: str
    rag_auto_build: bool
    rag_reranker_enabled: bool

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            environment=os.getenv("APP_ENV", "development"),
            jwt_secret_key=os.getenv("JWT_SECRET_KEY"),
            jwt_issuer=os.getenv("JWT_ISSUER"),
            jwt_audience=os.getenv("JWT_AUDIENCE"),
            request_max_bytes=int(os.getenv("REQUEST_MAX_BYTES", "16384")),
            chat_max_characters=int(os.getenv("CHAT_MAX_CHARACTERS", "4000")),
            rate_limit_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")),
            database_url=os.getenv("DATABASE_URL"),
            order_service_base_url=os.getenv("ORDER_SERVICE_BASE_URL"),
            order_service_token=os.getenv("ORDER_SERVICE_TOKEN"),
            knowledge_version=os.getenv("KNOWLEDGE_VERSION", "unversioned"),
            rag_auto_build=_as_bool(os.getenv("RAG_AUTO_BUILD"), False),
            rag_reranker_enabled=_as_bool(os.getenv("RAG_RERANKER_ENABLED"), False),
        )

    def validate_for_startup(self) -> None:
        if not self.is_production:
            return
        missing = []
        if not self.jwt_secret_key:
            missing.append("JWT_SECRET_KEY")
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.order_service_base_url:
            missing.append("ORDER_SERVICE_BASE_URL")
        if not self.order_service_token:
            missing.append("ORDER_SERVICE_TOKEN")
        if missing:
            raise RuntimeError(f"生产环境缺少必要配置: {', '.join(missing)}")


settings = Settings.from_env()
