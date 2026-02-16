"""
GlobalID V2 Core Configuration

统一的配置管理，支持环境变量和配置文件
"""
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """数据库配置"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8", 
        case_sensitive=False,
        extra="ignore",
    )

    url: str = Field(
        default="postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid",
        description="异步数据库连接URL",
    )
    url_sync: str = Field(
        default="postgresql://globalid:globalid_dev_password@localhost:5432/globalid",
        description="同步数据库连接URL（用于Alembic）",
    )
    echo: bool = Field(default=False, description="是否打印SQL语句")
    pool_size: int = Field(default=10, description="连接池大小")
    max_overflow: int = Field(default=20, description="最大溢出连接数")


class RedisSettings(BaseSettings):
    """Redis配置"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8", 
        case_sensitive=False,
        extra="ignore",
    )

    url: str = Field(default="redis://localhost:6379/0", description="Redis连接URL")
    encoding: str = Field(default="utf-8", description="编码")
    decode_responses: bool = Field(default=True, description="自动解码响应")


class QdrantSettings(BaseSettings):
    """Qdrant向量数据库配置"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8", 
        case_sensitive=False,
        extra="ignore",
    )

    url: str = Field(default="http://localhost:6333", description="Qdrant连接URL")
    api_key: str | None = Field(default=None, description="API密钥")
    collection_name: str = Field(default="diseases", description="集合名称")
    vector_size: int = Field(default=1536, description="向量维度")


class AISettings(BaseSettings):
    """AI模型配置"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8", 
        case_sensitive=False,
        extra="ignore",
    )

    # 提供商配置
    default_provider: str = Field(default="glm", description="默认AI提供商(openai/anthropic/glm/qianwen/azure/custom)")
    
    # OpenAI配置
    openai_api_key: str = Field(default="", description="OpenAI API密钥")
    openai_base_url: str = Field(default="https://api.openai.com/v1", description="OpenAI API基础URL")
    
    # Anthropic配置
    anthropic_api_key: str = Field(default="", description="Anthropic API密钥")
    
    # GLM智谱AI配置
    glm_api_key: str = Field(default="", description="GLM API密钥")
    glm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4", description="GLM API基础URL")
    
    # 千问配置
    qianwen_api_key: str = Field(default="", description="千问API密钥")
    qianwen_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", description="千问API基础URL")
    
    # Azure OpenAI配置
    azure_api_key: str = Field(default="", description="Azure OpenAI API密钥")
    azure_endpoint: str = Field(default="", description="Azure OpenAI端点")
    azure_api_version: str = Field(default="2024-02-01", description="Azure API版本")
    
    # 自定义配置
    custom_api_key: str = Field(default="", description="自定义API密钥")
    custom_base_url: str = Field(default="", description="自定义API基础URL")
    
    # 默认模型配置  
    default_model: str = Field(default="glm-4-7", description="默认使用的模型")
    fallback_model: str = Field(default="glm-4-plus", description="降级模型")
    
    # 模型配置
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="生成温度")
    max_tokens: int = Field(default=2000, gt=0, description="最大生成tokens")
    max_retries: int = Field(default=3, ge=1, le=5, description="最大重试次数")
    # Reviewer threshold for approval (0.0-1.0)
    reviewer_threshold: float = Field(default=0.7, ge=0.0, le=1.0, description="Reviewer approval threshold")
    
    # 成本控制
    enable_cache: bool = Field(default=True, description="是否启用缓存")
    cache_ttl: int = Field(default=168, description="缓存过期时间（小时）")
    enable_rate_limiting: bool = Field(default=True, description="是否启用限流")
    rate_limit: int = Field(default=50, description="每分钟请求限制")
    
    # 测试配置
    enable_api_test: bool = Field(default=True, description="是否启用API连通性测试")
    test_prompt: str = Field(default="测试成功", description="API测试提示")


