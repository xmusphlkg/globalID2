"""
GlobalID V2 CLI - 命令行接口

提供命令行工具来管理和操作系统
"""

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="globalid",
    help="GlobalID V2 - 智能全球疾病监测系统",
    add_completion=False,
)
console = Console()


@app.command()
def version():
    """显示版本信息"""
    from src import __version__
    console.print(f"[bold cyan]GlobalID[/bold cyan] [green]v{__version__}[/green]")


@app.command()
def health():
    """检查系统健康状态"""
    console.print("[bold yellow]检查系统健康状态...[/bold yellow]\n")
    
    # 检查配置
    try:
        from src.core import get_config
        config = get_config()
        console.print("✓ [green]配置加载成功[/green]")
    except Exception as e:
        console.print(f"✗ [red]配置加载失败: {e}[/red]")
        raise typer.Exit(1)
    
    # 检查数据库连接
    async def check_db():
        try:
            from src.core import get_engine
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute("SELECT 1")
            console.print("✓ [green]数据库连接正常[/green]")
            return True
        except Exception as e:
            console.print(f"✗ [red]数据库连接失败: {e}[/red]")
            return False
    
    # 检查 Redis
    async def check_redis():
        try:
            from src.core import get_cache
            cache = get_cache()
            await cache.connect()
            console.print("✓ [green]Redis 连接正常[/green]")
            return True
        except Exception as e:
            console.print(f"✗ [red]Redis 连接失败: {e}[/red]")
            return False
    
    # 运行异步检查
    async def run_checks():
        db_ok = await check_db()
        redis_ok = await check_redis()
        return db_ok and redis_ok
    
    all_ok = asyncio.run(run_checks())
    
    if all_ok:
        console.print("\n[bold green]✓ 所有系统正常运行[/bold green]")
    else:
        console.print("\n[bold red]✗ 部分系统存在问题[/bold red]")
        raise typer.Exit(1)


@app.command()
def config():
    """显示当前配置"""
    from src.core import get_config
    
    cfg = get_config()
    
    table = Table(title="GlobalID V2 配置", show_header=True, header_style="bold magenta")
    table.add_column("配置项", style="cyan", width=30)
    table.add_column("值", style="white")
    
    table.add_row("应用名称", cfg.app_name)
    table.add_row("版本", cfg.version)
    table.add_row("环境", cfg.app_env)
    table.add_row("调试模式", str(cfg.debug))
    table.add_row("", "")
    table.add_row("数据库", cfg.database_url.split("@")[1] if "@" in cfg.database_url else "未配置")
    table.add_row("Redis", cfg.redis_url)
    table.add_row("Qdrant", cfg.qdrant_url)
    table.add_row("", "")
    table.add_row("AI 缓存", "✓" if cfg.enable_ai_cache else "✗")
    table.add_row("速率限制", "✓" if cfg.enable_rate_limiting else "✗")
    table.add_row("向量搜索", "✓" if cfg.enable_vector_search else "✗")
    table.add_row("", "")
    table.add_row("OpenAI Key", "✓ 已配置" if cfg.openai_api_key else "✗ 未配置")
    table.add_row("默认模型", cfg.default_model)
    table.add_row("最大重试", str(cfg.max_retries))
    
    console.print(table)


@app.command()
def init_db():
    """初始化数据库"""
    console.print("[bold yellow]初始化数据库...[/bold yellow]\n")
    
    async def _init():
        from src.core import init_database
        await init_database()
    
    try:
        asyncio.run(_init())
        console.print("[bold green]✓ 数据库初始化成功[/bold green]")
    except Exception as e:
        console.print(f"[bold red]✗ 数据库初始化失败: {e}[/bold red]")
        raise typer.Exit(1)


@app.command()
def test_cache():
    """测试缓存功能"""
    console.print("[bold yellow]测试缓存功能...[/bold yellow]\n")
    
    async def _test():
        from src.core import get_cache
        
        cache = get_cache()
        
        # 设置缓存
        console.print("1. 设置缓存: test_key = 'Hello, GlobalID!'")
        await cache.set("test_key", "Hello, GlobalID!", ttl=60)
        
        # 读取缓存
        console.print("2. 读取缓存...")
        value = await cache.get("test_key")
        console.print(f"   获取到: [cyan]{value}[/cyan]")
        
        # 检查存在
        exists = await cache.exists("test_key")
        console.print(f"3. 缓存存在: [green]{exists}[/green]")
        
        # 获取 TTL
        ttl = await cache.get_ttl("test_key")
        console.print(f"4. 剩余时间: [yellow]{ttl}秒[/yellow]")
        
        # 删除缓存
        console.print("5. 删除缓存...")
        await cache.delete("test_key")
        
        # 验证删除
        exists_after = await cache.exists("test_key")
        console.print(f"6. 删除后存在: [red]{exists_after}[/red]")
        
        console.print("\n[bold green]✓ 缓存功能测试通过[/bold green]")
    
    try:
        asyncio.run(_test())
    except Exception as e:
        console.print(f"\n[bold red]✗ 缓存测试失败: {e}[/bold red]")
        raise typer.Exit(1)


@app.command()
def test_limiter():
    """测试速率限制器"""
    console.print("[bold yellow]测试速率限制器...[/bold yellow]\n")
    
    from src.core import RateLimiter
    
    # 创建一个测试用的限制器（5次/10秒）
    limiter = RateLimiter(max_requests=5, window_seconds=10)
    
    console.print(f"限制: {limiter.max_requests} 次请求 / {limiter.window_seconds} 秒\n")
    
    # 模拟10次请求
    for i in range(10):
        if limiter.can_proceed():
            limiter.record_request()
            console.print(f"请求 {i+1}: [green]✓ 允许[/green]")
        else:
            wait = limiter.wait_time()
            console.print(f"请求 {i+1}: [red]✗ 限流（需等待 {wait:.2f}秒）[/red]")
    
    # 显示统计
    stats = limiter.get_stats()
    console.print(f"\n统计信息:")
    console.print(f"  当前请求数: {stats['current_requests']}/{stats['max_requests']}")
    console.print(f"  使用率: {stats['usage_percent']}%")
    console.print(f"  可继续: {stats['can_proceed']}")
    
    console.print("\n[bold green]✓ 速率限制器测试完成[/bold green]")


if __name__ == "__main__":
    app()
