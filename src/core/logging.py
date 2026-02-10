"""GlobalID V2 日志系统"""

import sys
from loguru import logger

from .config import get_config

_logging_initialized = False


def setup_logging() -> None:
    """配置日志系统"""
    global _logging_initialized
    
    if _logging_initialized:
        return
    
    config = get_config()
    logger.remove()

    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=config.log_level,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    logger.add(
        config.log_dir / "globalid_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=config.log_level,
        rotation="00:00",
        retention="30 days",
        compression="zip",
        enqueue=True,
    )

    logger.add(
        config.log_dir / "error_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}\n{exception}",
        level="ERROR",
        rotation="00:00",
        retention="90 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    _logging_initialized = True
    logger.info(f"Logging initialized - Level: {config.log_level}")


def get_logger(name: str):
    """获取logger实例"""
    if not _logging_initialized:
        setup_logging()
    return logger.bind(name=name)
