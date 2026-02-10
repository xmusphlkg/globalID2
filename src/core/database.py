"""
GlobalID V2 数据库连接管理

基于 SQLAlchemy 2.0 的异步数据库连接
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from .config import get_config
from .logging import get_logger

logger = get_logger(__name__)

# 创建Base - 所有模型的基类
Base = declarative_base()

# 全局引擎和session maker
_engine = None
_session_maker = None


def get_engine():
    """获取数据库引擎（单例）"""
    global _engine
    if _engine is None:
        config = get_config()
        _engine = create_async_engine(
            config.database_url,
            echo=config.db_echo,
            pool_size=config.db_pool_size,
            max_overflow=config.db_max_overflow,
            pool_pre_ping=True,  # 连接前测试
            pool_recycle=3600,  # 1小时回收连接
        )
        logger.info(f"Database engine created: {config.database_url.split('@')[1]}")
    return _engine


def get_session_maker():
    """获取session maker（单例）"""
    global _session_maker
    if _session_maker is None:
        _session_maker = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # 提交后不过期对象
        )
        logger.info("Session maker created")
    return _session_maker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    获取数据库session（依赖注入用）

    用法：
        async with get_db() as session:
            result = await session.execute(...)
    """
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
    """
    初始化数据库（创建所有表）

    注意：生产环境应使用 Alembic 进行迁移
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")


async def close_database():
    """关闭数据库连接"""
    global _engine
    if _engine:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None
