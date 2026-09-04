import json
from types import SimpleNamespace

import httpx
import pytest

from src.domain import TaskStatus, TaskType
from src.literature.clients.crossref import CrossrefIncrementalResult
from src.literature.clients.pubmed import PubMedResult
from src.literature.pipeline import LiteraturePipeline, _hold_degraded_enrichment_for_review
from src.literature.types import ArticleCandidate, Classification
from src.services import _lifecycle as lifecycle_module


def _candidate() -> ArticleCandidate:
    return ArticleCandidate(
        article_id="lit_provider_failure",
        slug="provider-failure",
        title="Dengue surveillance in Japan",
        doi="10.1000/provider-failure",
    )


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        europe_pmc_enabled=False,
        max_europe_pmc_records=10,
        unpaywall_enabled=True,
        max_unpaywall_records=10,
        openalex_enabled=True,
        max_openalex_records=10,
        openalex_api_key="",
        openalex_batch_size=100,
        metadata_enrichment_concurrency=2,
        metadata_enrichment_min_interval_seconds=0,
        contact_email="tests@example.org",
        request_timeout_seconds=5,
        max_retries=1,
    )


class _SuccessfulUnpaywall:
    calls = 0

    def __init__(self, **_kwargs) -> None:
        pass

    async def enrich_by_dois(self, dois, **_kwargs):
        type(self).calls += 1
        return {
            doi: {
                "doi": doi,
                "is_oa": True,
                "best_oa_location": {"url": "https://repository.example.org/article"},
            }
            for doi in dois
        }


class _SuccessfulOpenAlex:
    calls = 0

    def __init__(self, **_kwargs) -> None:
        pass

    async def enrich_by_dois(self, dois, **_kwargs):
        type(self).calls += 1
        return {
            doi: {
                "id": "https://openalex.org/W123",
                "doi": f"https://doi.org/{doi}",
                "open_access": {"is_oa": True},
            }
            for doi in dois
        }


class _FailingUnpaywall:
    calls = 0

    def __init__(self, **_kwargs) -> None:
        pass

    async def enrich_by_dois(self, _dois, **_kwargs):
        type(self).calls += 1
        raise httpx.ConnectError("", request=httpx.Request("GET", "https://api.unpaywall.example/v2"))


class _FailingOpenAlex:
    calls = 0

    def __init__(self, **_kwargs) -> None:
        pass

    async def enrich_by_dois(self, _dois, **_kwargs):
        type(self).calls += 1
        raise httpx.ConnectError("", request=httpx.Request("GET", "https://api.openalex.example/works"))


@pytest.mark.parametrize(
    ("unpaywall_class", "openalex_class", "failed_providers", "expected_oa", "expected_openalex"),
    [
        (_SuccessfulUnpaywall, _FailingOpenAlex, ["openalex"], "open", None),
        (_FailingUnpaywall, _SuccessfulOpenAlex, ["unpaywall"], "open", "W123"),
        (_FailingUnpaywall, _FailingOpenAlex, ["unpaywall", "openalex"], "unknown", None),
    ],
)
async def test_optional_provider_failures_are_isolated_and_other_providers_continue(
    monkeypatch,
    unpaywall_class,
    openalex_class,
    failed_providers,
    expected_oa,
    expected_openalex,
):
    for provider_class in (_SuccessfulUnpaywall, _SuccessfulOpenAlex, _FailingUnpaywall, _FailingOpenAlex):
        provider_class.calls = 0
    monkeypatch.setattr("src.literature.pipeline.UnpaywallClient", unpaywall_class)
    monkeypatch.setattr("src.literature.pipeline.OpenAlexClient", openalex_class)
    candidate = _candidate()

    counts = await LiteraturePipeline(_config())._enrich_candidates([candidate])

    assert counts["enrichment_errors"] == len(failed_providers)
    assert counts["enrichment_failed_providers"] == failed_providers
    assert counts["unpaywall_errors"] == int("unpaywall" in failed_providers)
    assert counts["openalex_errors"] == int("openalex" in failed_providers)
    assert counts["europe_pmc_errors"] == 0
    assert candidate.open_access_status == expected_oa
    assert candidate.openalex_id == expected_openalex
    assert unpaywall_class.calls == 1
    assert openalex_class.calls == 1
    serialized_counts = json.dumps(counts)
    assert "ConnectError" not in serialized_counts
    assert "http" not in serialized_counts


