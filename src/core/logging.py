"""
GlobalID V2 日志系统

基于 loguru 的统一日志管理
"""

import sys
from pathlib import Path

from loguru import logger

from .config import get_config

# 全局标记，避免重复初始化
_logging_initialized = False


def setup_logging() -> None:
    """配置日志系统"""
    global _logging_initialized
    
    if _logging_initialized:
        return
    
    config = get_config()

    # 移除默认handler
    logger.remove()

    # 控制台输出 - 带颜色
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=config.log_level,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # 文件输出 - 所有日志
    logger.add(
        config.log_dir / "globalid_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=config.log_level,
        rotation="00:00",  # 每天轮转
        retention="30 days",  # 保留30天
        compression="zip",  # 压缩旧日志
        enqueue=True,  # 异步写入
    )

    # 错误日志单独记录
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
    logger.info(f"Logging initialized - Level: {config.log_level}, Log dir: {config.log_dir}")


def get_logger(name: str):
    """
    获取logger实例

    Args:
        name: logger名称，通常使用 __name__

    Returns:
        绑定了名称的logger实例
    """
    if not _logging_initialized:
        setup_logging()
    return logger.bind(name=name)
