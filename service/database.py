"""PostgreSQL 就绪检查与连接生命周期；SQLite 仅用于本地 Mock 回退。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from service.settings import settings

_engine: AsyncEngine | None = None


def get_database_engine() -> AsyncEngine | None:
    global _engine
    if not settings.database_url:
        return None
    if _engine is None:
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
    return _engine


async def ensure_database_ready() -> bool:
    engine = get_database_engine()
    if engine is None:
        return not settings.is_production
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def close_database() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
