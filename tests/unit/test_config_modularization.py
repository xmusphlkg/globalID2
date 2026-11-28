"""Unit tests for modularized core configuration under src/core/settings/."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core import config as config_module
from src.core import settings as settings_pkg
from src.core.config import (
    PROJECT_ENV_FILE,
    PROJECT_ROOT,
    AISettings,
    AppSettings,
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
    get_config,
)
from src.core.settings import ai as ai_module
from src.core.settings import base as base_module
from src.core.settings import infrastructure as infrastructure_module
from src.core.settings import ingestion as ingestion_module
from src.core.settings import release as release_module
from src.core.settings import worker as worker_module


def test_project_root_resolves_to_repository_root() -> None:
    expected_root = Path(__file__).resolve().parents[2]
    assert PROJECT_ROOT == expected_root
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert (PROJECT_ROOT / "src").is_dir()
    assert PROJECT_ENV_FILE == PROJECT_ROOT / ".env"
    assert base_module.PROJECT_ROOT == expected_root


def test_reexported_settings_classes_are_identical() -> None:
    symbol_pairs = [
        ("PROJECT_ROOT", PROJECT_ROOT, settings_pkg.PROJECT_ROOT, base_module.PROJECT_ROOT),
        ("PROJECT_ENV_FILE", PROJECT_ENV_FILE, settings_pkg.PROJECT_ENV_FILE, base_module.PROJECT_ENV_FILE),
        ("_BaseEnvSettings", _BaseEnvSettings, settings_pkg._BaseEnvSettings, base_module._BaseEnvSettings),
        ("AppSettingsConfig", AppSettingsConfig, settings_pkg.AppSettingsConfig, base_module.AppSettingsConfig),
        ("DatabaseSettings", DatabaseSettings, settings_pkg.DatabaseSettings, infrastructure_module.DatabaseSettings),
        ("RedisSettings", RedisSettings, settings_pkg.RedisSettings, infrastructure_module.RedisSettings),
        ("QdrantSettings", QdrantSettings, settings_pkg.QdrantSettings, infrastructure_module.QdrantSettings),
        ("AISettings", AISettings, settings_pkg.AISettings, ai_module.AISettings),
        (
            "DataReleaseSettings",
            DataReleaseSettings,
            settings_pkg.DataReleaseSettings,
            release_module.DataReleaseSettings,
        ),
        (
            "RawArchiveSettings",
            RawArchiveSettings,
            settings_pkg.RawArchiveSettings,
            release_module.RawArchiveSettings,
        ),
        ("ReportSettings", ReportSettings, settings_pkg.ReportSettings, release_module.ReportSettings),
        (
            "AutomationSettings",
            AutomationSettings,
            settings_pkg.AutomationSettings,
            ingestion_module.AutomationSettings,
        ),
        (
            "SituationSettings",
            SituationSettings,
            settings_pkg.SituationSettings,
            ingestion_module.SituationSettings,
        ),
        (
            "LiteratureSettings",
            LiteratureSettings,
            settings_pkg.LiteratureSettings,
            ingestion_module.LiteratureSettings,
        ),
        ("CrawlerSettings", CrawlerSettings, settings_pkg.CrawlerSettings, ingestion_module.CrawlerSettings),
        ("TaskWorkerSettings", TaskWorkerSettings, settings_pkg.TaskWorkerSettings, worker_module.TaskWorkerSettings),
    ]

    for name, from_config, from_pkg, from_domain in symbol_pairs:
        assert from_config is from_pkg, f"{name} differs between src.core.config and src.core.settings"
        assert from_config is from_domain, f"{name} differs between src.core.config and domain module"
        assert name in config_module.__all__, f"{name} missing from src.core.config.__all__"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("release", False),
        ("prod", False),
        ("production", False),
        ("false", False),
        ("0", False),
        ("off", False),
        ("no", False),
        ("debug", True),
        ("dev", True),
        ("development", True),
        ("true", True),
        ("1", True),
        ("on", True),
        ("yes", True),
    ],
)
def test_app_settings_debug_normalization(tmp_path: Path, raw_value: str, expected: bool) -> None:
    cfg = AppSettings(
        _env_file=None,
        debug=raw_value,
        log_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
        raw_data_dir=tmp_path / "data" / "raw",
        processed_data_dir=tmp_path / "data" / "processed",
        cache_dir=tmp_path / "data" / "cache",
    )
    assert cfg.debug is expected
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "data" / "cache").is_dir()


def test_app_settings_nested_ai_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI__DEFAULT_PROVIDER", "openai")
    monkeypatch.setenv("AI__DEFAULT_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("AI__TEMPERATURE", "0.3")
    monkeypatch.setenv("AI__KNOWLEDGE_AUTOMATION_BATCH_SIZE", "9")
    monkeypatch.setenv("AI__IDLE_BACKLOG_SLICE_SIZE", "120")

    cfg = AppSettings(
        _env_file=None,
        log_dir=tmp_path / "logs",
        data_dir=tmp_path / "data",
        raw_data_dir=tmp_path / "data" / "raw",
        processed_data_dir=tmp_path / "data" / "processed",
        cache_dir=tmp_path / "data" / "cache",
    )

    assert cfg.ai.default_provider == "openai"
    assert cfg.ai.default_model == "gpt-4o-mini"
    assert cfg.ai.temperature == 0.3
    assert cfg.ai.knowledge_automation_batch_size == 9
    assert cfg.ai.idle_backlog_slice_size == 120


def test_ai_settings_model_chain_and_agent_role_models() -> None:
    empty_ai = AISettings(_env_file=None, model_chain_raw="", agent_role_models_raw="")
    assert empty_ai.model_chain == []
    assert empty_ai.agent_role_models == {}

    chained_ai = AISettings(
        _env_file=None,
        model_chain_raw=" gpt-4o-mini , glm-4-plus , gpt-4o-mini ,  , claude-3-5-sonnet ",
    )
    assert chained_ai.model_chain == ["gpt-4o-mini", "glm-4-plus", "claude-3-5-sonnet"]

    json_roles_ai = AISettings(
        _env_file=None,
        agent_role_models_raw='{"Planner": ["gpt-4o-mini", "gpt-4o"], "reviewer": "glm-4-plus|glm-4-7"}',
    )
    assert json_roles_ai.agent_role_models == {
        "planner": ["gpt-4o-mini", "gpt-4o"],
        "reviewer": ["glm-4-plus", "glm-4-7"],
    }

    compact_roles_ai = AISettings(
        _env_file=None,
        agent_role_models_raw="Planner=gpt-4o-mini|gpt-4o; reviewer:glm-4-plus , invalid_chunk",
    )
    assert compact_roles_ai.agent_role_models == {
        "planner": ["gpt-4o-mini", "gpt-4o"],
        "reviewer": ["glm-4-plus"],
    }


def test_literature_ai_enrichment_languages_property() -> None:
    default_lit = LiteratureSettings(_env_file=None, ai_enrichment_languages_raw="EN, zh, fr, en, de, ")
    assert default_lit.ai_enrichment_languages == ["en", "zh", "fr"]

    fallback_lit = LiteratureSettings(_env_file=None, ai_enrichment_languages_raw="es,jp")
    assert fallback_lit.ai_enrichment_languages == ["en"]


def test_automation_admin_emails_and_jobs_properties() -> None:
    auto = AutomationSettings(
        _env_file=None,
        admin_emails_raw=" ops@example.com , , dev@example.com ",
        jobs_json='[{"id": "daily-sync", "cron": "0 2 * * *"}, "ignore-non-dict", {"id": "weekly"}]',
    )
    assert auto.admin_emails == ["ops@example.com", "dev@example.com"]
    assert auto.jobs == [{"id": "daily-sync", "cron": "0 2 * * *"}, {"id": "weekly"}]

    bad_json_auto = AutomationSettings(_env_file=None, jobs_json="{not-json")
    assert bad_json_auto.jobs == []

    non_list_auto = AutomationSettings(_env_file=None, jobs_json='{"id": "not-a-list"}')
    assert non_list_auto.jobs == []


def test_get_config_caching_and_cache_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    get_config.cache_clear()
    try:
        first = get_config()
        second = get_config()
        assert first is second

        monkeypatch.setenv("APP_NAME", "GlobalID-Modular-Test")
        still_cached = get_config()
        assert still_cached is first

        get_config.cache_clear()
        reloaded = get_config()
        assert reloaded is not first
        assert reloaded.app_name == "GlobalID-Modular-Test"
    finally:
        get_config.cache_clear()
