"""Data release, raw archive mirroring, and report generation settings."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import _BaseEnvSettings


class DataReleaseSettings(_BaseEnvSettings):
    """静态站点数据发布与部署配置"""

    theme_publish_enabled: bool = Field(default=False, validation_alias="GIDS_THEME_PUBLISH_ENABLED")
    theme_r2_bucket: str = Field(default="", validation_alias="GIDS_THEME_R2_BUCKET")
    theme_public_base_url: str = Field(default="", validation_alias="GIDS_THEME_PUBLIC_BASE_URL")
    theme_compat_v1_ready: bool = Field(default=False, validation_alias="GIDS_THEME_COMPAT_V1_READY")
    theme_stylesheet_url: str = Field(default="", validation_alias="PUBLIC_GIDS_THEME_V1_URL")

    surveillance_json_enabled: bool = Field(
        default=False,
        validation_alias="DATA_RELEASE__SURVEILLANCE_JSON_ENABLED",
        description="启用独立监测JSON发布；先初始化数据并部署同源Worker，开启后再发布动态页面模板",
    )

    situation_json_enabled: bool = Field(
        default=False,
        validation_alias="DATA_RELEASE__SITUATION_JSON_ENABLED",
        description="启用Situation独立JSON发布；先初始化数据与同源Worker，再启用更新触发和动态页面",
    )

    literature_json_enabled: bool = Field(
        default=False,
        validation_alias="DATA_RELEASE__LITERATURE_JSON_ENABLED",
        description="启用独立文献发布；首次初始化并接通页面路由后再开启",
    )
    literature_publish_interval_minutes: int = Field(
        default=360,
        ge=5,
        le=10080,
        validation_alias="DATA_RELEASE__LITERATURE_PUBLISH_INTERVAL_MINUTES",
        description="独立文献发布间隔，默认六小时",
    )

    pages_data_worker_enabled: bool = Field(
        default=False,
        validation_alias="GIDS_PAGES_DATA_WORKER",
        description="静态发布携带Pages同源数据Worker；用于未授予独立Workers Routes权限的部署",
    )

    reports_auto_publish_enabled: bool = Field(
        default=False,
        validation_alias="DATA_RELEASE__REPORTS_AUTO_PUBLISH_ENABLED",
        description="报告生成完成后自动发布报告，并在发布队列空闲时补触发",
    )
    batch_max_countries: int = Field(
        default=8,
        ge=1,
        le=500,
        validation_alias="DATA_RELEASE__BATCH_MAX_COUNTRIES",
        description="一次增量发布的国家/地区预算；旧父级标记作为完整依赖组发布",
    )
    enabled: bool = Field(default=True, description="是否启用 data release 调度")
    timezone: str = Field(default="UTC", description="默认 data release 调度时区")
    poll_interval_seconds: int = Field(default=30, ge=5, description="data release 轮询间隔秒数")
    auto_failure_cooldown_minutes: int = Field(
        default=720,
        ge=0,
        validation_alias="DATA_RELEASE__AUTO_FAILURE_COOLDOWN_MINUTES",
        description="自动触发 data release 失败后的冷却分钟数，0 表示不冷却",
    )
    auto_retry_max_attempts: int = Field(
        default=3,
        ge=0,
        le=10,
        validation_alias="DATA_RELEASE__AUTO_RETRY_MAX_ATTEMPTS",
        description="定时或上游触发发布发生可重试瞬时错误后的最大自动重试次数",
    )
    auto_retry_base_delay_seconds: int = Field(
        default=300,
        ge=5,
        le=86400,
        validation_alias="DATA_RELEASE__AUTO_RETRY_BASE_DELAY_SECONDS",
        description="自动发布第一次重试前的等待秒数；后续按 2 的幂指数退避",
    )
    auto_retry_max_delay_seconds: int = Field(
        default=3600,
        ge=5,
        le=86400,
        validation_alias="DATA_RELEASE__AUTO_RETRY_MAX_DELAY_SECONDS",
        description="自动发布指数退避的单次最长等待秒数",
    )
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
    pages_file_budget: int = Field(
        default=20_000,
        ge=1_000,
        le=100_000,
        validation_alias="DATA_RELEASE__PAGES_FILE_BUDGET",
        description="Cloudflare Pages 主站部署允许的最大文件数",
    )
    pages_file_warn: int = Field(
        default=14_000,
        ge=1_000,
        le=100_000,
        validation_alias="DATA_RELEASE__PAGES_FILE_WARN",
        description="Cloudflare Pages 主站文件数告警阈值，低于平台硬上限以保留增长余量",
    )
    r2_papers_enabled: bool = Field(
        default=True,
        validation_alias="DATA_RELEASE__R2_PAPERS_ENABLED",
        description="是否在站点发布时同步 papers 子域到 R2",
    )
    r2_papers_bucket: str = Field(
        default="globalid-papers",
        validation_alias="R2_PAPERS_BUCKET",
        description="Research 长尾页面所在的 R2 bucket",
    )
    r2_papers_public_base_url: str = Field(
        default="https://papers.globalinfectiousdisease.com",
        validation_alias="R2_PAPERS_PUBLIC_BASE_URL",
        description="papers 子域公开 base URL",
    )
    r2_reports_enabled: bool = Field(
        default=True,
        validation_alias="DATA_RELEASE__R2_REPORTS_ENABLED",
        description="是否在站点发布时同步 reports 子域到独立 R2 bucket",
    )
    r2_reports_bucket: str = Field(
        default="globalid-reports",
        validation_alias="R2_REPORTS_BUCKET",
        description="国家周期报告页面所在的 R2 bucket",
    )
    r2_reports_public_base_url: str = Field(
        default="https://reports.globalinfectiousdisease.com",
        validation_alias="R2_REPORTS_PUBLIC_BASE_URL",
        description="reports 子域公开 base URL",
    )
    r2_access_key_id: str = Field(
        default="",
        validation_alias="R2_ACCESS_KEY_ID",
        description="R2 S3 API access key id",
    )
    r2_secret_access_key: str = Field(
        default="",
        validation_alias="R2_SECRET_ACCESS_KEY",
        description="R2 S3 API secret access key",
    )
    r2_endpoint_url: str = Field(
        default="",
        validation_alias="R2_ENDPOINT_URL",
        description="Optional R2 S3 endpoint override",
    )
    r2_account_id: str = Field(
        default="",
        validation_alias="R2_ACCOUNT_ID",
        description="Optional R2 account id override; defaults to CLOUDFLARE_ACCOUNT_ID",
    )
    main_site_origin: str = Field(
        default="https://globalinfectiousdisease.com",
        validation_alias="DATA_RELEASE__MAIN_SITE_ORIGIN",
        description="主站绝对 origin，用于 papers HTML 根路径改写",
    )


class RawArchiveSettings(_BaseEnvSettings):
    """爬取原始数据的可读 Git 镜像配置。"""

    enabled: bool = Field(default=False, description="是否在数据更新任务后自动归档 data/raw")
    repo_url: str = Field(default="", description="原始数据专用 GitHub 仓库 URL")
    branch: str = Field(default="main", description="原始数据归档 GitHub 分支")
    repository_dir: Path = Field(
        default=Path("exports/raw-git-archive"),
        description="原始数据仓库 main 分支的持久本地克隆目录",
    )
    git_timeout_seconds: int = Field(default=1800, ge=60, le=7200, description="单次 Git 网络操作超时秒数")


class ReportSettings(BaseSettings):
    """报告配置"""

    model_config = SettingsConfigDict(env_prefix="")

    output_dir: str = Field(default="reports", description="报告输出目录")
    template_dir: str = Field(default="templates", description="模板目录")
    max_retries: int = Field(default=3, description="最大重试次数")
    max_parallel_tasks: int = Field(
        default=20,
        alias="MAX_PARALLEL_TASKS",
        description="AI并行任务数",
    )
    schedule_enabled: bool = Field(
        default=True,
        validation_alias="REPORT_SCHEDULE_ENABLED",
        description="是否根据日本试点监测数据的更新频率自动生成周期报告",
    )
    schedule_poll_interval_seconds: int = Field(
        default=900,
        ge=60,
        le=86400,
        validation_alias="REPORT_SCHEDULE_POLL_INTERVAL_SECONDS",
        description="检查新完整报告周期的间隔秒数",
    )
    schedule_max_tasks_per_tick: int = Field(
        default=3,
        ge=1,
        le=20,
        validation_alias="REPORT_SCHEDULE_MAX_TASKS_PER_TICK",
        description="每轮自动排入队列的报告数量上限",
    )
    schedule_max_active_tasks: int = Field(
        default=6,
        ge=1,
        le=50,
        validation_alias="REPORT_SCHEDULE_MAX_ACTIVE_TASKS",
        description="同时处于排队、运行或重试状态的自动报告任务上限",
    )
    schedule_min_records: int = Field(
        default=1,
        ge=1,
        validation_alias="REPORT_SCHEDULE_MIN_RECORDS",
        description="报告周期内至少需要的旧版生成器记录数",
    )


__all__ = [
    "DataReleaseSettings",
    "RawArchiveSettings",
    "ReportSettings",
]
