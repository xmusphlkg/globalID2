"""Connection and bootstrap helpers for the dedicated Situation history DB."""

from __future__ import annotations

from contextlib import asynccontextmanager
import re
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_config
from src.core.logging import get_logger
from src.domain.situation_history import HistoryBase


logger = get_logger(__name__)
_engine: AsyncEngine | None = None
_session_maker: async_sessionmaker[AsyncSession] | None = None
_schema_initialized = False


def history_database_url() -> URL:
    """Resolve the explicit history URL or derive it from the primary DB."""
    config = get_config()
    history = config.situation_history_database
    if history.url.strip():
        return make_url(history.url.strip())
    return make_url(config.database.url).set(database=history.database_name)


def history_database_descriptor() -> dict[str, object]:
    """Return safe connection metadata for the control panel (never a password)."""
    url = history_database_url()
    return {
        "enabled": get_config().situation_history_database.enabled,
        "driver": url.drivername,
        "host": url.host,
        "port": url.port,
        "database": url.database,
        "isolated_from_primary": url.database != make_url(get_config().database.url).database,
    }


def get_history_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        config = get_config().situation_history_database
        url = history_database_url()
        kwargs: dict[str, object] = {
            "echo": config.echo,
            "pool_pre_ping": True,
        }
        if url.get_backend_name() != "sqlite":
            kwargs.update(
                pool_size=config.pool_size,
                max_overflow=config.max_overflow,
                pool_recycle=3600,
            )
        _engine = create_async_engine(url, **kwargs)
        logger.info("Situation history database engine created for {}", url.database)
    return _engine


def get_history_session_maker() -> async_sessionmaker[AsyncSession]:
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_history_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_maker


@asynccontextmanager
async def get_history_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_history_session_maker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_history_database_if_missing() -> bool:
    """Create the configured PostgreSQL database, returning whether it was new.

    Database creation cannot run inside a PostgreSQL transaction, so this uses
    a short AUTOCOMMIT connection to the primary database.  The configured name
    is validated before it is interpolated as an identifier.
    """
    config = get_config()
    if not config.situation_history_database.enabled:
        return False
    target = history_database_url()
    if target.get_backend_name() != "postgresql":
        return False
    database_name = target.database or ""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", database_name):
        raise ValueError("Invalid Situation history database name")

    admin_url = make_url(config.database.url)
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = bool(
                (
                    await conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                        {"database_name": database_name},
                    )
                ).scalar_one_or_none()
            )
            if exists:
                return False
            await conn.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
            logger.info("Created Situation history database {}", database_name)
            return True
    finally:
        await admin_engine.dispose()


async def init_history_database(*, create_database: bool = False) -> bool:
    """Create the database when requested and idempotently create its schema."""
    global _schema_initialized
    config = get_config().situation_history_database
    if not config.enabled:
        return False
    if _schema_initialized:
        return False
    created = await create_history_database_if_missing() if create_database else False
    async with get_history_engine().begin() as conn:
        await conn.run_sync(HistoryBase.metadata.create_all)
    _schema_initialized = True
    return created


async def dispose_history_database() -> None:
    global _engine, _session_maker, _schema_initialized
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_maker = None
    _schema_initialized = False


__all__ = [
    "create_history_database_if_missing",
    "dispose_history_database",
    "get_history_db",
    "get_history_engine",
    "history_database_descriptor",
    "history_database_url",
    "init_history_database",
]
