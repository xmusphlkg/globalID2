"""Automation, Situation, Research Radar (Literature), and Crawler ingestion settings."""

import json
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .base import _BaseEnvSettings


class AutomationSettings(_BaseEnvSettings):
    """数据流自动化与失败通知配置"""

    enabled: bool = Field(default=False, description="是否启用自动化调度")
    timezone: str = Field(default="UTC", description="自动化调度时区")
    poll_interval_seconds: int = Field(default=30, ge=5, description="自动化轮询间隔秒数")
    default_retry_threshold: int = Field(default=3, ge=1, le=20, description="失败告警触发阈值")
    auto_retry_base_delay_seconds: int = Field(
        default=300,
        ge=5,
        le=86400,
        description="定时采集瞬态失败首次自动重试延迟秒数",
    )
    auto_retry_max_delay_seconds: int = Field(
        default=3600,
        ge=5,
        le=604800,
        description="定时采集指数退避最大延迟秒数",
    )
    alert_group_cooldown_minutes: int = Field(
        default=360,
        ge=0,
        validation_alias="AUTOMATION__ALERT_GROUP_COOLDOWN_MINUTES",
        description="同类任务失败告警聚合冷却分钟数，0 表示不聚合",
    )
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


class SituationSettings(_BaseEnvSettings):
    """Scheduled Situation refresh: collect reports, read them and write alerts to the database."""

    schedule_enabled: bool = Field(default=True, description="Run the Situation refresh on the scheduler")
    interval_minutes: int = Field(
        default=30,
        ge=15,
        le=10080,
        description="Minutes between samples of each situation source, matching surveillance ingestion",
    )
    sample_pool_size: int = Field(
        default=2,
        ge=1,
        le=8,
        description="Situation sampling tasks that may be queued or running together",
    )
    catch_up_interval_minutes: int = Field(
        default=20,
        ge=5,
        le=240,
        description="Follow-up delay while reports are still waiting to be read",
    )
    poll_interval_seconds: int = Field(default=60, ge=5, le=600)
    timezone: str = Field(default="UTC")
    document_limit: int = Field(default=60, ge=1, le=500)
    ai_limit: int = Field(default=30, ge=1, le=500)


