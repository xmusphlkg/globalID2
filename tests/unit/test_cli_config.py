"""The config command is an offline, credential-safe view of nested settings."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from src.cli.main import _connection_address, app


@pytest.fixture
def settings(monkeypatch):
    cfg = SimpleNamespace(
        app_name="TestGlobalID",
        version="1.2.3",
        app_env="testing",
        debug=False,
        database=SimpleNamespace(
            url="postgresql+asyncpg://db-user:db-secret@db.example:5432/private-db?password=query-secret#fragment-secret"
        ),
        redis=SimpleNamespace(url="redis://redis-user:redis-secret@redis.example:6379/7"),
        qdrant=SimpleNamespace(
            url="https://q-user:q-secret@vector.example:6333/private-path?api_key=url-secret",
            api_key="qdrant-secret",
        ),
        ai=SimpleNamespace(
            enable_cache=True,
            default_provider="custom",
            model_chain=["seed-model-a", "seed-model-b"],
            default_model="env-default-is-not-runtime",
            openai_api_key="openai-secret",
            anthropic_api_key="",
            glm_api_key="glm-secret",
            qianwen_api_key="",
            azure_api_key="azure-secret",
            custom_api_key="custom-secret",
        ),
    )
    monkeypatch.setattr("src.core.get_config", lambda: cfg)

    def forbid_connection(*args, **kwargs):
        pytest.fail("config must not connect to external services")

    for name in ("get_engine", "get_database", "get_cache"):
        monkeypatch.setattr(f"src.core.{name}", forbid_connection)
    return cfg


def test_config_reads_nested_settings_without_disclosing_credentials(settings):
    result = CliRunner().invoke(app, ["config"], env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.exception
    assert "db.example:5432" in result.output
    assert "redis.example:6379" in result.output
    assert "vector.example:6333" in result.output
    assert "首次初始化模型列表" in result.output
    assert "seed-model-a, seed-model-b" in result.output
    assert "由模型中心管理" in result.output
    assert "已启用" in result.output
    for hidden in (
        "secret", "db-user", "redis-user", "q-user", "private-db", "private-path",
        "env-default-is-not-runtime",
    ):
        assert hidden not in result.output


def test_config_empty_bootstrap_does_not_claim_an_active_model(settings):
    settings.ai.model_chain = []
    settings.ai.enable_cache = False
    result = CliRunner().invoke(app, ["config"], env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.exception
    assert "未设置；仅初始化提供商" in result.output
    assert "已关闭" in result.output
    assert "env-default-is-not-runtime" not in result.output


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", "未配置"),
        ("postgresql://user:p%40ss%3Aword@[::1]:5432/private", "[::1]:5432"),
        ("sqlite:///private-db.sqlite", "已配置（地址隐藏）"),
        ("https://user:secret@invalid.example:invalid/private", "已配置（地址隐藏）"),
        ("https://[malformed/secret", "已配置（地址隐藏）"),
    ],
)
def test_connection_address_hides_non_host_components(url, expected):
    assert _connection_address(url) == expected
