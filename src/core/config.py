"""
GlobalID V2 Core Configuration

统一的配置管理，支持环境变量和配置文件
"""
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class _BaseEnvSettings(BaseSettings):
    """Shared base for all sub-settings that read from .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


class DatabaseSettings(_BaseEnvSettings):
    """数据库配置"""

    url: str = Field(
        default="postgresql+asyncpg://globalid:globalid_dev_password@localhost:5432/globalid",
        validation_alias="DATABASE_URL",
        description="异步数据库连接URL",
    )
    url_sync: str = Field(
        default="postgresql://globalid:globalid_dev_password@localhost:5432/globalid",
        validation_alias="DATABASE_URL_SYNC",
        description="同步数据库连接URL（用于Alembic）",
    )
    echo: bool = Field(default=False, description="是否打印SQL语句")
    pool_size: int = Field(default=10, description="连接池大小")
    max_overflow: int = Field(default=20, description="最大溢出连接数")


class RedisSettings(_BaseEnvSettings):
    """Redis配置"""

    url: str = Field(default="redis://localhost:6379/0", description="Redis连接URL")
    encoding: str = Field(default="utf-8", description="编码")
    decode_responses: bool = Field(default=True, description="自动解码响应")


class QdrantSettings(_BaseEnvSettings):
    """Qdrant向量数据库配置"""

    url: str = Field(default="http://localhost:6333", description="Qdrant连接URL")
    api_key: str | None = Field(default=None, description="API密钥")
    collection_name: str = Field(default="agent_memory_v1", description="集合名称")
    vector_size: int = Field(default=1536, description="向量维度")


class AISettings(_BaseEnvSettings):
    """AI模型配置"""

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
    model_chain_raw: str = Field(
        default="",
        description="模型优先级列表，逗号分隔（高→低），为空则使用 default_model + fallback_model",
    )
    knowledge_model_shards_raw: str = Field(
        default="",
        description="知识库任务的模型分流列表，逗号分隔；为空时回退到 model_chain",
    )
    agent_role_models_raw: str = Field(
        default="",
        description="多专家角色模型偏好，格式 role=model1|model2,role2=model3；为空时回退到 model_chain",
    )
    agent_max_replan_rounds: int = Field(default=1, ge=0, le=10, description="Agent workflow 最大重规划次数")
    agent_max_search_rounds: int = Field(default=2, ge=1, le=10, description="Agent workflow 最大搜索轮次")
    agent_step_token_budget: int = Field(default=2500, gt=0, description="单步 token 预算")
    agent_total_token_budget: int = Field(default=12000, gt=0, description="单次 workflow 总 token 预算")
    
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
    rate_limit_cooldown_seconds: int = Field(default=300, gt=0, description="命中429/额度限制后的冷却秒数")
    rate_limit_wait_cap_seconds: int = Field(
        default=900,
        gt=0,
        description="限流恢复阶段单次最大等待秒数（建议大于等于冷却时间）",
    )
    rate_limit_recovery_max_rounds: int = Field(
        default=4,
        ge=0,
        le=20,
        description="当全部候选因限流失败时，允许的恢复重试轮数",
    )
    route_cache_ttl_seconds: int = Field(default=15, gt=0, description="模型中心运行时路由缓存秒数")
    prompt_auto_reload: bool = Field(
        default=False,
        description="是否启用提示词模板热刷新（开发环境建议开启）",
    )
    
    # 测试配置
    enable_api_test: bool = Field(default=True, description="是否启用API连通性测试")
    test_prompt: str = Field(default="测试成功", description="API测试提示")

    @property
    def model_chain(self) -> list[str]:
        """
        返回去重后的模型优先级列表。
        
        优先使用 model_chain_raw；如果未配置，则退回 [default_model, fallback_model]。
        """
        raw = self.model_chain_raw.strip()
        candidates: list[str] = []
        if raw:
            parts = [m.strip() for m in raw.split(",") if m.strip()]
            candidates.extend(parts)
        else:
            if self.default_model:
                candidates.append(self.default_model)
            if self.fallback_model and self.fallback_model != self.default_model:
                candidates.append(self.fallback_model)

        seen: set[str] = set()
        ordered: list[str] = []
        for m in candidates:
            if m and m not in seen:
                seen.add(m)
                ordered.append(m)
        return ordered

    @property
    def knowledge_model_shards(self) -> list[str]:
        """
        返回知识库生成的候选分流模型列表。

        若未显式配置 knowledge_model_shards_raw，则回退到通用 model_chain。
        """
        raw = self.knowledge_model_shards_raw.strip()
        if not raw:
            return list(self.model_chain)

        seen: set[str] = set()
        ordered: list[str] = []
        for item in [m.strip() for m in raw.split(",") if m.strip()]:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    @property
    def agent_role_models(self) -> dict[str, list[str]]:
        """Parse role-specific model preferences.

        Supported formats:
        - JSON object: {"planner": ["gpt-4o-mini", "gpt-4o"], "reviewer": ["gpt-4o-mini"]}
        - Compact string: planner=gpt-4o-mini|gpt-4o,reviewer=gpt-4o-mini
        """
        raw = self.agent_role_models_raw.strip()
        if not raw:
            return {}

        parsed: dict[str, list[str]] = {}
        if raw.startswith("{"):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for key, value in payload.items():
                    role = str(key).strip().lower()
                    if not role:
                        continue
                    if isinstance(value, str):
                        models = [item.strip() for item in value.split("|") if item.strip()]
                    elif isinstance(value, list):
                        models = [str(item).strip() for item in value if str(item).strip()]
                    else:
                        models = []
                    if models:
                        parsed[role] = models
                return parsed

        for chunk in [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]:
            separator = "=" if "=" in chunk else (":" if ":" in chunk else None)
            if not separator:
                continue
            role, model_blob = chunk.split(separator, 1)
            role = role.strip().lower()
            models = [item.strip() for item in model_blob.split("|") if item.strip()]
            if role and models:
                parsed[role] = models
        return parsed


class AutomationSettings(_BaseEnvSettings):
    """数据流自动化与失败通知配置"""

    enabled: bool = Field(default=False, description="是否启用自动化调度")
    timezone: str = Field(default="UTC", description="自动化调度时区")
    poll_interval_seconds: int = Field(default=30, ge=5, description="自动化轮询间隔秒数")
    default_retry_threshold: int = Field(default=3, ge=1, le=20, description="失败告警触发阈值")
    jobs_json: str = Field(default="[]", description="自动化任务 JSON 列表")
    admin_emails_raw: str = Field(default="", description="管理员邮箱，逗号分隔")
    smtp_host: str = Field(default="", description="SMTP 服务器地址 (如 email-smtp.us-east-1.amazonaws.com)")
    smtp_port: int = Field(default=587, description="SMTP 端口 (587=STARTTLS, 465=SSL)")
    smtp_username: str = Field(default="", description="SMTP 用户名 (AWS SES SMTP_USER_NAME)")
    smtp_password: str = Field(default="", description="SMTP 密码 (AWS SES SMTP_USER_PASSWORD)")
    smtp_from_email: str = Field(default="", description="发件人邮箱地址")
    smtp_use_tls: bool = Field(default=True, description="是否使用 STARTTLS (否则使用 SMTP_SSL)")

    @property
    def admin_emails(self) -> list[str]:
        return [item.strip() for item in self.admin_emails_raw.split(",") if item.strip()]

    @property
    def jobs(self) -> list[dict[str, Any]]:
        raw = self.jobs_json.strip()
        if not raw:
            return []
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(loaded, list):
            return []
        return [item for item in loaded if isinstance(item, dict)]


class DataReleaseSettings(_BaseEnvSettings):
    """静态站点数据发布与部署配置"""

    enabled: bool = Field(default=True, description="是否启用 data release 调度")
    timezone: str = Field(default="UTC", description="默认 data release 调度时区")
    poll_interval_seconds: int = Field(default=30, ge=5, description="data release 轮询间隔秒数")
    default_github_remote: str = Field(default="origin", description="默认 Git 远端名称")
    default_github_branch: str = Field(default="", description="默认 Git 分支，为空时读取当前分支")
    default_cloudflare_project_name: str = Field(
        default="globalid",
        validation_alias="CLOUDFLARE_PROJECT_NAME",
        description="默认 Cloudflare Pages 项目名",
    )
    default_commit_message_template: str = Field(
        default="chore(data-release): publish site data {timestamp}",
        description="默认发布提交消息模板",
    )


class AppSettingsConfig(BaseSettings):
    """应用基础配置"""

    base_dir: Path = Field(default=Path("."), description="应用根目录")
    output_dir: Path = Field(default=Path("exports"), description="输出目录")


class ReportSettings(BaseSettings):
    """报告配置"""

    model_config = SettingsConfigDict(env_prefix="")

    output_dir: str = Field(default="reports", description="报告输出目录")
    template_dir: str = Field(default="templates", description="模板目录")
    max_retries: int = Field(default=3, description="最大重试次数")
    max_parallel_tasks: int = Field(
        default=20,
        alias="MAX_PARALLEL_TASKS",
        description="AI并行任务数"
    )


class CrawlerSettings(BaseSettings):
    """爬虫配置"""

    model_config = SettingsConfigDict(env_prefix="")

    max_concurrent: int = Field(
        default=2,
        alias="MAX_CRAWLER_CONCURRENT",
        description="爬虫并发数"
    )
    timeout: int = Field(default=30, description="请求超时时间（秒）")
    max_retries: int = Field(default=3, description="最大重试次数")
    delay: float = Field(default=1.0, description="请求延迟（秒）")


class TaskWorkerSettings(_BaseEnvSettings):
    """后台任务 worker 配置"""

    concurrency: int = Field(
        default=2,
        validation_alias="TASK_WORKER_CONCURRENCY",
        ge=1,
        le=64,
        description="任务 worker 最大并发数",
    )
    poll_interval_seconds: float = Field(
        default=2.0,
        validation_alias="TASK_WORKER_POLL_INTERVAL",
        gt=0,
        description="任务 worker 拉取队列轮询间隔（秒）",
    )
    idle_log_every: int = Field(
        default=30,
        validation_alias="TASK_WORKER_IDLE_LOG_EVERY",
        ge=1,
        description="worker 空闲日志输出周期（轮）",
    )


class AppSettings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
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
    processed_data_dir: Path = Field(default=Path("data/processed"), description="处理后数据目录（不含 history/current 约定目录）")
    cache_dir: Path = Field(default=Path("data/cache"), description="缓存目录")
    github_data_share_repo_url: str = Field(default="", description="下载数据分享 GitHub 仓库 URL")
    github_data_share_repo_branch: str = Field(default="main", description="下载数据分享 GitHub 分支")
    github_data_share_raw_base_url: str = Field(default="", description="下载数据分享公开 raw base URL")
    cloudflare_api_token: str = Field(default="", description="Cloudflare API token")
    cloudflare_account_id: str = Field(default="", description="Cloudflare account id")
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
