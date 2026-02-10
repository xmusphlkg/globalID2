"""GlobalID V2 数据库连接管理"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)

Base = declarative_base()

_engine = None
_session_maker = None


def get_engine():
    """获取数据库引擎"""
    global _engine
    if _engine is None:
        config = get_config()
        _engine = create_async_engine(
            config.database.url,
            echo=config.database.echo,
            pool_size=config.database.pool_size,
            max_overflow=config.database.max_overflow,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        logger.info(f"Database engine created")
    return _engine


def get_session_maker():
    """获取session maker"""
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("Session maker created")
    return _session_maker


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库session"""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_database():
    """初始化数据库"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")
