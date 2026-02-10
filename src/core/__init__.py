"""核心服务模块"""

from .config import AppSettings, get_config
from .logging import setup_logging, get_logger
from .database import Base, get_engine, get_session_maker, get_db, init_database
from .cache import CacheService, get_cache
from .rate_limiter import RateLimiter

# 别名
get_database = get_db  # 为了兼容性


async def init_app():
    """初始化应用（包括数据库和缓存）"""
    await init_database()
    # 可以在这里添加其他初始化逻辑


__all__ = [
    "AppSettings",
    "get_config",
    "setup_logging",
    "get_logger",
    "Base",
    "get_engine",
    "get_session_maker",
    "get_db",
    "get_database",
    "init_database",
    "init_app",
    "CacheService",
    "get_cache",
    "RateLimiter",
]
