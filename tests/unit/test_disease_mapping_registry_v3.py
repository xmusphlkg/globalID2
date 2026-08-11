import asyncio
import smtplib

import pytest

from src.domain import DiseaseConceptRelation, StandardDisease
from src.services.disease_mapping_ai_service import DiseaseMappingAIService
from src.services.disease_mapping_registry_service import DiseaseMappingRegistryService, normalize_source_text
from src.services.mapping_notification_service import MappingEmailTransport


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
