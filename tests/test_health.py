"""
GlobalID V2 - 测试核心功能

快速测试脚本，验证各个组件是否正常工作
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


async def test_config():
    """测试配置加载"""
    console.print("\n[bold cyan]1. 测试配置加载[/bold cyan]")
    try:
        from src.core import get_config
        
        config = get_config()
        console.print(f"  ✓ 配置加载成功")
        console.print(f"  ✓ 应用名称: {config.app_name}")
        console.print(f"  ✓ 版本: {config.version}")
        console.print(f"  ✓ 环境: {config.app_env}")
        return True
    except Exception as e:
        console.print(f"  ✗ [red]配置加载失败: {e}[/red]")
        return False


async def test_logging():
    """测试日志系统"""
    console.print("\n[bold cyan]2. 测试日志系统[/bold cyan]")
    try:
        from src.core import setup_logging, get_logger
        
        setup_logging()
        logger = get_logger(__name__)
        
        logger.info("这是一条测试日志")
        logger.debug("Debug 级别日志")
        logger.warning("警告日志")
        
        console.print("  ✓ 日志系统初始化成功")
        console.print("  ✓ 日志已写入 logs/ 目录")
        return True
    except Exception as e:
        console.print(f"  ✗ [red]日志系统失败: {e}[/red]")
        return False


async def test_database():
    """测试数据库连接"""
    console.print("\n[bold cyan]3. 测试数据库连接[/bold cyan]")
    try:
        from src.core import get_engine
        from sqlalchemy import text
        
        engine = get_engine()
        
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version();"))
            version = result.scalar()
            console.print(f"  ✓ 数据库连接成功")
            console.print(f"  ✓ PostgreSQL 版本: {version.split(',')[0]}")
            
            # 检查 TimescaleDB 扩展
            result = await conn.execute(
                text("SELECT * FROM pg_extension WHERE extname = 'timescaledb';")
            )
            if result.fetchone():
                console.print(f"  ✓ TimescaleDB 扩展已安装")
            
            # 检查 vector 扩展
            result = await conn.execute(
                text("SELECT * FROM pg_extension WHERE extname = 'vector';")
            )
            if result.fetchone():
                console.print(f"  ✓ pgvector 扩展已安装")
        
        return True
    except Exception as e:
        console.print(f"  ✗ [red]数据库连接失败: {e}[/red]")
        return False


async def test_redis():
    """测试 Redis 连接"""
    console.print("\n[bold cyan]4. 测试 Redis 缓存[/bold cyan]")
    try:
        from src.core import get_cache
        
        cache = get_cache()
        await cache.connect()
        
        # 测试设置和获取
        test_key = "test:health_check"
        test_value = {"status": "ok", "timestamp": "2026-02-10"}
        
        await cache.set(test_key, test_value, ttl=60)
        console.print(f"  ✓ 缓存写入成功")
        
        retrieved = await cache.get(test_key)
        if retrieved == test_value:
            console.print(f"  ✓ 缓存读取成功")
        
        await cache.delete(test_key)
        console.print(f"  ✓ 缓存删除成功")
        
        return True
    except Exception as e:
        console.print(f"  ✗ [red]Redis 连接失败: {e}[/red]")
        console.print(f"     提示: 确保 Redis 容器正在运行")
        return False


async def test_rate_limiter():
    """测试速率限制器"""
    console.print("\n[bold cyan]5. 测试速率限制器[/bold cyan]")
    try:
        from src.core import RateLimiter
        
        limiter = RateLimiter(max_requests=5, window_seconds=10)
        console.print(f"  ✓ 限制器初始化成功")
        console.print(f"  ✓ 限制: {limiter.max_requests} 次/{limiter.window_seconds}秒")
        
        # 模拟请求
        for i in range(5):
            if limiter.can_proceed():
                limiter.record_request()
        
        stats = limiter.get_stats()
        console.print(f"  ✓ 已记录 {stats['current_requests']} 次请求")
        console.print(f"  ✓ 使用率: {stats['usage_percent']}%")
        
        return True
    except Exception as e:
        console.print(f"  ✗ [red]速率限制器失败: {e}[/red]")
        return False


async def main():
    """运行所有测试"""
    console.print(Panel.fit(
        "[bold cyan]GlobalID V2 - 系统健康检查[/bold cyan]\n"
        "测试所有核心组件",
        border_style="cyan"
    ))
    
    results = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        
        task = progress.add_task("运行测试...", total=5)
        
        results.append(await test_config())
        progress.update(task, advance=1)
        
        results.append(await test_logging())
        progress.update(task, advance=1)
        
        results.append(await test_database())
        progress.update(task, advance=1)
        
        results.append(await test_redis())
        progress.update(task, advance=1)
        
        results.append(await test_rate_limiter())
        progress.update(task, advance=1)
    
    # 汇总结果
    console.print("\n" + "="*70)
    passed = sum(results)
    total = len(results)
    
    if passed == total:
        console.print(Panel.fit(
            f"[bold green]✓ 所有测试通过 ({passed}/{total})[/bold green]\n"
            "系统运行正常，可以开始开发！",
            border_style="green"
        ))
        return 0
    else:
        console.print(Panel.fit(
            f"[bold yellow]部分测试失败 ({passed}/{total})[/bold yellow]\n"
            "请检查失败的组件",
            border_style="yellow"
        ))
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