class LiteratureSettings(_BaseEnvSettings):
    """Research Radar source, classification, and scheduling settings."""

    enabled: bool = Field(default=True, description="Enable Research Radar APIs and manual synchronization")
    schedule_enabled: bool = Field(default=False, description="Run literature synchronization on the scheduler")
    interval_minutes: int = Field(
        default=15,
        ge=5,
        le=10080,
        description="Normal literature sync cadence; sized to stay ahead of the curated-journal arrival rate",
    )
    catch_up_enabled: bool = Field(
        default=True,
        description="Schedule an earlier bounded follow-up whenever the Crossref checkpoint is truncated",
    )
    catch_up_interval_minutes: int = Field(default=5, ge=1, le=60)
    catch_up_max_exception_backlog: int = Field(
        default=500,
        ge=1,
        le=100_000,
        description=(
            "Pause accelerated catch-up when distinct actionable review articles plus the next batch reach this limit; normal sync continues"
        ),
    )

    poll_interval_seconds: int = Field(default=30, ge=5, le=300)
    timezone: str = Field(default="UTC")
    contact_email: str = Field(default="research-radar@globalinfectiousdisease.com")
    journals_path: Path = Field(default=Path("configs/literature/journals.json"))
    taxonomy_path: Path = Field(default=Path("configs/literature/taxonomy.json"))
    disease_aliases_path: Path = Field(default=Path("configs/literature/disease_aliases.json"))
    initial_lookback_days: int = Field(default=14, ge=1, le=365)
    index_overlap_days: int = Field(
        default=0,
        ge=0,
        le=30,
        description=(
            "Deprecated compatibility setting; stable index-date checkpoints now continue at the "
            "committed watermark instead of replaying a multi-day window"
        ),
    )
    max_records_per_run: int = Field(default=300, ge=1, le=5000)
    controlled_discovery_enabled: bool = Field(
        default=True,
        description="Rotate bounded disease/pathogen/MeSH/vaccine/AMR queries during ordinary sync",
    )
    controlled_discovery_queries_per_run: int = Field(default=8, ge=1, le=50)
    controlled_discovery_records_per_query: int = Field(default=15, ge=1, le=100)
    controlled_discovery_max_records_per_run: int = Field(default=120, ge=2, le=1000)
    controlled_discovery_max_terms_per_query: int = Field(default=8, ge=1, le=20)
    max_europe_pmc_records: int = Field(default=200, ge=0, le=2000)
    europe_pmc_enabled: bool = Field(default=True)
    pubmed_enabled: bool = Field(
        default=True,
        description="Use PubMed E-utilities as bounded fallback and secondary biomedical discovery",
    )
    pubmed_api_key: str = Field(default="", description="Optional NCBI API key for higher PubMed E-utilities limits")
    pubmed_tool: str = Field(default="GIDSResearchRadar", max_length=80)
    max_pubmed_records: int = Field(default=200, ge=0, le=2000)
    pubmed_min_interval_seconds: float = Field(
        default=0.34,
        ge=0.0,
        le=10.0,
        description="Minimum interval between PubMed E-utilities requests; 0.34 stays under the no-key NCBI limit",
    )
    official_guidance_enabled: bool = Field(
        default=True,
        description="Discover licensed public-health guidance metadata from WHO IRIS OAI-PMH",
    )
    official_guidance_oai_endpoint: str = Field(
        default="https://iris.who.int/server/oai/request",
        description="Reviewed WHO IRIS OAI-PMH endpoint; the client rejects other hosts and paths",
    )
    max_official_guidance_records: int = Field(default=60, ge=1, le=500)
    springer_nature_enabled: bool = Field(
        default=False,
        description="Discover bounded article metadata through the credential-gated Springer Nature API",
    )
    springer_nature_api_key: str = Field(default="", description="Springer Nature Metadata API key")
    springer_nature_query: str = Field(
        default="infectious disease OR outbreak OR surveillance OR vaccine OR antimicrobial resistance",
        max_length=1_000,
    )
    max_springer_nature_records: int = Field(default=50, ge=1, le=500)
    elsevier_enabled: bool = Field(
        default=False,
        description="Discover bounded Scopus metadata through the credential-gated Elsevier API",
    )
    elsevier_api_key: str = Field(default="", description="Elsevier developer API key")
    elsevier_institutional_token: str = Field(
        default="",
        description="Optional Elsevier institutional token; never persisted in checkpoints",
    )
    elsevier_query: str = Field(
        default=(
            'TITLE-ABS-KEY("infectious disease" OR outbreak OR surveillance OR vaccine '
            'OR "antimicrobial resistance")'
        ),
        max_length=1_000,
    )
    max_elsevier_records: int = Field(default=50, ge=1, le=500)
    preprint_discovery_enabled: bool = Field(
        default=False,
        description="Discover bioRxiv/medRxiv metadata; every result is held for editorial review",
    )
    max_preprint_records: int = Field(default=100, ge=1, le=500)
    publisher_rss_enabled: bool = Field(
        default=False,
        description="Poll the explicitly trusted publisher RSS/Atom whitelist for Online First metadata",
    )
    publisher_rss_feeds_path: Path = Field(default=Path("configs/literature/publisher_feeds.json"))
    max_publisher_rss_records: int = Field(default=50, ge=1, le=500)
    publisher_rss_concurrency: int = Field(default=3, ge=1, le=8)
    publisher_rss_max_feed_bytes: int = Field(default=2_000_000, ge=16_384, le=10_000_000)
    publisher_rss_seen_id_limit: int = Field(default=2_000, ge=100, le=20_000)
    max_openalex_records: int = Field(default=300, ge=0, le=5000)
    openalex_enabled: bool = Field(default=True)
    openalex_api_key: str = Field(default="", description="Optional OpenAlex API key for a larger daily budget")
    openalex_batch_size: int = Field(default=100, ge=1, le=100)
    max_unpaywall_records: int = Field(default=300, ge=0, le=5000)
    unpaywall_enabled: bool = Field(default=True)
    metadata_enrichment_concurrency: int = Field(default=3, ge=1, le=12)
    metadata_enrichment_min_interval_seconds: float = Field(default=0.05, ge=0.0, le=10.0)
    metadata_backfill_openalex_target: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Coverage target used by bounded resumable OpenAlex backfill runs",
    )
    metadata_backfill_unpaywall_target: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Coverage target used by bounded resumable Unpaywall backfill runs",
    )
    source_concurrency: int = Field(
        default=2,
        ge=1,
        le=12,
        description="Concurrent requests per literature provider; kept conservative for Crossref TLS stability",
    )
    request_timeout_seconds: int = Field(default=30, ge=5, le=120)
    max_retries: int = Field(default=3, ge=1, le=5)
    persistence_deadlock_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Retry a rolled-back Research Radar persistence transaction after PostgreSQL deadlocks",
    )
    persistence_deadlock_retry_base_seconds: float = Field(
        default=0.25,
        ge=0.01,
        le=10.0,
        description="Initial exponential backoff for Research Radar deadlock retries",
    )
    persistence_batch_size: int = Field(
        default=25,
        ge=1,
        le=500,
        description="Maximum Research Radar articles committed in one database transaction",
    )
    auto_publish_min_score: float = Field(default=0.72, ge=0.0, le=1.0)
    public_article_limit: int = Field(default=500, ge=20, le=5000)
    ai_enrichment_enabled: bool = Field(
        default=False,
        description="Allow model-backed, evidence-grounded literature summary tasks",
    )
    ai_enrichment_schedule_enabled: bool = Field(
        default=False,
        description="Continuously process the next eligible summary batch on the scheduler",
    )
    ai_enrichment_auto_on_ingest: bool = Field(
        default=True,
        description="Queue a targeted evidence-summary task for newly inserted literature after each sync",
    )
    ai_enrichment_interval_minutes: int = Field(default=15, ge=15, le=10080)
    ai_enrichment_catch_up_interval_minutes: int = Field(
        default=1,
        ge=1,
        le=1440,
        description="Accelerated follow-up cadence while Research Radar AI summary batches are still full",
    )
    ai_enrichment_batch_size: int = Field(default=50, ge=1, le=50)
    ai_enrichment_concurrency: int = Field(
        default=8,
        ge=1,
        le=24,
        description="Maximum article-level concurrency within one Research Radar AI enrichment task",
    )
    ai_enrichment_candidate_scan_limit: int = Field(
        default=10000,
        ge=100,
        le=100000,
        description="Maximum eligible article candidates scanned to fill one AI enrichment batch",
    )
    ai_max_quality_attempts: int = Field(
        default=5,
        ge=1,
        le=5,
        description="Maximum automatic generations per unchanged article/language before exception review",
    )
    ai_enrichment_languages_raw: str = Field(default="en,zh,fr")
    abstract_repair_enabled: bool = True
    abstract_repair_controller_enabled: bool = True
    abstract_repair_batch_size: int = Field(default=200, ge=1, le=500)
    abstract_repair_retry_after_hours: int = Field(default=168, ge=1)
    ai_min_abstract_characters: int = Field(default=180, ge=80, le=2000)
    ai_require_open_access: bool = Field(
        default=False,
        description="Send any eligible abstract to an external model route, regardless of open-access status",
    )
    ai_model_request_timeout_seconds: int = Field(default=420, ge=10, le=600)
    weekly_ai_review_enabled: bool = Field(
        default=False,
        description="Run a distinct, content-bound AI quality review for generated weekly briefs",
    )
    weekly_ai_review_batch_size: int = Field(default=2, ge=1, le=8)
    weekly_ai_review_timeout_seconds: int = Field(default=60, ge=10, le=180)
    weekly_ai_review_max_attempts: int = Field(default=2, ge=1, le=2)
    weekly_ai_review_recheck_hours: int = Field(
        default=6,
        ge=1,
        le=168,
        description=(
            "Minimum delay before automatically re-reviewing an unchanged weekly brief "
            "that AI previously held for editorial attention"
        ),
    )
    gap_discovery_enabled: bool = Field(
        default=True,
        description="Enable review-only targeted literature discovery for Situation Room evidence gaps",
    )
    gap_discovery_schedule_enabled: bool = Field(default=False)
    gap_discovery_interval_minutes: int = Field(default=720, ge=60, le=10080)
    gap_discovery_max_gaps_per_run: int = Field(default=8, ge=1, le=50)
    gap_discovery_records_per_gap: int = Field(default=25, ge=5, le=100)
    gap_discovery_candidate_limit: int = Field(
        default=12,
        ge=1,
        le=50,
        description="Maximum ranked review relationships retained per evidence gap and discovery run",
    )
    gap_discovery_lookback_days: int = Field(default=730, ge=30, le=3650)
    gap_discovery_retry_hours: int = Field(default=24, ge=1, le=720)
    autopilot_enabled: bool = Field(
        default=False,
        description="Apply versioned quality gates and leave only exceptions for editorial review",
    )
    autopilot_article_min_score: float = Field(default=0.70, ge=0.0, le=1.0)
    autopilot_article_exclude_below_score: float = Field(default=0.60, ge=0.0, le=1.0)
    autopilot_disease_min_confidence: float = Field(default=0.82, ge=0.0, le=1.0)
    autopilot_exact_relation_min_confidence: float = Field(default=0.78, ge=0.0, le=1.0)
    autopilot_context_relation_min_confidence: float = Field(default=0.82, ge=0.0, le=1.0)
    autopilot_summary_min_quality: float = Field(default=0.90, ge=0.0, le=1.0)
    autopilot_reconcile_batch_size: int = Field(
        default=100,
        ge=1,
        le=5000,
        description="Maximum articles or summaries held in memory per autopilot reconciliation batch",
    )
    autopilot_statement_timeout_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        description="PostgreSQL statement timeout applied locally to one autopilot transaction",
    )
    autopilot_lock_timeout_seconds: int = Field(
        default=15,
        ge=1,
        le=300,
        description="PostgreSQL lock wait timeout applied locally to one autopilot transaction",
    )
    payload_compaction_enabled: bool = Field(
        default=True,
        description="Incrementally compact legacy provider payloads after successful ingest writes",
    )
    payload_compaction_batch_size: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum legacy payload rows compacted in one bounded maintenance transaction",
    )
    autopilot_auto_reject_weak_links: bool = Field(default=True)
    autopilot_auto_exclude_incomplete: bool = Field(default=True)
    autopilot_auto_exclude_preprints: bool = Field(default=True)

    @property
    def ai_enrichment_languages(self) -> list[str]:
        languages = [value.strip().lower() for value in self.ai_enrichment_languages_raw.split(",")]
        return [value for value in dict.fromkeys(languages) if value in {"en", "zh", "fr"}] or ["en"]


class CrawlerSettings(BaseSettings):
    """爬虫配置"""

    model_config = SettingsConfigDict(env_prefix="")

    max_concurrent: int = Field(
        default=2,
        alias="MAX_CRAWLER_CONCURRENT",
        description="爬虫并发数",
    )
    timeout: int = Field(default=30, description="请求超时时间（秒）")
    max_retries: int = Field(default=3, description="最大重试次数")
    delay: float = Field(default=1.0, description="请求延迟（秒）")


__all__ = [
    "AutomationSettings",
    "SituationSettings",
    "LiteratureSettings",
    "CrawlerSettings",
]
