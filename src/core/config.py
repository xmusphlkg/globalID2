"""
GlobalID V2 Core Configuration

统一的配置管理，支持环境变量和配置文件
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .settings import (
    PROJECT_ENV_FILE,
    PROJECT_ROOT,
    AISettings,
    AppSettingsConfig,
    AutomationSettings,
    CrawlerSettings,
    DatabaseSettings,
    DataReleaseSettings,
    LiteratureSettings,
    QdrantSettings,
    RawArchiveSettings,
    RedisSettings,
    ReportSettings,
    SituationSettings,
    TaskWorkerSettings,
    _BaseEnvSettings,
)


class AppSettings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Allow AI__FIELD_NAME in .env to populate the nested `ai: AISettings` sub-model
        env_nested_delimiter="__",
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
    raw_data_dir: Path = Field(default=Path("data/raw"), description="原始抓取/调试缓存目录")
    processed_data_dir: Path = Field(
        default=Path("data/processed"),
        description="处理后数据目录（不含 history/current 约定目录）",
    )
    cache_dir: Path = Field(default=Path("data/cache"), description="缓存目录")
    github_data_share_repo_url: str = Field(default="", description="下载数据分享 GitHub 仓库 URL")
    github_data_share_repo_branch: str = Field(default="main", description="下载数据分享 GitHub 分支")
    github_data_share_raw_base_url: str = Field(default="", description="下载数据分享公开 raw base URL")
    cloudflare_api_token: str = Field(default="", description="Cloudflare API token")
    cloudflare_account_id: str = Field(default="", description="Cloudflare account id")
    public_ga4_measurement_id: str = Field(
        default="G-8P39XV52NC",
        validation_alias="PUBLIC_GA4_MEASUREMENT_ID",
        description="Astro 网站构建时注入的公开 GA4 Measurement ID",
    )
    dashboard_api_key: str = Field(
        default="",
        validation_alias="DASHBOARD_API_KEY",
        description="Optional shared secret required by dashboard HTTP API requests",
    )

    # 子配置
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    ai: AISettings = Field(default_factory=AISettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    data_release: DataReleaseSettings = Field(default_factory=DataReleaseSettings)
    raw_archive: RawArchiveSettings = Field(default_factory=RawArchiveSettings)
    literature: LiteratureSettings = Field(default_factory=LiteratureSettings)
    situation: SituationSettings = Field(default_factory=SituationSettings)
    app: AppSettingsConfig = Field(default_factory=AppSettingsConfig)
    report: ReportSettings = Field(default_factory=ReportSettings)
    crawler: CrawlerSettings = Field(default_factory=CrawlerSettings)
    task_worker: TaskWorkerSettings = Field(default_factory=TaskWorkerSettings)

    @field_validator("log_dir", "data_dir", "raw_data_dir", "processed_data_dir", "cache_dir")
    @classmethod
    def ensure_path_exists(cls, v: Path) -> Path:
        """确保目录存在"""
        v.mkdir(parents=True, exist_ok=True)
        return v

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug(cls, v: Any) -> Any:
        """兼容历史环境变量值，如 DEBUG=release。"""
        if isinstance(v, str):
            normalized = v.strip().lower()
            if normalized in {"release", "prod", "production", "false", "0", "off", "no"}:
                return False
            if normalized in {"debug", "dev", "development", "true", "1", "on", "yes"}:
                return True
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


__all__ = [
    "PROJECT_ROOT",
    "PROJECT_ENV_FILE",
    "_BaseEnvSettings",
    "DatabaseSettings",
    "RedisSettings",
    "QdrantSettings",
    "AISettings",
    "AutomationSettings",
    "DataReleaseSettings",
    "RawArchiveSettings",
    "SituationSettings",
    "LiteratureSettings",
    "AppSettingsConfig",
    "ReportSettings",
    "CrawlerSettings",
    "TaskWorkerSettings",
    "AppSettings",
    "get_config",
    "config",
]


if __name__ == "__main__":
    # 测试配置
    from rich import print as rprint
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

    rprint(table)

    rprint("\n[green]✓[/green] Configuration loaded successfully!")
