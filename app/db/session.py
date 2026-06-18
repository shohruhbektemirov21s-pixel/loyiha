"""Async SQLAlchemy engine and session factory.

One engine per process, created during the FastAPI lifespan. Sessions are
request-scoped: each request gets its own ``AsyncSession`` via
``Depends(get_db)``, committed on success, rolled back on error.

Connection string (via Settings):
    postgresql+asyncpg://user:password@host:5432/dbname

TLS on the LAN:
    Set XRAY_DB_SSL_MODE=require and optionally supply XRAY_DB_SSL_CERT_PATH
    for mTLS. asyncpg supports ssl= parameter; we pass it through.

The engine is module-level but created lazily (only after ``init_db`` is
called in the lifespan). Importing this module on a box without asyncpg or
without a configured DB is safe — nothing connects until ``init_db`` runs.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

log = logging.getLogger("xray.db")

# Module-level singletons; populated by init_db() during lifespan startup.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str, *, echo: bool = False, pool_size: int = 10, ssl: bool = False) -> None:
    """Create the engine and session factory. Call once from lifespan."""
    global _engine, _session_factory

    if _engine is not None:
        log.warning("init_db called twice — ignoring second call")
        return

    connect_args: dict = {}
    if ssl:
        import ssl as _ssl
        ctx = _ssl.create_default_context()
        connect_args["ssl"] = ctx

    _engine = create_async_engine(
        database_url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=5,
        pool_pre_ping=True,     # detect stale connections
        connect_args=connect_args,
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        autoflush=False,
    )
    log.info("DB engine initialised (pool_size=%d ssl=%s)", pool_size, ssl)


async def close_db() -> None:
    """Dispose the engine pool. Call from lifespan shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        log.info("DB engine disposed")


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("DB not initialised. Call init_db() in the lifespan.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("DB not initialised. Call init_db() in the lifespan.")
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a request-scoped async session.

    Commits on success, rolls back on any exception. The caller should not
    call ``session.commit()`` themselves (it will be a no-op anyway, but is
    confusing). For operations that need explicit savepoints, use
    ``session.begin_nested()``.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


__all__ = ["init_db", "close_db", "get_db", "get_engine", "get_session_factory"]