def test_degraded_enrichment_holds_publishable_thin_candidate_for_review():
    classification = Classification(publication_status="published")
    assert _hold_degraded_enrichment_for_review(classification, enrichment_degraded=True) is True
    assert classification.publication_status == "review"

    excluded = Classification(publication_status="excluded")
    assert _hold_degraded_enrichment_for_review(excluded, enrichment_degraded=True) is False
    assert excluded.publication_status == "excluded"


async def test_crossref_connect_failure_still_fails_the_whole_run(monkeypatch, tmp_path):
    journals = tmp_path / "journals.json"
    journals.write_text(
        json.dumps({"journals": [{"name": "Test", "issn": "1234-5678"}]}),
        encoding="utf-8",
    )
    config = SimpleNamespace(
        journals_path=journals,
        contact_email="tests@example.org",
        request_timeout_seconds=5,
        max_retries=1,
        max_records_per_run=10,
        source_concurrency=1,
    )

    class FailingCrossref:
        def __init__(self, **_kwargs) -> None:
            pass

        async def fetch_incremental(self, **_kwargs):
            raise httpx.ConnectError("", request=httpx.Request("GET", "https://api.crossref.example/works"))

    pipeline = LiteraturePipeline(config)
    finished = []

    async def resolve_start(_now, _task):
        from datetime import datetime, timezone
        return datetime(2026, 8, 1, tzinfo=timezone.utc), None

    async def create_run(*_args, **_kwargs):
        return None

    async def finish_run(_run_uuid, status, **kwargs):
        finished.append((status, kwargs))

    monkeypatch.setattr("src.literature.pipeline.CrossrefClient", FailingCrossref)
    monkeypatch.setattr(pipeline, "_resolve_start", resolve_start)
    monkeypatch.setattr(pipeline, "_create_run", create_run)
    monkeypatch.setattr(pipeline, "_finish_run", finish_run)

    with pytest.raises(httpx.ConnectError):
        await pipeline.execute()
    assert finished == [("failed", {"error": "ConnectError"})]


