import pytest

from src.core import source_credentials as credentials
from src.services.automation_service import AutomationJobConfig, _ingestion_job_health


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_SERVICE_KEY", raising=False)
    monkeypatch.delenv("KDCA_EID_SERVICE_KEY", raising=False)
    monkeypatch.setattr(credentials, "get_country_bootstrap_config", lambda _: {})


@pytest.mark.parametrize("source", ["kdca_death_annual", "kdca_region_annual"])
def test_annual_sources_need_credentials_but_remain_visible_as_needing_review(source):
    job = AutomationJobConfig(job_id="kr-test", name="Korea", country_code="KR", source=source)
    health = _ingestion_job_health(job, None, None)
    assert health["health_status"] == "needs_review"
    assert "DATA_GO_KR_SERVICE_KEY" in health["health_reason"]
    job.enabled = False
    assert _ingestion_job_health(job, None, None)["health_status"] == "disabled"


@pytest.mark.parametrize("source", ["kdca_open_api", "all", None])
def test_monthly_portal_fallback_is_not_blocked(source):
    assert credentials.source_credential_blocker("KR", source) is None


@pytest.mark.parametrize("name", ["DATA_GO_KR_SERVICE_KEY", "KDCA_EID_SERVICE_KEY", "CUSTOM_KDCA_KEY"])
def test_scheduler_and_crawler_accept_the_same_credential(monkeypatch, name):
    from src.data.crawlers import kr

    config = {"crawler_config": {"service_key_env": "CUSTOM_KDCA_KEY"}} if name.startswith("CUSTOM") else {}
    monkeypatch.setattr(credentials, "get_country_bootstrap_config", lambda _: config)
    monkeypatch.setattr(kr, "get_country_bootstrap_config", lambda _: config)
    monkeypatch.setenv(name, " test%2Bkey%3D ")
    assert credentials.source_credential_blocker("KR", "kdca_death_annual") is None
    crawler = kr.KoreaKDCAOpenAPICrawler()
    try:
        assert crawler.service_key == "test+key="
    finally:
        crawler.session.close()


def test_whitespace_key_does_not_block_a_valid_legacy_key(monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "  ")
    assert credentials.source_credential_blocker("KR", "kdca_region_annual")
    monkeypatch.setenv("KDCA_EID_SERVICE_KEY", "legacy")
    assert credentials.kdca_service_key() == "legacy"


def test_unrelated_country_has_no_kdca_requirement():
    assert credentials.source_credential_blocker("US", "kdca_region_annual") is None