class EmailSettings(BaseSettings):
    """邮件配置"""

    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP服务器")
    smtp_port: int = Field(default=587, description="SMTP端口")
    smtp_user: str = Field(default="", description="SMTP用户名")
    smtp_password: str = Field(default="", description="SMTP密码")
    from_address: str = Field(default="", description="发送方邮箱")
    use_tls: bool = Field(default=True, description="使用TLS")
    default_recipients: list[str] = Field(default_factory=list, description="默认收件人列表")


class AppSettingsConfig(BaseSettings):
    """应用基础配置"""

    base_dir: Path = Field(default=Path("."), description="应用根目录")
    output_dir: Path = Field(default=Path("exports"), description="输出目录")


class ReportSettings(BaseSettings):
    """报告配置"""

    output_dir: str = Field(default="reports", description="报告输出目录")
    template_dir: str = Field(default="templates", description="模板目录")
    max_retries: int = Field(default=3, description="最大重试次数")
    enable_email: bool = Field(default=False, description="是否启用邮件发送")


class AppSettings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 基本信息
    app_name: str = Field(default="GlobalID", description="应用名称")
    version: str = Field(default="2.0.0", description="版本号")
    app_env: str = Field(default="development", description="运行环境")
    debug: bool = Field(default=True, description="调试模式")
    
    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别")
    log_dir: Path = Field(default=Path("logs"), description="日志目录")
    
    # 数据目录
    data_dir: Path = Field(default=Path("data"), description="数据根目录")
    raw_data_dir: Path = Field(default=Path("data/raw"), description="原始数据目录")
    processed_data_dir: Path = Field(default=Path("data/processed"), description="处理后数据目录")
    cache_dir: Path = Field(default=Path("data/cache"), description="缓存目录")
    
    # 子配置
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    ai: AISettings = Field(default_factory=AISettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    app: AppSettingsConfig = Field(default_factory=AppSettingsConfig)
    report: ReportSettings = Field(default_factory=ReportSettings)
    
    @field_validator("log_dir", "data_dir", "raw_data_dir", "processed_data_dir", "cache_dir")
    @classmethod
    def ensure_path_exists(cls, v: Path) -> Path:
        """确保目录存在"""
        v.mkdir(parents=True, exist_ok=True)
        return v
    
    @property
    def is_development(self) -> bool:
        """是否开发环境"""
        return self.app_env.lower() in ("dev", "development")
    
    @property
    def is_production(self) -> bool:
        """是否生产环境"""
        return self.app_env.lower() in ("prod", "production")


@lru_cache
def get_config() -> AppSettings:
    """
    获取配置单例
    
    使用lru_cache确保全局只有一个配置实例
    """
    return AppSettings()


# 便捷访问
config = get_config()


if __name__ == "__main__":
    # 测试配置
    from rich import print as rprint
    from rich.panel import Panel
    from rich.table import Table
    
    cfg = get_config()
    
    table = Table(title="GlobalID V2 Configuration", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("App Name", cfg.app_name)
    table.add_row("Version", cfg.version)
    table.add_row("Environment", cfg.app_env)
    table.add_row("Debug", str(cfg.debug))
    table.add_row("Log Level", cfg.log_level)
    table.add_row("", "")
    table.add_row("Database URL", cfg.database.url[:50] + "...")
    table.add_row("Redis URL", cfg.redis.url)
    table.add_row("Qdrant URL", cfg.qdrant.url)
    table.add_row("", "")
    table.add_row("OpenAI Key", "✓" if cfg.ai.openai_api_key else "✗")
    table.add_row("Anthropic Key", "✓" if cfg.ai.anthropic_api_key else "✗")
    table.add_row("Cache Enabled", str(cfg.ai.enable_cache))
    table.add_row("Rate Limiting", str(cfg.ai.enable_rate_limiting))
    
    rprint(table)
    
    rprint("\n[green]✓[/green] Configuration loaded successfully!")