async def test_crossref_connect_failure_uses_pubmed_fallback_without_advancing_crossref_checkpoint(
    monkeypatch,
    tmp_path,
):
    journals = tmp_path / "journals.json"
    journals.write_text(
        json.dumps({"journals": [{"name": "Test Journal", "issn": "1234-5678"}]}),
        encoding="utf-8",
    )
    config = SimpleNamespace(
        journals_path=journals,
        taxonomy_path="configs/literature/taxonomy.json",
        index_overlap_days=0,
        contact_email="tests@example.org",
        request_timeout_seconds=5,
        max_retries=1,
        max_records_per_run=10,
        max_pubmed_records=10,
        source_concurrency=1,
        pubmed_enabled=True,
        pubmed_api_key="",
        pubmed_tool="GIDSTest",
        pubmed_min_interval_seconds=0,
        europe_pmc_enabled=False,
        controlled_discovery_enabled=False,
        official_guidance_enabled=False,
        springer_nature_enabled=False,
        elsevier_enabled=False,
        preprint_discovery_enabled=False,
        publisher_rss_enabled=False,
        unpaywall_enabled=False,
        openalex_enabled=False,
        autopilot_enabled=False,
        auto_publish_min_score=0.72,
    )

    class FailingCrossref:
        def __init__(self, **_kwargs) -> None:
            pass

        async def fetch_incremental(self, **_kwargs):
            raise httpx.ConnectError("", request=httpx.Request("GET", "https://api.crossref.example/works"))

    class FallbackPubMed:
        def __init__(self, **_kwargs) -> None:
            pass

        async def fetch_incremental(self, **_kwargs):
            return PubMedResult(
                records=[{
                    "uid": "123",
                    "title": "Dengue surveillance",
                    "pubdate": "2026",
                    "articleids": [{"idtype": "doi", "value": "10.1000/pubmed-fallback"}],
                }],
                checkpoint={"provider": "pubmed", "records_returned": 1, "truncated": False},
            )

    class FakeDb:
        async def commit(self):
            return None

    class FakeDbContext:
        async def __aenter__(self):
            return FakeDb()

        async def __aexit__(self, *_args):
            return False

    class FakeRepository:
        def __init__(self, _db) -> None:
            pass

        async def upsert(self, *_args, **_kwargs):
            return True

    pipeline = LiteraturePipeline(config)
    finished = []

    async def resolve_start(_now, _task):
        from datetime import datetime, timezone
        return datetime(2026, 8, 1, tzinfo=timezone.utc), None

    async def create_run(*_args, **_kwargs):
        return None

    async def finish_run(_run_uuid, status, **kwargs):
        finished.append((status, kwargs))

    async def classify_catalogues():
        return [], []

    async def enrich_candidates(_candidates):
        return {
            "europe_pmc_enriched": 0,
            "unpaywall_enriched": 0,
            "openalex_enriched": 0,
            "europe_pmc_errors": 0,
            "unpaywall_errors": 0,
            "openalex_errors": 0,
            "enrichment_errors": 0,
            "enrichment_failed_providers": [],
            "enrichment_degraded_review": 0,
        }

    monkeypatch.setattr("src.literature.pipeline.CrossrefClient", FailingCrossref)
    monkeypatch.setattr("src.literature.pipeline.PubMedClient", FallbackPubMed)
    monkeypatch.setattr("src.literature.pipeline.get_db", lambda: FakeDbContext())
    monkeypatch.setattr("src.literature.pipeline.LiteratureRepository", FakeRepository)
    monkeypatch.setattr("src.literature.pipeline.classify_candidate", lambda *_, **__: Classification())
    monkeypatch.setattr(pipeline, "_resolve_start", resolve_start)
    monkeypatch.setattr(pipeline, "_create_run", create_run)
    monkeypatch.setattr(pipeline, "_finish_run", finish_run)
    monkeypatch.setattr(pipeline, "_classification_catalogues", classify_catalogues)
    monkeypatch.setattr(pipeline, "_enrich_candidates", enrich_candidates)

    result = await pipeline.execute()

    assert result["pubmed_core_fallback_used"] == 1
    assert result["crossref_source_errors"] == 1
    assert result["through_indexed_at"] == "2026-08-01T00:00:00+00:00"
    assert finished[0][0] == "completed"
    assert finished[0][1]["checkpoint"]["strategy"] == "pubmed-fallback-no-crossref-advance-v1"
    assert finished[0][1]["source"].startswith("pubmed-fallback+pubmed")


