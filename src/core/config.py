"""
GlobalID V2 配置管理

统一的配置管理，支持环境变量和配置文件
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ========== 应用设置 ==========
    app_name: str = Field(default="GlobalID", description="应用名称")
    version: str = Field(default="2.0.0", description="版本号")
    app_env: str = Field(default="development", description="运行环境")
    debug: bool = Field(default=True, description="调试模式")

    # ========== 日志配置 ==========
    log_level: str = Field(default="INFO", description="日志级别")
    log_dir: Path = Field(default=Path("logs"), description="日志目录")

    # ========== 数据目录 ==========
    data_dir: Path = Field(default=Path("data"), description="数据根目录")
    raw_data_dir: Path = Field(default=Path("data/raw"), description="原始数据目录")
    processed_data_dir: Path = Field(
        default=Path("data/processed"), description="处理后数据目录"
    )
    cache_dir: Path = Field(default=Path("data/cache"), description="缓存目录")
    config_dir: Path = Field(default=Path("configs"), description="配置目录")

    # ========== 数据库配置 ==========
    database_url: str = Field(
        default="postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid",
        description="异步数据库连接URL",
    )
    database_url_sync: str = Field(
        default="postgresql://globalid:globalid_dev_password@localhost:5432/globalid",
        description="同步数据库连接URL（用于Alembic）",
    )
    db_echo: bool = Field(default=False, description="是否打印SQL语句")
    db_pool_size: int = Field(default=10, description="连接池大小")
    db_max_overflow: int = Field(default=20, description="最大溢出连接数")

    # ========== Redis配置 ==========
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis连接URL")

    # ========== Qdrant配置 ==========
    qdrant_url: str = Field(default="http://localhost:6333", description="Qdrant连接URL")
    qdrant_api_key: Optional[str] = Field(default=None, description="Qdrant API密钥")
    qdrant_collection_name: str = Field(default="diseases", description="集合名称")

    # ========== AI模型配置 ==========
    openai_api_key: str = Field(default="", description="OpenAI API密钥")
    anthropic_api_key: str = Field(default="", description="Anthropic API密钥")

    default_model: str = Field(default="gpt-4-turbo", description="默认使用的模型")
    fallback_model: str = Field(default="gpt-3.5-turbo", description="降级模型")

    ai_temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="生成温度")
    ai_max_tokens: int = Field(default=2000, gt=0, description="最大生成tokens")
    max_retries: int = Field(default=3, ge=1, le=5, description="最大重试次数")

    # ========== 功能开关 ==========
    enable_ai_cache: bool = Field(default=True, description="是否启用AI缓存")
    enable_rate_limiting: bool = Field(default=True, description="是否启用速率限制")
    enable_vector_search: bool = Field(default=True, description="是否启用向量搜索")

    # ========== 缓存配置 ==========
    cache_ttl: int = Field(default=3600, description="缓存过期时间（秒）")

    # ========== 速率限制配置 ==========
    rate_limit_requests: int = Field(default=50, description="每分钟请求限制")
    rate_limit_window: int = Field(default=60, description="限流窗口（秒）")

    @field_validator(
        "log_dir",
        "data_dir",
        "raw_data_dir",
        "processed_data_dir",
        "cache_dir",
        "config_dir",
    )
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
def get_config() -> Settings:
    """
    获取配置单例

    使用lru_cache确保全局只有一个配置实例
    """
    return Settings()


# 便捷访问
config = get_config()
