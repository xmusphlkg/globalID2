"""
GlobalID V2 CLI - 命令行接口

提供命令行工具来管理和操作系统
"""

import asyncio
from urllib.parse import urlsplit

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="globalid",
    help="GlobalID V2 - 智能全球疾病监测系统",
    add_completion=False,
)
console = Console()


def _connection_address(url: str) -> str:
    """Show only a connection's host and port, never credentials or URL options."""
    if not url:
        return "未配置"
    try:
        parsed = urlsplit(str(url))
        hostname = parsed.hostname
        if not hostname:
            return "已配置（地址隐藏）"
        host = f"[{hostname}]" if ":" in hostname else hostname
        return f"{host}:{parsed.port}" if parsed.port else host
    except ValueError:
        return "已配置（地址隐藏）"


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
    """显示本地配置与首次初始化设置，不查询运行中的模型目录。"""
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
    table.add_row("数据库地址", _connection_address(cfg.database.url))
    table.add_row("Redis 地址", _connection_address(cfg.redis.url))
    table.add_row("Qdrant 地址", _connection_address(cfg.qdrant.url))
    table.add_row("Qdrant 密钥", "已配置" if cfg.qdrant.api_key else "未配置")
    table.add_row("", "")
    table.add_row("AI 缓存", "已启用" if cfg.ai.enable_cache else "已关闭")
    table.add_row("AI 运行路由", "由模型中心管理；本命令不查询运行目录")
    table.add_row("", "")
    table.add_row("首次初始化提供商", cfg.ai.default_provider)
    table.add_row(
        "首次初始化模型列表",
        ", ".join(cfg.ai.model_chain) or "未设置；仅初始化提供商",
    )
    for provider, field in (
        ("OpenAI", "openai_api_key"),
        ("Anthropic", "anthropic_api_key"),
        ("GLM", "glm_api_key"),
        ("千问", "qianwen_api_key"),
        ("Azure", "azure_api_key"),
        ("自定义", "custom_api_key"),
    ):
        table.add_row(
            f"{provider} 初始化密钥",
            "已配置" if getattr(cfg.ai, field) else "未配置",
        )
    
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


if __name__ == "__main__":
    app()