async def test_full_pipeline_autopilot_cannot_publish_crossref_record_when_openalex_is_down(
    monkeypatch,
    tmp_path,
):
    journals = tmp_path / "journals.json"
    taxonomy = tmp_path / "taxonomy.json"
    journals.write_text(
        json.dumps({"journals": [{"name": "Test", "issn": "1234-5678"}]}),
        encoding="utf-8",
    )
    taxonomy.write_text(
        json.dumps({"topics": {"Surveillance": ["surveillance"]}, "study_types": {}}),
        encoding="utf-8",
    )
    config = SimpleNamespace(
        journals_path=journals,
        taxonomy_path=taxonomy,
        contact_email="tests@example.org",
        request_timeout_seconds=5,
        max_retries=1,
        max_records_per_run=10,
        source_concurrency=1,
        europe_pmc_enabled=False,
        unpaywall_enabled=True,
        max_unpaywall_records=10,
        openalex_enabled=True,
        max_openalex_records=10,
        openalex_api_key="",
        openalex_batch_size=100,
        metadata_enrichment_concurrency=1,
        metadata_enrichment_min_interval_seconds=0,
        controlled_discovery_enabled=False,
        official_guidance_enabled=False,
        publisher_rss_enabled=False,
        auto_publish_min_score=0,
        autopilot_enabled=True,
    )

    class SuccessfulCrossref:
        def __init__(self, **_kwargs) -> None:
            pass

        async def fetch_incremental(self, **kwargs):
            return CrossrefIncrementalResult(
                records=[{
                    "DOI": "10.1000/thin-crossref",
                    "title": ["Dengue surveillance in Japan"],
                    "published-online": {"date-parts": [[2026, 8, 17]]},
                    "indexed": {"date-time": "2026-08-17T00:00:00Z"},
                }],
                checkpoint={
                    "through_indexed_at": kwargs["until"].isoformat(),
                    "records_seen": 1,
                    "records_returned": 1,
                    "truncated": False,
                },
            )

    captured = {}

    class CapturingRepository:
        def __init__(self, _db) -> None:
            pass

        async def upsert(self, candidate, classification, **kwargs):
            captured["candidate"] = candidate
            captured["classification"] = classification
            captured["kwargs"] = kwargs
            return True

    class FakeDb:
        async def commit(self):
            return None

    class FakeDbContext:
        async def __aenter__(self):
            return FakeDb()

        async def __aexit__(self, _exc_type, _exc, _traceback):
            return False

    pipeline = LiteraturePipeline(config)
    finished = []

    async def resolve_start(_now, _task):
        from datetime import datetime, timezone
        return datetime(2026, 8, 1, tzinfo=timezone.utc), None

    async def create_run(*_args, **_kwargs):
        return None

    async def finish_run(_run_uuid, status, **kwargs):
        finished.append((status, kwargs))

    async def catalogues():
        return (
            [{"disease_id": "D021", "name_en": "Dengue", "name_zh": "登革热", "aliases": []}],
            [{"code": "JP", "name": "Japan", "name_en": "Japan", "name_zh": "日本"}],
        )

    async def unexpected_reconcile():
        raise AssertionError("degraded enrichment must not invoke autopilot reconcile")

    from src.services.literature_automation_service import literature_automation_service

    monkeypatch.setattr("src.literature.pipeline.CrossrefClient", SuccessfulCrossref)
    monkeypatch.setattr("src.literature.pipeline.UnpaywallClient", _SuccessfulUnpaywall)
    monkeypatch.setattr("src.literature.pipeline.OpenAlexClient", _FailingOpenAlex)
    monkeypatch.setattr("src.literature.pipeline.LiteratureRepository", CapturingRepository)
    monkeypatch.setattr("src.literature.pipeline.get_db", lambda: FakeDbContext())
    monkeypatch.setattr(pipeline, "_resolve_start", resolve_start)
    monkeypatch.setattr(pipeline, "_create_run", create_run)
    monkeypatch.setattr(pipeline, "_finish_run", finish_run)
    monkeypatch.setattr(pipeline, "_classification_catalogues", catalogues)
    monkeypatch.setattr(literature_automation_service, "reconcile", unexpected_reconcile)

    result = await pipeline.execute()

    assert result["inserted"] == 1
    assert result["published"] == 0
    assert result["requires_review"] == 1
    assert result["enrichment_errors"] == 1
    assert result["enrichment_failed_providers"] == ["openalex"]
    assert result["enrichment_degraded_review"] == 1
    assert result["autopilot_changed"] == 0
    assert result["autopilot_skipped_degraded_enrichment"] == 1
    assert result["automation"] is None
    assert captured["classification"].publication_status == "review"
    assert captured["kwargs"] == {"preserve_existing_publication_status": True}
    assert finished[0][0] == "completed"


async def test_empty_connect_error_persists_nonempty_redacted_task_error(monkeypatch):
    task = SimpleNamespace(task_uuid="literature-connect-error", task_type=TaskType.SYNC_LITERATURE)
    status_updates = []
    workbook_entries = []

    async def update_status(_task_uuid, status, **kwargs):
        status_updates.append((status, kwargs))

    async def add_entry(*_args, **kwargs):
        workbook_entries.append(kwargs)

    async def send_alert(*_args, **_kwargs):
        return None

    monkeypatch.setattr(lifecycle_module.task_manager, "update_task_status", update_status)
    monkeypatch.setattr(lifecycle_module.task_manager, "add_workbook_entry", add_entry)
    monkeypatch.setattr(lifecycle_module.task_alert_service, "send_task_alert", send_alert)

    with pytest.raises(httpx.ConnectError):
        async with lifecycle_module.task_lifecycle(task, exit_on_cancel=False):
            raise httpx.ConnectError("", request=httpx.Request("GET", "https://secret.example/api?token=abc"))

    assert status_updates == [
        (TaskStatus.RUNNING, {}),
        (TaskStatus.FAILED, {"error_message": "ConnectError"}),
    ]
    assert workbook_entries[0]["content"] == "Error: ConnectError"
    assert lifecycle_module.safe_exception_summary(
        RuntimeError("failed at https://secret.example/path?token=abc token=abc")
    ) == "RuntimeError: failed at [redacted-url] token=[redacted]"
