"""Domain-modularized settings models for GlobalID V2."""

from .ai import AISettings
from .base import PROJECT_ENV_FILE, PROJECT_ROOT, AppSettingsConfig, _BaseEnvSettings
from .infrastructure import DatabaseSettings, QdrantSettings, RedisSettings
from .ingestion import AutomationSettings, CrawlerSettings, LiteratureSettings, SituationSettings
from .release import DataReleaseSettings, RawArchiveSettings, ReportSettings
from .worker import TaskWorkerSettings

__all__ = [
    "PROJECT_ROOT",
    "PROJECT_ENV_FILE",
    "_BaseEnvSettings",
    "AppSettingsConfig",
    "DatabaseSettings",
    "RedisSettings",
    "QdrantSettings",
    "AISettings",
    "AutomationSettings",
    "DataReleaseSettings",
    "RawArchiveSettings",
    "SituationSettings",
    "LiteratureSettings",
    "ReportSettings",
    "CrawlerSettings",
    "TaskWorkerSettings",
]
