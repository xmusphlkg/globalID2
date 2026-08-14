import asyncio
from datetime import datetime, timedelta, timezone
import smtplib

import pytest

from src.domain import (
    DiseaseConceptRelation,
    DiseaseMappingAssertion,
    DiseaseMappingRelease,
    SourceDiseaseCategory,
    StandardDisease,
)
from src.services.disease_mapping_ai_service import (
    DiseaseMappingAIService,
    _is_all_model_routes_unavailable,
)
from src.services.disease_mapping_automation_service import (
    DiseaseMappingAutomationService,
    _all_results_are_provider_circuit_failures,
    _is_provider_circuit_error,
    _route_signature,
)
from src.services.disease_mapping_registry_service import DiseaseMappingRegistryService, normalize_source_text
from src.services.mapping_notification_service import (
    MappingEmailTransport,
    _next_digest_at,
)


def _disease(disease_id: str, name: str) -> StandardDisease:
    return StandardDisease(
        disease_id=disease_id,
        standard_name_en=name,
        standard_name_zh=None,
        category="test",
        source="test",
        metadata_={},
        is_active=True,
    )


def test_source_normalization_is_unicode_and_whitespace_stable():
    assert normalize_source_text("  Ｔyphus\n") == "typhus"
    assert normalize_source_text("Hépatite\u00a0 A") == "hépatite a"


def test_covid_exact_name_is_a_unique_deterministic_candidate():
    category = SourceDiseaseCategory(canonical_source_label="COVID-19")
    shortlist = DiseaseMappingAIService._deterministic_shortlist(
        category,
        [_disease("D004", "COVID-19"), _disease("D017", "Measles")],
    )

    exact = DiseaseMappingAIService._unique_exact_match(shortlist)

    assert exact is not None
    assert exact["disease_id"] == "D004"
    assert exact["score"] == 1.0


def test_duplicate_exact_names_require_model_or_human_disambiguation():
    shortlist = [
        {"disease_id": "D001", "score": 1.0},
        {"disease_id": "D002", "score": 1.0},
    ]

    assert DiseaseMappingAIService._unique_exact_match(shortlist) is None


def test_model_route_outage_is_not_a_semantic_mapping_failure():
    assert _is_all_model_routes_unavailable(
        "All active AI model routes failed to produce mapping suggestions; quota"
    )
    assert not _is_all_model_routes_unavailable("Candidate validation failed")


def test_multi_model_disagreement_never_becomes_direct_mapping():
    result = DiseaseMappingAIService._reconcile_outputs(
        [
            {"candidates": [{"candidate_kind": "existing_concept", "target_code": "D026", "confidence_score": 0.9}]},
            {"candidates": [{"candidate_kind": "existing_concept", "target_code": "D124", "confidence_score": 0.8}]},
        ]
    )
    assert {item["target_code"] for item in result["candidates"]} == {"D026", "D124"}
    assert all(item["mapping_relation"] == "ambiguous" for item in result["candidates"])
    assert all(item["comparability"] == "unknown" for item in result["candidates"])
    assert all(item["confidence_score"] <= 0.65 for item in result["candidates"])


def test_interpreted_name_guardrail_surfaces_exact_scope_concept():
    output = {
        "candidates": [
            {
                "candidate_kind": "existing_concept",
                "target_code": "D026",
                "mapping_relation": "narrower",
                "comparability": "direct",
                "confidence_score": 0.92,
                "interpreted_name_en": "Typhoid fever",
                "reasoning": "Selected the umbrella concept.",
                "evidence": [],
            }
        ]
    }
    result = DiseaseMappingAIService._apply_interpreted_name_guardrail(
        output,
        [_disease("D026", "Typhoid and Paratyphoid fever"), _disease("D124", "Typhoid fever")],
    )
    exact, umbrella = result["candidates"][:2]
    assert exact["target_code"] == "D124"
    assert exact["mapping_relation"] == "exact"
    assert exact["comparability"] == "conditional"
    assert exact["confidence_score"] <= 0.75
    assert umbrella["target_code"] == "D026"
    assert umbrella["comparability"] == "unknown"


def test_concept_graph_guardrail_corrects_source_to_target_direction():
    relation = DiseaseConceptRelation(
        subject_disease_id="D124",
        object_disease_id="D026",
        relation_type="reported_component_of",
        comparability="not_comparable",
        aggregation_policy="non_additive",
        is_hierarchical=True,
        confidence_score=1.0,
        assertion_status="approved",
        asserted_by="test",
        source_name="test",
        metadata_={"rollup_policy": "no_auto_rollup"},
    )
    output = {
        "candidates": [{
            "candidate_kind": "existing_concept",
            "target_code": "D026",
            "mapping_relation": "broader",
            "interpreted_name_en": "Typhoid fever",
            "evidence": [],
        }]
    }
    result = DiseaseMappingAIService._apply_concept_relation_guardrail(
        output,
        [_disease("D026", "Typhoid and Paratyphoid fever"), _disease("D124", "Typhoid fever")],
        [relation],
    )
    assert result["candidates"][0]["mapping_relation"] == "narrower"


def test_email_provider_falls_back_to_configured_smtp(monkeypatch):
    monkeypatch.setenv("MAPPING_EMAIL_PROVIDER", "unexpected-provider")
    assert MappingEmailTransport().configured_provider() == "smtp"


def test_smtp_retry_classification_distinguishes_transient_and_permanent_errors():
    transport = MappingEmailTransport()
    assert transport._smtp_retryable(TimeoutError("timed out")) is True
    assert transport._smtp_retryable(smtplib.SMTPServerDisconnected("closed")) is True
    assert transport._smtp_retryable(smtplib.SMTPResponseException(421, b"temporary")) is True
    assert transport._smtp_retryable(smtplib.SMTPResponseException(550, b"rejected")) is False
    assert transport._smtp_retryable(smtplib.SMTPAuthenticationError(535, b"bad auth")) is False


def test_mapping_ai_provider_circuit_classifies_global_quota_and_auth_failures():
    assert _is_provider_circuit_error(
        "PermissionDeniedError: Error code: 403 - insufficient_user_quota"
    )
    assert _is_provider_circuit_error("AuthenticationError: Error code: 401")
    assert not _is_provider_circuit_error("TimeoutError: one model request timed out")


def test_deferred_provider_outages_open_the_mapping_circuit():
    assert _all_results_are_provider_circuit_failures(
        [
            {
                "status": "no_model",
                "provider_error": "PermissionDeniedError: Error code: 403",
            },
            {
                "status": "no_model",
                "provider_error": "insufficient_user_quota",
            },
        ]
    )
    assert not _all_results_are_provider_circuit_failures(
        [{"status": "completed"}, {"status": "no_model", "provider_error": "403"}]
    )


def test_mapping_notification_digest_runs_once_at_daily_utc_slot():
    before_slot = datetime(2026, 8, 12, 0, 30, tzinfo=timezone.utc)
    after_slot = datetime(2026, 8, 12, 7, 30, tzinfo=timezone.utc)

    assert _next_digest_at(before_slot, None, hour_utc=1) == datetime(
        2026, 8, 12, 1, 0, tzinfo=timezone.utc
    )
    assert _next_digest_at(after_slot, None, hour_utc=1) == after_slot
    assert _next_digest_at(
        after_slot,
        datetime(2026, 8, 12, 1, 5, tzinfo=timezone.utc),
        hour_utc=1,
    ) == datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)


def test_mapping_ai_route_change_invalidates_old_provider_circuit():
    service = DiseaseMappingAutomationService()
    service._ai_route_signature = "old:model"
    service._ai_circuit_until = datetime.now(timezone.utc) + timedelta(hours=6)

    signature = _route_signature([{"model_key": "new:model", "priority": 100}])
    service._sync_route_signature(signature)

    assert signature == "new:model"
    assert service._ai_route_signature == "new:model"
    assert service._ai_circuit_until is None


@pytest.mark.asyncio
async def test_mapping_schema_preflight_is_serialized_for_parallel_page_queries(monkeypatch):
    service = DiseaseMappingRegistryService()
    calls = 0

    async def initialize(_db):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        service._schema_ready = True

    monkeypatch.setattr(service, "_initialize_schema", initialize)

    await asyncio.gather(*(service.ensure_schema(object()) for _ in range(4)))

    assert calls == 1


@pytest.mark.asyncio
async def test_candidate_acceptance_atomically_creates_and_activates_release(monkeypatch):
    service = DiseaseMappingRegistryService()
    assertion = DiseaseMappingAssertion(
        id=301,
        assertion_key="MAP_TEST",
        category_id=41,
        target_kind="concept",
        target_code="D001",
        mapping_relation="exact",
        comparability="conditional",
        projection_policy="canonical",
        aggregation_policy="direct_only",
        assertion_status="approved",
        confidence_score=0.8,
        suggestion_method="ai",
        evidence=[],
        metadata_={},
    )
    release = DiseaseMappingRelease(
        id=501,
        release_code="placeholder",
        status="draft",
        checksum="0" * 64,
        created_by="reviewer",
        metadata_={"assertion_count": 1},
    )
    calls = []

    class FakeDB:
        async def execute(self, statement, parameters=None):
            calls.append((str(statement), parameters))

    async def accept(_db, **kwargs):
        assert kwargs == {
            "candidate_id": 17,
            "reviewer": "reviewer",
            "notes": "checked",
        }
        return assertion

    async def create(_db, **kwargs):
        assert kwargs["release_code"].startswith("DMR-AUTO-")
        assert kwargs["release_code"].endswith("-17")
        release.release_code = kwargs["release_code"]
        return release

    async def activate(_db, release_id):
        assert release_id == release.id
        release.status = "active"
        return release

    monkeypatch.setattr(service, "accept_candidate", accept)
    monkeypatch.setattr(service, "create_release", create)
    monkeypatch.setattr(service, "activate_release", activate)

    accepted, published = await service.accept_and_publish_candidate(
        FakeDB(),
        candidate_id=17,
        reviewer="reviewer",
        notes="checked",
    )

    assert accepted is assertion
    assert published is release
    assert published.status == "active"
    assert published.metadata_["publication_trigger"] == "candidate_acceptance"
    assert published.metadata_["accepted_assertion_id"] == 301
    assert "pg_advisory_xact_lock" in calls[0][0]
