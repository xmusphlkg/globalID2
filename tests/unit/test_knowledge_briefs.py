import asyncio
import hashlib
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import requests
import pytest
from requests import Response

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import AISettings
from src.domain import TaskType
from src.generation.report_v4.composer import compose_report_document
from src.knowledge.evidence import (
    MAX_EVIDENCE_MANIFEST_CHARACTERS,
    build_evidence_manifest,
    prepare_evidence_packet,
)
from src.knowledge.citations import (
    normalize_knowledge_citations,
    validate_knowledge_citations,
)
from src.knowledge.catalogue import (
    LEGACY_CATALOGUE_BRIEF_TIER,
    public_disease_page_exclusion_reason,
    resolve_disease_knowledge_status,
    should_generate_public_disease_page,
)
from src.knowledge.llm_brief_generator import AIDiseaseBriefGenerator
from src.knowledge.reviewed_brief_generator import ReviewedDiseaseBriefGenerator
from src.knowledge.profile_schema import (
    attach_profile_schema,
    resolve_knowledge_profile_schema,
)
from src.knowledge.quality import (
    apply_knowledge_quality_gate,
    assess_knowledge_brief,
    assess_knowledge_evidence,
    assess_knowledge_field,
    EVIDENCE_POLICY_VERSION,
    has_grounding_content,
    KNOWLEDGE_SCHEMA_VERSION,
    strip_unavailable_knowledge_sentences,
)
from src.knowledge.sources import DiseaseKnowledgeFetcher, SourceCandidate
from src.services.disease_knowledge_service import (
    DiseaseKnowledgeUpdateService,
    KNOWLEDGE_PIPELINE_VERSION,
    _active_knowledge_tasks_by_disease,
    _assess_retained_profile_evidence,
    _bilingual_publication_prerequisites,
    _evidence_entity_aliases,
    _evidence_gate_result,
    _is_registry_definition_only_packet,
    _registry_definition_target_sections,
    _generated_profile_failures,
    _knowledge_repair_task_priority,
    _merge_repair_payload,
    _normalize_requested_sections_by_language,
    _profile_repair_diagnostics_by_language,
    _profile_repair_metadata,
    _profile_repair_sections,
    _profile_repair_sections_by_language,
    _publication_evidence_sections,
    _resolve_repair_sections_by_language,
    _restore_brief_with_retained_evidence,
    _select_knowledge_repair_candidates,
    _source_gap_blocks_publication,
    _source_discovery_state,
    _source_transport_retry_delay_seconds,
    _translation_source_usable,
    expand_sources,
)

from scripts.generate_site_data import (
    apply_country_brief_fields,
    apply_disease_knowledge_fields,
    build_country_data,
)


@pytest.fixture(autouse=True)
def _reset_source_adapter_health() -> None:
    """Keep process-local source circuits out of unit-test ordering."""

    DiseaseKnowledgeFetcher.reset_adapter_health()
    yield
    DiseaseKnowledgeFetcher.reset_adapter_health()


def test_requested_repair_sections_are_validated_and_keep_schema_order() -> None:
    ordered = [
        "brief",
        "definition",
        "clinical_features",
        "epidemiology",
        "transmission",
        "prevention",
        "surveillance_note",
        "risk_groups",
    ]

    assert _normalize_requested_sections_by_language(
        {
            "en": ["risk_groups", "unknown", "surveillance_note"],
            "zh": ["prevention"],
            "fr": ["brief"],
        },
        ordered_sections=ordered,
    ) == {
        "en": ["surveillance_note", "risk_groups"],
        "zh": ["prevention"],
    }


def test_default_source_recovery_skips_metadata_only_msd_adapter() -> None:
    assert "msd" not in expand_sources(None)
    assert expand_sources(["msd"]) == ["msd"]


def test_icd10_registry_definition_unblocks_only_a_classification_definition() -> None:
    disease = attach_profile_schema(
        {
            "disease_id": "D066",
            "name_en": "Other viral infections characterized by skin lesions",
            "icd_10": "B08",
            "source": "ICD-10",
        }
    )
    candidate = DiseaseKnowledgeFetcher._build_icd10_definition_source(disease)

    assert candidate is not None
    assert candidate.source_type == "registry_definition"
    assert candidate.metadata["allowed_sections"] == ["definition"]
    packet = prepare_evidence_packet(
        [vars(candidate)],
        resolve_knowledge_profile_schema(disease),
        target_sections=["definition"],
        entity_aliases=[disease["name_en"]],
        max_sources=1,
        allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
    )

    assert packet.coverage.complete
    assert not packet.assessment.sufficient
    assert _evidence_gate_result(packet)["state"] == "ready_for_generation"
    assert _is_registry_definition_only_packet(packet)


def test_registry_definition_targets_never_include_legacy_optional_sections() -> None:
    assert _registry_definition_target_sections(
        {
            "en": ["brief", "definition", "clinical_features"],
            "zh": ["brief", "definition", "epidemiology"],
        },
        registry_definition_only=True,
    ) == {
        "en": ["brief", "definition"],
        "zh": ["brief", "definition"],
    }


def test_sinan_registry_definition_unblocks_only_a_surveillance_definition() -> None:
    disease = attach_profile_schema(
        {
            "disease_id": "D184",
            "name_en": "Accident caused by venomous animals",
            "description": "SINAN surveillance concept for injuries caused by venomous animals",
            "source": "Brazil DATASUS SINAN",
        }
    )
    candidate = DiseaseKnowledgeFetcher._build_registry_definition_source(disease)

    assert disease["knowledge_profile_type"] == "surveillance_event"
    assert candidate is not None
    assert candidate.source_name == "Brazil Ministry of Health SINAN"
    assert candidate.metadata["registry_kind"] == "sinan_catalogue_provenance"
    assert candidate.metadata["allowed_sections"] == ["definition"]
    packet = prepare_evidence_packet(
        [vars(candidate)],
        resolve_knowledge_profile_schema(disease),
        target_sections=["definition"],
        entity_aliases=[disease["name_en"]],
        max_sources=1,
        allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
    )

    assert packet.coverage.complete
    assert _evidence_gate_result(packet)["state"] == "ready_for_generation"


def test_sinan_registry_definition_is_definition_evidence_even_with_nonclinical_disclaimer() -> None:
    disease = attach_profile_schema(
        {
            "disease_id": "D189",
            "name_en": "Work-related dermatosis",
            "description": "SINAN occupational health concept for work-related dermatoses",
            "source": "Brazil DATASUS SINAN",
        }
    )
    candidate = DiseaseKnowledgeFetcher._build_registry_definition_source(disease)

    assert candidate is not None
    packet = prepare_evidence_packet(
        [vars(candidate)],
        resolve_knowledge_profile_schema(disease),
        target_sections=["definition"],
        entity_aliases=[disease["name_en"]],
        max_sources=1,
        allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
    )

    assert packet.coverage.complete
    assert packet.manifest.fragments[0].supported_sections == ("definition",)
    assert _evidence_gate_result(packet)["state"] == "ready_for_generation"


def test_registry_definition_short_circuits_remote_discovery_for_a_complete_profile() -> None:
    disease = attach_profile_schema(
        {
            "disease_id": "D184",
            "name_en": "Accident caused by venomous animals",
            "description": "SINAN surveillance concept",
            "source": "Brazil DATASUS SINAN",
        }
    )
    fetcher = DiseaseKnowledgeFetcher()

    def unexpected_remote_discovery(_disease):
        raise AssertionError(
            "definition-only registry evidence must short-circuit remote discovery"
        )

    fetcher._fetch_who_pages = unexpected_remote_discovery
    fetcher._fetch_who_don = unexpected_remote_discovery
    fetcher._fetch_wikidata = unexpected_remote_discovery
    fetcher._fetch_web_search = unexpected_remote_discovery
    fetcher._fetch_wikipedia = unexpected_remote_discovery
    fetcher._fetch_pubmed = unexpected_remote_discovery

    report = fetcher.fetch_with_report(disease, target_sections=["definition"])

    assert report.adapter_outcomes == {"registry_definition": "success"}
    assert len(report.candidates) == 1
    assert report.candidates[0].source_name == "Brazil Ministry of Health SINAN"


def test_aggregate_ontology_definition_short_circuits_remote_discovery() -> None:
    disease = attach_profile_schema(
        {
            "disease_id": "D119",
            "name_en": "Streptococcal disease",
            "source": "CDC",
            "ontology_context": {
                "definition": "Broad streptococcal disease concept used as a non-additive parent.",
                "facet_tags": {
                    "surveillance_scope": ["surveillance_scope.aggregate"],
                },
            },
        }
    )
    fetcher = DiseaseKnowledgeFetcher()

    report = fetcher.fetch_with_report(disease, target_sections=["definition"])

    assert report.adapter_outcomes == {"registry_definition": "success"}
    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.source_name == "GlobalID Disease Ontology"
    assert candidate.metadata["registry_kind"] == "globalid_aggregate_concept"
    packet = prepare_evidence_packet(
        [vars(candidate)],
        resolve_knowledge_profile_schema(disease),
        target_sections=["definition"],
        entity_aliases=[disease["name_en"]],
        max_sources=1,
        allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
    )
    assert packet.coverage.complete
    assert _evidence_gate_result(packet)["state"] == "ready_for_generation"


def test_knowledge_repair_task_priority_preserves_repair_urgency() -> None:
    assert _knowledge_repair_task_priority(None, "urgent").value == "urgent"
    assert _knowledge_repair_task_priority(None, "high").value == "high"
    assert _knowledge_repair_task_priority(None, "normal").value == "normal"
    assert _knowledge_repair_task_priority(None, "low").value == "low"


def test_knowledge_repair_candidates_skip_non_public_catalogue_rows() -> None:
    selected, skipped = _select_knowledge_repair_candidates(
        [
            {
                "disease_id": "D999",
                "name_en": "Total",
                "category": "Summary",
                "repair_priority": "urgent",
                "repair_sections": ["definition"],
            }
        ]
    )

    assert selected == []
    assert skipped == [{"disease_id": "D999", "reason": "non_public_disease_id"}]


def test_transport_source_gap_does_not_revoke_a_previously_valid_profile() -> None:
    assert not _source_gap_blocks_publication(
        {"source_gap": True, "adapter_outcomes": {"web_search": "timeout"}}
    )
    assert _source_gap_blocks_publication(
        {"source_gap": True, "adapter_outcomes": {"web_search": "success_empty"}}
    )


def test_compact_knowledge_repairs_get_a_shorter_route_timeout() -> None:
    compact = AIDiseaseBriefGenerator._effective_model_request_timeout_seconds(
        output_token_budget=800,
    )
    full = AIDiseaseBriefGenerator._effective_model_request_timeout_seconds(
        output_token_budget=3600,
    )

    assert compact == min(full, 55)
    assert compact <= full


def test_fetcher_deduplicates_by_disease_source_and_url() -> None:
    first = SourceCandidate(
        disease_id="flu",
        source_type="wikipedia",
        source_name="Wikipedia",
        url="https://en.wikipedia.org/wiki/Influenza",
    )
    duplicate = SourceCandidate(
        disease_id="flu",
        source_type="wikipedia",
        source_name="Wikipedia",
        url="https://en.wikipedia.org/wiki/Influenza",
    )

    assert DiseaseKnowledgeFetcher._dedupe([first, duplicate]) == [first]


def test_source_adapters_run_in_parallel_and_report_duration() -> None:
    fetcher = DiseaseKnowledgeFetcher(
        min_interval_seconds=0,
        source_hints_path=None,
    )

    def authority_source(_disease):
        time.sleep(0.2)
        return [
            SourceCandidate(
                disease_id="ANY",
                source_type="who",
                source_name="WHO",
                url="https://www.who.int/example",
                status="active",
                review_status="approved",
                content_text=(
                    "This authoritative disease profile describes infection, clinical illness, "
                    "transmission, epidemiology, prevention and surveillance. " * 12
                ),
                metadata={"relevance_score": 1.0},
            )
        ]

    def empty_scholarly_source(_disease):
        time.sleep(0.2)
        return []

    fetcher._fetch_who_pages = authority_source  # type: ignore[method-assign]
    fetcher._fetch_pubmed = empty_scholarly_source  # type: ignore[method-assign]
    started_at = time.monotonic()
    report = fetcher.fetch_with_report(
        {"disease_id": "ANY", "name_en": "Example infection"},
        enabled_sources=["who", "pubmed"],
        target_sections=["brief", "prevention"],
    )
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.35
    assert report.adapter_outcomes == {"who": "success", "pubmed": "success_empty"}
    assert report.adapter_durations["who"] >= 0.19
    assert report.adapter_durations["pubmed"] >= 0.19


def test_source_adapter_timeout_does_not_block_other_results() -> None:
    fetcher = DiseaseKnowledgeFetcher(
        min_interval_seconds=0,
        source_hints_path=None,
        adapter_timeout_seconds=1,
    )

    def slow_source(_disease):
        time.sleep(1.5)
        return []

    def fast_source(_disease):
        return [
            SourceCandidate(
                disease_id="ANY",
                source_type="who",
                source_name="WHO",
                url="https://www.who.int/example-timeout",
                status="active",
                review_status="approved",
                content_text=(
                    "This authoritative disease profile describes infection, clinical illness, "
                    "transmission, epidemiology, prevention and surveillance. " * 12
                ),
                metadata={"relevance_score": 1.0},
            )
        ]

    fetcher._fetch_who_pages = fast_source  # type: ignore[method-assign]
    fetcher._fetch_pubmed = slow_source  # type: ignore[method-assign]
    started_at = time.monotonic()

    report = fetcher.fetch_with_report(
        {"disease_id": "ANY", "name_en": "Example infection"},
        enabled_sources=["who", "pubmed"],
        target_sections=["brief", "prevention"],
    )

    assert time.monotonic() - started_at < 1.4
    assert report.candidates
    assert report.adapter_outcomes["who"] == "success"
    assert report.adapter_outcomes["pubmed"] == "timeout"


def test_source_fetch_cancellation_stops_waiting_for_slow_adapter() -> None:
    fetcher = DiseaseKnowledgeFetcher(
        min_interval_seconds=0,
        source_hints_path=None,
        adapter_timeout_seconds=2,
    )
    cancel_event = threading.Event()
    result = {}

    def slow_source(_disease):
        time.sleep(0.4)
        return []

    fetcher._fetch_pubmed = slow_source  # type: ignore[method-assign]

    def run_fetch() -> None:
        result["report"] = fetcher.fetch_with_report(
            {"disease_id": "ANY", "name_en": "Example infection"},
            enabled_sources=["pubmed"],
            target_sections=["prevention"],
            cancel_event=cancel_event,
        )

    worker = threading.Thread(target=run_fetch)
    started_at = time.monotonic()
    worker.start()
    time.sleep(0.05)
    cancel_event.set()
    worker.join(timeout=0.3)

    assert worker.is_alive() is False
    assert time.monotonic() - started_at < 0.3
    assert result["report"].adapter_outcomes["pubmed"] == "cancelled"


def test_enrichment_does_not_retry_an_adapter_that_timed_out_in_primary_round() -> None:
    fetcher = DiseaseKnowledgeFetcher(
        min_interval_seconds=0,
        source_hints_path=None,
        adapter_timeout_seconds=1,
    )
    who_calls = 0

    def slow_who(_disease):
        nonlocal who_calls
        who_calls += 1
        time.sleep(1.2)
        return []

    def alias_from_wikidata(_disease):
        return [
            SourceCandidate(
                disease_id="ANY",
                source_type="wikidata",
                source_name="Wikidata",
                url="https://www.wikidata.org/wiki/Q1",
                title="Example condition",
                raw_excerpt="Example condition (EC).",
                content_text="Example condition (EC).",
                review_status="approved",
            )
        ]

    fetcher._fetch_who_pages = slow_who  # type: ignore[method-assign]
    fetcher._fetch_wikidata = alias_from_wikidata  # type: ignore[method-assign]
    fetcher._fetch_web_search = lambda _disease: []  # type: ignore[method-assign]
    fetcher._fetch_wikipedia = lambda _disease: []  # type: ignore[method-assign]
    fetcher._fetch_pubmed = lambda _disease: []  # type: ignore[method-assign]

    report = fetcher.fetch_with_report(
        {"disease_id": "ANY", "name_en": "Example condition"},
        enabled_sources=["who", "wikidata"],
        target_sections=["prevention"],
    )

    assert report.adapter_outcomes["who"] == "timeout"
    assert who_calls == 1


def test_targeted_adapter_requests_use_short_timeout_without_retries() -> None:
    fetcher = DiseaseKnowledgeFetcher(timeout=12, min_interval_seconds=0)
    calls: list[int] = []

    class TimeoutSession:
        headers: dict[str, str] = {}

        def get(self, _url, params=None, timeout=12):
            calls.append(timeout)
            raise requests.Timeout("network unavailable")

    fetcher.session = TimeoutSession()  # type: ignore[assignment]
    fetcher._adapter_request_state.request_timeout_seconds = 4
    fetcher._adapter_request_state.max_retries = 0

    assert fetcher._get("https://example.org/slow") is None
    assert calls == [4]


def test_fetcher_caches_repeated_get_requests() -> None:
    response = Response()
    response.status_code = 200
    calls: list[str] = []

    class FakeSession:
        headers: dict[str, str] = {}

        def get(self, url: str, params: dict[str, str] | None = None, timeout: int = 12) -> Response:
            calls.append(url)
            return response

    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    fetcher.session = FakeSession()  # type: ignore[assignment]

    assert fetcher._get("https://example.org/source", params={"q": "flu"}) is response
    assert fetcher._get("https://example.org/source", params={"q": "flu"}) is response
    assert calls == ["https://example.org/source"]


def test_cre_knowledge_uses_ontology_aliases_and_reviewed_source_hints() -> None:
    service = DiseaseKnowledgeUpdateService()
    disease = service._find_disease("D227")

    assert "CRE" in disease["query_aliases"]
    assert "carbapenem-resistant enterobacteriaceae infection" in {
        alias.casefold() for alias in disease["query_aliases"]
    }

    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    hints = fetcher._source_hints(disease)
    assert "CRE infection" in hints["aliases"]
    assert {source["source_name"] for source in hints["sources"]} == {
        "US CDC",
        "World Health Organization",
    }


def test_severe_enterovirus_reviewed_source_hint_covers_the_public_profile() -> None:
    service = DiseaseKnowledgeUpdateService()
    disease = service._find_disease("D180")
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    fetcher._crawl_html_page = lambda **_kwargs: None  # type: ignore[method-assign]
    report = fetcher.fetch_with_report(disease, enabled_sources=["web_search"])
    candidates = [
        candidate
        for candidate in report.candidates
        if candidate.metadata.get("offline_reviewed_summary")
    ]
    schema = resolve_knowledge_profile_schema(disease)
    hints = fetcher._source_hints(disease)
    packet = prepare_evidence_packet(
        [
            {
                "id": index + 1,
                "source_type": candidate.source_type,
                "source_name": candidate.source_name,
                "url": candidate.url,
                "status": candidate.status,
                "review_status": candidate.review_status,
                "content_text": candidate.content_text,
                "content_sections": candidate.content_sections,
                "metadata": candidate.metadata,
            }
            for index, candidate in enumerate(candidates)
        ],
        schema,
        target_sections=["brief", *schema.required_fields],
        entity_aliases=hints["aliases"],
    )

    assert len(candidates) == 1
    assert candidates[0].metadata["offline_reviewed_summary"] is True
    assert packet.coverage.missing_required_sections == ()


def test_registry_codes_do_not_pollute_public_knowledge_query_aliases() -> None:
    disease = DiseaseKnowledgeUpdateService()._find_disease("D230")

    assert "HIVE" not in disease["query_aliases"]
    assert "Crianca exposta ao HIV" in disease["query_aliases"]


def test_cre_reviewed_source_summaries_survive_remote_fetch_failure() -> None:
    service = DiseaseKnowledgeUpdateService()
    disease = service._find_disease("D227")
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    hints = fetcher._source_hints(disease)
    fetcher._crawl_html_page = lambda **_kwargs: None  # type: ignore[method-assign]

    candidates = fetcher._fetch_configured_sources(
        disease,
        hints["sources"],
        enabled_sources=["who", "web_search"],
    )

    assert len(candidates) == 2
    assert all(candidate.metadata["offline_reviewed_summary"] for candidate in candidates)
    assert assess_knowledge_evidence(candidates).sufficient


def test_cronobacter_reviewed_source_hint_covers_required_profile_fields() -> None:
    service = DiseaseKnowledgeUpdateService()
    disease = service._find_disease("D117")
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    fetcher._crawl_html_page = lambda **_kwargs: None  # type: ignore[method-assign]
    fetcher._fetch_web_search = lambda _disease: []  # type: ignore[method-assign]
    report = fetcher.fetch_with_report(
        disease,
        enabled_sources=["web_search"],
    )
    candidates = report.candidates
    sources = [
        {**candidate.__dict__, "id": index, "metadata": candidate.metadata}
        for index, candidate in enumerate(candidates, start=1)
    ]
    schema = resolve_knowledge_profile_schema(disease)
    packet = prepare_evidence_packet(
        sources,
        schema,
        target_sections=["brief", *schema.required_fields],
        entity_aliases=_evidence_entity_aliases(disease),
        max_sources=8,
        max_manifest_characters=12_000,
        allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
    )

    assert packet.assessment.sufficient
    assert packet.coverage.complete


@pytest.mark.parametrize(
    ("disease_id", "target_sections"),
    [
        ("D100", ("transmission",)),
        ("D138", ("epidemiology", "transmission", "prevention")),
        ("D144", ("prevention",)),
        ("D146", ("prevention",)),
        ("D157", ("transmission", "prevention")),
        ("D159", ("prevention",)),
        ("D170", ("epidemiology",)),
        ("D172", ("transmission",)),
        ("D183", ("definition", "epidemiology", "transmission", "prevention")),
        (
            "D185",
            ("definition", "clinical_features", "epidemiology", "transmission", "prevention"),
        ),
        ("D149", ("definition",)),
        ("D242", ("prevention",)),
    ],
)
def test_targeted_reviewed_source_hints_cover_previous_evidence_gaps(
    disease_id: str,
    target_sections: tuple[str, ...],
) -> None:
    service = DiseaseKnowledgeUpdateService()
    disease = service._find_disease(disease_id)
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    fetcher._crawl_html_page = lambda **_kwargs: None  # type: ignore[method-assign]
    hints = fetcher._source_hints(disease)
    candidates = fetcher._fetch_configured_sources(
        disease,
        hints["sources"],
        enabled_sources=["web_search"],
    )
    packet = prepare_evidence_packet(
        [
            {
                **candidate.__dict__,
                "id": index,
                "metadata": candidate.metadata,
            }
            for index, candidate in enumerate(candidates, start=1)
        ],
        resolve_knowledge_profile_schema(disease),
        target_sections=target_sections,
        entity_aliases=[disease["name_en"], *hints["aliases"]],
        allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
    )

    assert len(candidates) == 1
    assert candidates[0].metadata["offline_reviewed_summary"] is True
    assert packet.coverage.complete


def test_generation_prompt_includes_deterministic_repair_feedback() -> None:
    payload = AIDiseaseBriefGenerator._prompt_payload(
        disease=attach_profile_schema(
            {
                "disease_id": "ANY",
                "name_en": "Example infection",
                "target_sections": ["prevention"],
                "repair_context": [
                    "The previous candidate omitted the required prevention field.",
                ],
            }
        ),
        sources=[],
        language="en",
    )

    assert payload["repair_context"] == [
        "The previous candidate omitted the required prevention field."
    ]


def test_configured_source_hint_augments_a_live_capture_with_reviewed_sections() -> None:
    service = DiseaseKnowledgeUpdateService()
    disease = service._find_disease("D117")
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)

    def live_candidate(**kwargs):
        return SourceCandidate(
            disease_id=kwargs["disease_id"],
            source_type=kwargs["source_type"],
            source_name=kwargs["source_name"],
            url=kwargs["url"],
            title="About Cronobacter Infection",
            content_text="Live official capture with a clinical overview.",
            review_status="approved",
            metadata=kwargs["metadata"],
        )

    fetcher._crawl_html_page = live_candidate  # type: ignore[method-assign]
    fetcher._fetch_web_search = lambda _disease: []  # type: ignore[method-assign]
    report = fetcher.fetch_with_report(disease, enabled_sources=["web_search"])

    assert len(report.candidates) == 1
    candidate = report.candidates[0]
    assert candidate.metadata["configured_reviewed_summary"] is True
    assert candidate.content_sections
    assert "historic reporting coverage" in (candidate.content_text or "")


def test_cre_reviewed_profile_is_full_bilingual_and_source_cited() -> None:
    service = DiseaseKnowledgeUpdateService()
    disease = service._find_disease("D227")
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    hints = fetcher._source_hints(disease)
    fetcher._crawl_html_page = lambda **_kwargs: None  # type: ignore[method-assign]
    candidates = fetcher._fetch_configured_sources(
        disease,
        hints["sources"],
        enabled_sources=["who", "web_search"],
    )
    sources = [
        {
            **candidate.__dict__,
            "id": index,
            "metadata": candidate.metadata,
        }
        for index, candidate in enumerate(candidates, start=1)
    ]
    generator = ReviewedDiseaseBriefGenerator()

    results = [
        asyncio.run(
            generator.generate_with_trace(
                disease=disease,
                sources=sources,
                language=language,
            )
        )
        for language in ("en", "zh")
    ]

    assert generator.has_profile("D227")
    assert {result["payload"]["status"] for result in results} == {"published"}
    assert all(result["trace"]["generator"] == "reviewed" for result in results)
    assert all(not result["trace"]["error"] for result in results)
    assert all(
        assess_knowledge_brief(result["payload"], disease=disease).display_mode == "full"
        for result in results
    )
    assert all(result["payload"]["source_ids"] == [1, 2] for result in results)


def test_fetcher_extracts_who_pages_for_plague_when_english_name_matches() -> None:
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    html = """
        <html>
          <head><title>Plague</title></head>
          <body>
            <article>
              <p>Plague is an infectious disease caused by Yersinia pestis bacteria.</p>
              <p>It can be transmitted by infected fleas and respiratory droplets.</p>
            </article>
          </body>
        </html>
    """
    response = Response()
    response.status_code = 200
    response.url = "https://www.who.int/news-room/fact-sheets/detail/plague"
    response._content = html.encode("utf-8")

    def fake_get(url: str, params: dict[str, str] | None = None) -> Response | None:
        if "health-topics/plague" in url or "fact-sheets/detail/plague" in url or "questions-and-answers/item/plague" in url:
            return response
        return None

    fetcher._get = fake_get  # type: ignore[assignment]
    candidates = fetcher._fetch_who_pages(
        {
            "disease_id": "D001",
            "name_en": "Plague",
            "name_zh": "鼠疫",
            "standard_name_en": "Plague",
            "standard_name_zh": "鼠疫",
        }
    )

    assert len(candidates) == 3
    assert all(candidate.source_type == "who" for candidate in candidates)
    assert any("Yersinia pestis" in (candidate.raw_excerpt or "") for candidate in candidates)
    assert any("Yersinia pestis" in (candidate.content_text or "") for candidate in candidates)
    assert any(candidate.content_sections and candidate.content_sections[0]["heading"] == "Plague" for candidate in candidates)
    assert all(candidate.resolved_url == response.url for candidate in candidates)


def test_fetcher_skips_wikipedia_disambiguation_and_uses_disease_page() -> None:
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)

    def make_json_response(payload: dict) -> Response:
        response = Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode("utf-8")
        return response

    def fake_get(url: str, params: dict[str, str] | None = None) -> Response | None:
        if url.endswith("/Plague"):
            return make_json_response(
                {
                    "title": "Plague",
                    "description": "Wikipedia disambiguation page",
                    "extract": "Plague or The Plague may refer to:",
                }
            )
        if "Plague%20%28disease%29" in url:
            return make_json_response(
                {
                    "title": "Plague (disease)",
                    "description": "Disease caused by Yersinia pestis bacterium",
                    "extract": "Plague is an infectious disease caused by the bacterium Yersinia pestis.",
                    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Plague_(disease)"}},
                }
            )
        return None

    fetcher._get = fake_get  # type: ignore[assignment]
    candidates = fetcher._fetch_wikipedia(
        {
            "disease_id": "D001",
            "name_en": "Plague",
            "name_zh": "鼠疫",
            "standard_name_en": "Plague",
            "standard_name_zh": "鼠疫",
        }
    )

    assert len(candidates) == 1
    assert candidates[0].title == "Plague (disease)"
    assert "Yersinia pestis" in (candidates[0].raw_excerpt or "")
    assert "Yersinia pestis" in (candidates[0].content_text or "")
    assert candidates[0].resolved_url == "https://en.wikipedia.org/wiki/Plague_(disease)"


def test_ai_brief_user_prompt_uses_shared_manifest_as_only_content_boundary() -> None:
    prompt = AIDiseaseBriefGenerator._user_prompt(
        disease={
            "disease_id": "D001",
            "name_en": "Plague",
            "name_zh": "鼠疫",
            "category": "Bacterial",
            "description": "A serious bacterial infection.",
            "icd_10": "A20",
            "icd_11": "1B90",
        },
        sources=[
            {
                "id": 1,
                "source_type": "who",
                "source_name": "WHO Fact Sheet",
                "title": "Plague",
                "url": "https://www.who.int/news-room/fact-sheets/detail/plague",
                "resolved_url": "https://www.who.int/news-room/fact-sheets/detail/plague",
                "license": "WHO website terms",
                "content_text": "Plague is a severe infection caused by Yersinia pestis. Symptoms and epidemiology are described in detail.",
                "content_sections": [{"heading": "Symptoms", "text": "Fever and painful lymph nodes."}],
                "raw_excerpt": "Short excerpt.",
                "review_status": "approved",
            }
        ],
        language="en",
    )

    assert "Plague is a severe infection caused by Yersinia pestis" in prompt
    assert '"evidence_manifest"' in prompt
    assert '"supported_sections"' in prompt
    assert '"content_sections"' not in prompt
    assert '"resolved_url"' not in prompt
    assert '"source_id"' not in prompt


def test_ai_prompt_is_stable_across_database_source_id_changes() -> None:
    disease = {"disease_id": "ANY", "name_en": "Example infection"}
    source = {
        "id": 10,
        "source_type": "who",
        "source_name": "WHO",
        "title": "Example infection",
        "url": "https://www.who.int/example",
        "content_text": (
            "This infection has clinical, epidemiologic, transmission and prevention evidence. " * 6
        ),
    }

    first = AIDiseaseBriefGenerator._user_prompt(
        disease=disease,
        sources=[source],
        language="en",
    )
    second = AIDiseaseBriefGenerator._user_prompt(
        disease=disease,
        sources=[{**source, "id": 9999, "source_id": 9999}],
        language="en",
    )

    assert first == second
    assert len(first) < 5_000

    chinese = AIDiseaseBriefGenerator._user_prompt(
        disease=disease,
        sources=[source],
        language="zh",
    )
    assert AIDiseaseBriefGenerator._system_prompt("en") == (
        AIDiseaseBriefGenerator._system_prompt("zh")
    )
    assert first.rsplit('"output_language":', 1)[0] == chinese.rsplit(
        '"output_language":', 1
    )[0]

    partial_budget = AIDiseaseBriefGenerator._output_token_budget(
        ["brief", "prevention"]
    )
    full_budget = AIDiseaseBriefGenerator._output_token_budget(
        [
            "brief",
            "definition",
            "clinical_features",
            "epidemiology",
            "transmission",
            "prevention",
            "surveillance_note",
            "risk_groups",
        ]
    )
    assert 800 <= partial_budget < full_budget
    assert full_budget <= AISettings().knowledge_max_output_tokens


def test_ai_brief_prompt_requires_null_for_unsupported_fields() -> None:
    system_prompt = AIDiseaseBriefGenerator._system_prompt("en")
    user_prompt = AIDiseaseBriefGenerator._user_prompt(
        disease={"disease_id": "D207", "name_en": "Enterovirus 71 infection"},
        sources=[],
        language="en",
    )

    assert "return null for that field" in system_prompt
    assert "surveillance_note is required" in system_prompt
    assert "complications, opportunistic infections, co-infections" in system_prompt
    assert "set it to null" in user_prompt
    assert "absence explanation" in user_prompt
    assert AIDiseaseBriefGenerator._field({"prevention": None}, "prevention") is None


def test_ai_brief_prompt_uses_configured_evidence_budget(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.knowledge.llm_brief_generator.get_config",
        lambda: SimpleNamespace(
            ai=SimpleNamespace(
                knowledge_evidence_max_sources=3,
                knowledge_evidence_manifest_max_characters=4000,
            )
        ),
    )
    sources = [
        {
            "id": index,
            "source_type": "who",
            "source_name": "WHO",
            "url": f"https://example.org/{index}",
            "status": "active",
            "review_status": "approved",
            "content_text": (
                "Definition, clinical features, epidemiology, transmission, prevention, "
                "surveillance and risk groups. "
                * 40
            ),
            "metadata": {"relevance_score": 1.0},
        }
        for index in range(1, 7)
    ]

    payload = AIDiseaseBriefGenerator._prompt_payload(
        disease={"disease_id": "ANY", "name_en": "Example infection"},
        sources=sources,
        language="en",
    )

    fragments = payload["evidence_manifest"]["fragments"]
    assert payload["evidence_budget"] == {
        "max_sources": 3,
        "max_manifest_characters": 4000,
    }
    assert len(payload["sources"]) == 3
    assert len({fragment["citation_ref"] for fragment in fragments}) <= 3
    assert sum(len(fragment["text"]) for fragment in fragments) <= 4000


def test_ai_generator_translates_zh_from_english_payload_without_evidence_text() -> None:
    class TranslationAgent:
        def __init__(self) -> None:
            self.history = []

        def clear_conversation_history(self) -> None:
            self.history = []

        async def complete(self, **kwargs) -> str:
            response = json.dumps(
                {
                    "brief": "这是一段基于来源的中文概述，保留引用标记 [1]。",
                    "definition": None,
                    "clinical_features": None,
                    "epidemiology": None,
                    "transmission": None,
                    "prevention": "预防措施包括疫苗接种和卫生措施 [1]。",
                    "surveillance_note": None,
                    "risk_groups": None,
                }
            )
            self.history.append(
                {
                    "model": "test-model",
                    "provider": "test-provider",
                    "tokens": {"prompt": 100, "completion": 40, "total": 140},
                    "duration": 0.01,
                    "metadata": {"cache_hit": False},
                    "prompt": kwargs["prompt"],
                    "response": response,
                }
            )
            return response

        def get_latest_conversation(self) -> dict:
            return self.history[-1]

        def get_conversation_history(self) -> list[dict]:
            return self.history

    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Example infection",
            "name_zh": "示例感染",
            "target_sections": ["brief", "prevention"],
            "evidence_target_sections": ["brief", "prevention"],
            "_evidence_packet_prepared": True,
        }
    )
    source = {
        "id": 10,
        "source_type": "who",
        "source_name": "WHO",
        "url": "https://example.org/source",
        "status": "active",
        "review_status": "approved",
        "content_text": (
            "This source provides a definition, prevention and control evidence. "
            "Vaccination and hygiene are supported prevention measures. "
            * 8
        ),
        "metadata": {"relevance_score": 1.0},
    }
    manifest = build_evidence_manifest(
        [source],
        resolve_knowledge_profile_schema(disease),
        target_sections=["brief", "prevention"],
    ).to_dict()
    english_payload = {
        "brief": "A source-grounded overview of this infection [1].",
        "prevention": "Vaccination and hygiene are supported prevention measures [1].",
        "source_attribution": [{"source_id": 10, "citation_index": 1, "url": "https://example.org/source"}],
        "metadata": {"evidence_manifest": manifest, "citation_repair": {"final_failures": []}},
    }
    agent = TranslationAgent()

    result = asyncio.run(
        AIDiseaseBriefGenerator(agent=agent).translate_from_payload_with_trace(
            disease=disease,
            english_payload=english_payload,
            sources=[source],
            target_sections=["brief", "prevention"],
        )
    )

    assert result["trace"]["generator"] == "ai_translation"
    assert result["trace"]["citation_failures"] == []
    assert result["payload"]["language"] == "zh"
    assert result["payload"]["status"] == "draft"
    assert result["payload"]["metadata"]["translation_mode"] == "from_en_grounded_payload"
    assert "evidence_manifest" not in agent.history[0]["prompt"]
    assert source["content_text"] not in agent.history[0]["prompt"]


def test_ai_generator_does_not_create_content_fallback_without_evidence() -> None:
    result = asyncio.run(
        AIDiseaseBriefGenerator().generate_with_trace(
            disease={"disease_id": "ANY", "name_en": "Example infection"},
            sources=[],
            language="en",
        )
    )

    assert result["trace"]["generator"] == "ai"
    assert result["trace"]["error"]
    assert result["payload"]["status"] == "draft"
    for field in (
        "brief",
        "definition",
        "clinical_features",
        "epidemiology",
        "transmission",
        "prevention",
        "surveillance_note",
        "risk_groups",
    ):
        assert result["payload"][field] is None


def test_ai_generator_repairs_invalid_section_citations_once() -> None:
    class RepairingAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.latest = {}

        def clear_conversation_history(self) -> None:
            self.latest = {}

        async def complete(self, **_kwargs) -> str:
            self.calls += 1
            self.latest = {
                "model": "test-model",
                "provider": "test-provider",
                "tokens": {"total": 10},
                "duration": 0.01,
                "metadata": {"cache_hit": False},
            }
            if self.calls == 1:
                return json.dumps(
                    {
                        "brief": "A substantive evidence-grounded overview of this infection [1].",
                        "definition": None,
                        "clinical_features": None,
                        "epidemiology": None,
                        "transmission": None,
                        "prevention": "A substantive prevention paragraph with an invalid citation [2].",
                        "surveillance_note": None,
                        "risk_groups": None,
                    }
                )
            return json.dumps(
                {
                    "brief": "A substantive evidence-grounded overview of this infection [1].",
                    "definition": None,
                    "clinical_features": None,
                    "epidemiology": None,
                    "transmission": None,
                    "prevention": "Vaccination and hygiene are supported prevention measures [1].",
                    "surveillance_note": None,
                    "risk_groups": None,
                }
            )

        def get_latest_conversation(self) -> dict:
            return self.latest

    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Example infection",
            "target_sections": ["brief", "prevention"],
            "evidence_target_sections": ["brief", "prevention"],
            "_evidence_packet_prepared": True,
        }
    )
    source = {
        "id": 10,
        "source_type": "who",
        "source_name": "WHO",
        "url": "https://example.org/source",
        "status": "active",
        "review_status": "approved",
        "content_text": (
            "Prevention and control include vaccination and hygiene measures. "
            "This infection is addressed through public-health prevention programmes."
        ),
        "metadata": {"relevance_score": 1.0},
    }
    agent = RepairingAgent()

    result = asyncio.run(
        AIDiseaseBriefGenerator(agent=agent).generate_with_trace(
            disease=disease,
            sources=[source],
            language="en",
        )
    )

    assert agent.calls == 2
    assert result["payload"]["metadata"]["citation_repair"]["attempted"] is True
    assert result["payload"]["metadata"]["citation_repair"]["final_failures"] == []
    assert result["payload"]["prevention"].endswith("[1].")


def test_ai_generator_repairs_supported_missing_target_section_once() -> None:
    class QualityRepairingAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.prompts = []
            self.latest = {}

        def clear_conversation_history(self) -> None:
            self.latest = {}

        async def complete(self, **kwargs) -> str:
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            self.latest = {
                "model": "test-model",
                "provider": "test-provider",
                "tokens": {"total": 10},
                "duration": 0.01,
                "metadata": {"cache_hit": False},
            }
            prevention = (
                None
                if self.calls == 1
                else "Vaccination and hygiene are supported prevention measures [1]."
            )
            return json.dumps(
                {
                    "brief": "A substantive evidence-grounded overview of this infection [1].",
                    "definition": None,
                    "clinical_features": None,
                    "epidemiology": None,
                    "transmission": None,
                    "prevention": prevention,
                    "surveillance_note": None,
                    "risk_groups": None,
                }
            )

        def get_latest_conversation(self) -> dict:
            return self.latest

    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Example infection",
            "target_sections": ["brief", "prevention"],
            "evidence_target_sections": ["brief", "prevention"],
            "_evidence_packet_prepared": True,
        }
    )
    source = {
        "id": 10,
        "source_type": "who",
        "source_name": "WHO",
        "url": "https://example.org/source",
        "status": "active",
        "review_status": "approved",
        "content_text": (
            "Prevention and control include vaccination and hygiene measures. "
            "This infection is addressed through public-health prevention programmes."
        ),
        "metadata": {"relevance_score": 1.0},
    }
    agent = QualityRepairingAgent()

    result = asyncio.run(
        AIDiseaseBriefGenerator(agent=agent).generate_with_trace(
            disease=disease,
            sources=[source],
            language="en",
        )
    )

    assert agent.calls == 2
    assert '"quality_failures"' in agent.prompts[1]
    assert result["payload"]["metadata"]["quality_repair"] == {
        "attempted": True,
        "failures": [{"field": "prevention", "status": "missing", "reason": "empty"}],
        "error": None,
    }
    assert result["payload"]["brief"].endswith("[1].")
    assert result["payload"]["prevention"].endswith("[1].")
    assert '"previous_json"' not in agent.prompts[1]


def test_ai_generator_reserves_quality_repair_after_citation_repair() -> None:
    class MultiStageRepairingAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.prompts = []
            self.latest = {}

        def clear_conversation_history(self) -> None:
            self.latest = {}

        async def complete(self, **kwargs) -> str:
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            self.latest = {
                "model": "test-model",
                "provider": "test-provider",
                "tokens": {"total": 10},
                "duration": 0.01,
                "metadata": {"cache_hit": False},
            }
            if self.calls == 1:
                return json.dumps(
                    {
                        "brief": "A substantive source-grounded overview of this infection [2].",
                        "definition": None,
                        "clinical_features": None,
                        "epidemiology": None,
                        "transmission": None,
                        "prevention": None,
                        "surveillance_note": None,
                        "risk_groups": None,
                    }
                )
            if self.calls == 2:
                return json.dumps(
                    {
                        "brief": "A substantive source-grounded overview of this infection [1].",
                        "definition": None,
                        "clinical_features": None,
                        "epidemiology": None,
                        "transmission": None,
                        "prevention": None,
                        "surveillance_note": None,
                        "risk_groups": None,
                    }
                )
            return json.dumps(
                {
                    "prevention": "Vaccination and hygiene are supported prevention measures [1].",
                }
            )

        def get_latest_conversation(self) -> dict:
            return self.latest

    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Example infection",
            "target_sections": ["brief", "prevention"],
            "evidence_target_sections": ["brief", "prevention"],
            "_evidence_packet_prepared": True,
        }
    )
    source = {
        "id": 10,
        "source_type": "who",
        "source_name": "WHO",
        "url": "https://example.org/source",
        "status": "active",
        "review_status": "approved",
        "content_text": (
            "This infection is addressed through public-health prevention programmes. "
            "Prevention and control include vaccination and hygiene measures."
        ),
        "metadata": {"relevance_score": 1.0},
    }
    agent = MultiStageRepairingAgent()

    result = asyncio.run(
        AIDiseaseBriefGenerator(agent=agent).generate_with_trace(
            disease=disease,
            sources=[source],
            language="en",
        )
    )

    assert agent.calls == 3
    assert '"failures"' in agent.prompts[1]
    assert '"quality_failures"' in agent.prompts[2]
    assert result["payload"]["prevention"].endswith("[1].")
    assert result["trace"]["quality_repair_attempt_count"] == 1


def test_ai_generator_evicts_completions_when_citation_repair_is_rejected() -> None:
    class RejectedRepairAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.latest = {}
            self.invalidated = []

        def clear_conversation_history(self) -> None:
            self.latest = {}

        async def complete(self, **_kwargs) -> str:
            self.calls += 1
            self.latest = {
                "model": "test-model",
                "provider": "test-provider",
                "tokens": {"total": 10},
                "metadata": {"cache_hit": False},
            }
            return json.dumps(
                {
                    "brief": "A substantive evidence-grounded overview [2].",
                    "definition": None,
                    "clinical_features": None,
                    "epidemiology": None,
                    "transmission": None,
                    "prevention": "An unsupported prevention claim [2].",
                    "surveillance_note": None,
                    "risk_groups": None,
                }
            )

        def get_latest_conversation(self) -> dict:
            return self.latest

        async def invalidate_completion_cache(self, **kwargs) -> bool:
            self.invalidated.append(kwargs)
            return True

    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Example infection",
            "target_sections": ["brief", "prevention"],
            "evidence_target_sections": ["brief", "prevention"],
            "_evidence_packet_prepared": True,
        }
    )
    source = {
        "id": 10,
        "source_type": "who",
        "source_name": "WHO",
        "url": "https://example.org/source",
        "status": "active",
        "review_status": "approved",
        "content_text": "A general source-grounded overview without prevention detail. " * 4,
        "metadata": {"relevance_score": 1.0},
    }
    agent = RejectedRepairAgent()

    result = asyncio.run(
        AIDiseaseBriefGenerator(agent=agent).generate_with_trace(
            disease=disease,
            sources=[source],
            language="en",
        )
    )

    assert agent.calls == 2
    assert result["payload"]["status"] == "draft"
    assert result["payload"]["metadata"]["citation_repair"]["fallback_fields"] == [
        "brief",
        "prevention",
    ]
    assert len(agent.invalidated) == 2


def test_source_refresh_task_forces_source_only(monkeypatch) -> None:
    captured = {}

    async def fake_update(self, disease_id, **kwargs):
        captured["disease_id"] = disease_id
        captured.update(kwargs)
        return {"source_only": kwargs["source_only"], "disease_id": disease_id}

    monkeypatch.setattr(DiseaseKnowledgeUpdateService, "update_disease", fake_update)
    task = SimpleNamespace(
        task_uuid="source-refresh-task",
        input_data={"disease_id": "D001", "source_groups": ["who"], "force": False},
    )

    service = object.__new__(DiseaseKnowledgeUpdateService)
    result = asyncio.run(service.execute_source_refresh_task(task))

    assert result == {"source_only": True, "disease_id": "D001"}
    assert captured["enabled_sources"] == ["who"]
    assert captured["force"] is False
    assert captured["source_only"] is True
    assert captured["refresh_existing_on_source_change"] is False


def test_non_public_knowledge_update_skips_source_and_model_work(monkeypatch) -> None:
    service = object.__new__(DiseaseKnowledgeUpdateService)
    monkeypatch.setattr(
        service,
        "_find_disease",
        lambda _disease_id: {
            "disease_id": "D999",
            "name_en": "Total",
            "category": "Summary",
            "description": "Aggregate total for reporting",
        },
    )

    result = asyncio.run(
        service.update_disease("D999", dry_run=True, source_only=True)
    )

    assert result == {
        "disease_id": "D999",
        "skipped": True,
        "skip_reason": "non_public_disease_id",
        "archived_profile_count": 0,
        "source_only": True,
    }


def test_non_public_source_refresh_does_not_enqueue_a_model_followup(monkeypatch) -> None:
    async def fake_update(self, disease_id, **kwargs):
        return {
            "disease_id": disease_id,
            "source_only": True,
            "skipped": True,
            "skip_reason": "non_public_disease_id",
        }

    monkeypatch.setattr(DiseaseKnowledgeUpdateService, "update_disease", fake_update)
    task = SimpleNamespace(
        task_uuid="non-public-source-refresh",
        input_data={
            "disease_id": "D999",
            "enqueue_ai_after_source_refresh": True,
        },
    )

    service = object.__new__(DiseaseKnowledgeUpdateService)
    result = asyncio.run(service.execute_source_refresh_task(task))

    assert result["skipped"] is True
    assert result["skip_reason"] == "non_public_disease_id"


def test_superseded_automatic_model_repair_skips_the_model_call(monkeypatch) -> None:
    async def superseded(*_args, **_kwargs):
        return True

    async def unexpected_update(*_args, **_kwargs):
        raise AssertionError("superseded work must not call the model pipeline")

    async def fake_log(*_args, **_kwargs):
        return None

    async def fake_progress(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "src.services.disease_knowledge_service._automatic_model_repair_is_superseded",
        superseded,
    )
    monkeypatch.setattr(DiseaseKnowledgeUpdateService, "update_disease", unexpected_update)
    monkeypatch.setattr("src.services.disease_knowledge_service._log_task", fake_log)
    monkeypatch.setattr(
        "src.services.disease_knowledge_service.task_manager.update_task_progress",
        fake_progress,
    )
    task = SimpleNamespace(
        task_uuid="superseded-model-repair",
        input_data={
            "disease_id": "D146",
            "targeted_repair": True,
            "source_refreshed_task_uuid": "source-certificate",
        },
        tags=["knowledge", "auto_repair"],
    )

    result = asyncio.run(DiseaseKnowledgeUpdateService().execute_task(task))

    assert result == {
        "disease_id": "D146",
        "skipped": True,
        "skip_reason": "superseded_by_newer_published_profile",
        "source_refreshed_task_uuid": "source-certificate",
    }


def test_source_gap_invalidation_archives_non_public_catalogue_rows(monkeypatch) -> None:
    service = object.__new__(DiseaseKnowledgeUpdateService)
    monkeypatch.setattr(
        service,
        "_find_disease",
        lambda _disease_id: {
            "disease_id": "D999",
            "name_en": "Total",
            "category": "Summary",
            "description": "Aggregate total for reporting",
        },
    )
    archived = []

    async def fake_archive(_db, disease_id, reason):
        archived.append((disease_id, reason))
        return 2

    monkeypatch.setattr(
        "src.services.disease_knowledge_service._archive_non_public_disease_briefs",
        fake_archive,
    )

    class Database:
        async def flush(self):
            return None

    result = asyncio.run(
        service.invalidate_profiles_with_unresolved_source_gaps(
            Database(),
            {
                "D999": SimpleNamespace(
                    output_data={"source_gap": True},
                )
            },
        )
    )

    assert archived == [("D999", "non_public_disease_id")]
    assert result == {
        "affected_disease_count": 1,
        "updated_brief_count": 0,
        "retained_brief_count": 0,
        "archived_profile_count": 2,
    }


def test_source_refresh_queues_bounded_targeted_evidence_recovery(monkeypatch) -> None:
    captured = {}

    async def fake_update(self, disease_id, **kwargs):
        captured["update"] = {"disease_id": disease_id, **kwargs}
        return {
            "source_only": True,
            "disease_id": disease_id,
            "source_gap": True,
            "uncovered_sections": ["epidemiology", "prevention"],
        }

    async def fake_create_task(**kwargs):
        captured["create"] = kwargs
        return SimpleNamespace(task_uuid="evidence-followup")

    async def fake_workbook(*args, **kwargs):
        captured["workbook"] = {"args": args, **kwargs}

    async def fake_update_status(*_args, **_kwargs):
        return None

    async def fake_merge_metadata(*args, **kwargs):
        captured["metadata"] = {"args": args, **kwargs}
        return None

    monkeypatch.setattr(DiseaseKnowledgeUpdateService, "update_disease", fake_update)
    monkeypatch.setattr(
        "src.services.disease_knowledge_service.task_manager.create_task",
        fake_create_task,
    )
    monkeypatch.setattr(
        "src.services.disease_knowledge_service.task_manager.add_workbook_entry",
        fake_workbook,
    )
    monkeypatch.setattr(
        "src.services.disease_knowledge_service.task_manager.update_task_status",
        fake_update_status,
    )
    monkeypatch.setattr(
        "src.services.disease_knowledge_service.task_manager.merge_task_metadata",
        fake_merge_metadata,
    )
    monkeypatch.setattr(
        "src.services.disease_knowledge_service.get_config",
        lambda: SimpleNamespace(
            ai=SimpleNamespace(knowledge_source_discovery_max_rounds=4)
        ),
    )
    task = SimpleNamespace(
        task_uuid="source-refresh-task",
        input_data={
            "disease_id": "D001",
            "source_groups": ["who"],
            "enqueue_ai_after_source_refresh": True,
            "source_discovery_round": 1,
        },
        tags=["auto_repair"],
    )

    service = object.__new__(DiseaseKnowledgeUpdateService)
    result = asyncio.run(service.execute_source_refresh_task(task))

    assert result["automation_state"] == "awaiting_evidence_refresh"
    assert result["source_followup_task_uuid"] == "evidence-followup"
    assert captured["update"]["requested_sections_by_language"] is None
    assert captured["create"]["task_type"] == TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES
    assert captured["create"]["input_data"]["source_discovery_round"] == 2
    assert captured["create"]["input_data"]["repair_sections_by_language"] == {
        "en": ["epidemiology", "prevention"],
        "zh": ["epidemiology", "prevention"],
    }
    assert captured["metadata"]["args"][0] == "source-refresh-task"


def test_ai_generator_uses_one_compact_retry_for_invalid_json() -> None:
    class FormattingAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.history = []

        def clear_conversation_history(self) -> None:
            self.history = []

        async def complete(self, **kwargs) -> str:
            self.calls += 1
            response = (
                "brief: malformed response"
                if self.calls == 1
                else json.dumps(
                    {
                        "brief": "A substantive source-grounded overview of this infection [1].",
                        "definition": None,
                        "clinical_features": None,
                        "epidemiology": None,
                        "transmission": None,
                        "prevention": "Vaccination and hygiene are supported prevention measures [1].",
                        "surveillance_note": None,
                        "risk_groups": None,
                    }
                )
            )
            self.history.append(
                {
                    "model": "test-model",
                    "provider": "test-provider",
                    "tokens": {"prompt": 5, "completion": 5, "total": 10},
                    "duration": 0.01,
                    "metadata": {"cache_hit": False},
                    "prompt": kwargs["prompt"],
                    "response": response,
                }
            )
            return response

        def get_latest_conversation(self) -> dict:
            return self.history[-1]

        def get_conversation_history(self) -> list[dict]:
            return list(self.history)

    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Example infection",
            "target_sections": ["brief", "prevention"],
            "evidence_target_sections": ["brief", "prevention"],
            "_evidence_packet_prepared": True,
        }
    )
    source = {
        "id": 10,
        "source_type": "who",
        "source_name": "WHO",
        "url": "https://example.org/source",
        "status": "active",
        "review_status": "approved",
        "content_text": "Prevention and control include vaccination and hygiene measures. " * 4,
        "metadata": {"relevance_score": 1.0},
    }
    agent = FormattingAgent()

    result = asyncio.run(
        AIDiseaseBriefGenerator(agent=agent).generate_with_trace(
            disease=disease,
            sources=[source],
            language="en",
        )
    )

    assert agent.calls == 2
    assert result["trace"]["format_repair_attempted"] is True
    assert result["trace"]["citation_repair_attempted"] is False
    assert result["trace"]["interaction_metrics"]["token_usage"]["total"] == 20
    assert len(result["trace"]["format_repair_prompt"]) < len(result["trace"]["prompt"])
    assert 'Target sections: ["brief", "prevention"]' in result["trace"]["format_repair_prompt"]


def test_ai_generator_retries_format_repair_on_an_alternate_model() -> None:
    assert AIDiseaseBriefGenerator._repair_preferred_models(
        ["first", "second", "third"],
        "second",
    ) == ["first", "third", "second"]


def test_quality_gate_rejects_metadata_and_absence_prose() -> None:
    payload = {
        "language": "en",
        "status": "published",
        "source_confidence": "medium",
        "brief": (
            "The evidence boundary consists mainly of scholarly metadata and article titles. "
            "The snippets do not describe clinical features, transmission, prevention, or burden."
        ),
        "definition": "The available records mainly point to review literature on this topic.",
        "clinical_features": "Source-backed clinical detail is not yet available.",
        "epidemiology": "No epidemiologic detail can be stated from the supplied evidence.",
        "transmission": "The source snippets do not describe transmission.",
        "prevention": "A publication title discusses vaccines, but details are not yet available.",
        "surveillance_note": "This record should be read as a placeholder.",
        "risk_groups": "Risk group assignment would be speculative.",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "url": "https://example.org/article"}],
    }

    cleaned, assessment = apply_knowledge_quality_gate(payload)

    assert assessment.display_mode == "blocked"
    assert assessment.profile_available is False
    assert cleaned["status"] == "draft"
    assert cleaned["definition"] is None
    assert cleaned["clinical_features"] is None
    assert cleaned["metadata"]["knowledge_schema_version"] == KNOWLEDGE_SCHEMA_VERSION


def test_quality_gate_requires_required_sections_for_publication() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    payload = {
        "language": "en",
        "status": "published",
        "source_confidence": "high",
        "brief": "Example infection has a source-grounded public-health profile [1].",
        "definition": "The supporting source defines the condition [1].",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "citation_index": 1, "url": "https://example.org"}],
        "metadata": {"profile_schema": disease["profile_schema"]},
    }

    cleaned, assessment = apply_knowledge_quality_gate(payload)

    assert assessment.display_mode == "partial"
    assert assessment.publishable is False
    assert "clinical_features" in assessment.missing_required_fields
    assert cleaned["status"] == "draft"
    assert cleaned["quality_score"] < 0.85


def test_quality_cleanup_keeps_supported_sentence_and_removes_dominant_limitations() -> None:
    text = (
        "The supplied snippets do not identify a formal risk-group list. "
        "People exposed to infected fleas or mammals in established foci have source-backed ecological exposure [1]. "
        "More detailed age or comorbidity information is not yet available [1]."
    )

    cleaned = strip_unavailable_knowledge_sentences(text, "en")

    assert cleaned == "People exposed to infected fleas or mammals in established foci have source-backed ecological exposure [1]."


def test_semantic_quality_rejects_bilingual_missing_field_variants() -> None:
    english = (
        "No geographic distribution or surveillance burden is described in the supplied sources. "
        "The available records are scholarly citations rather than epidemiologic studies. "
        "Source-backed epidemiologic detail is not yet available."
    )
    chinese = (
        "所给来源未包含地理分布、暴发背景或监测负担等流行病学数据。"
        "目前也没有可直接引用的证据说明该类目具有特定季节性模式。"
        "具体监测规模和流行特征尚缺乏源支持。"
    )

    assert assess_knowledge_field(english, "en").available is False
    assert assess_knowledge_field(chinese, "zh").available is False


def test_quality_gate_detects_cross_language_fallback() -> None:
    result = assess_knowledge_field(
        "This English paragraph must not silently appear in the Chinese disease profile.",
        "zh",
    )

    assert result.status == "language_mismatch"
    assert result.available is False


def test_grounding_content_excludes_title_only_metadata() -> None:
    assert not has_grounding_content({"raw_excerpt": "Update on enterovirus 71 infection."})
    assert not has_grounding_content(
        {
            "source_type": "web_search",
            "content_text": "Scholarly metadata: a long article title with publisher, journal, year, and DOI fields.",
            "metadata": {"content_kind": "scholarly_metadata"},
        }
    )
    assert not has_grounding_content(
        {
            "source_type": "pubmed",
            "content_text": "Review article: Update on enterovirus 71 infection",
            "raw_excerpt": "Author et al. Update on enterovirus 71 infection. Journal. 2014.",
        }
    )
    assert has_grounding_content(
        {
            "content_text": (
                "Enterovirus surveillance reports describe the disease entity, its observed clinical pattern, "
                "and the public-health context needed for a grounded summary."
            )
        }
    )


def test_evidence_gate_requires_substantive_source_diversity() -> None:
    wikipedia = {
        "source_type": "wikipedia",
        "status": "active",
        "review_status": "approved",
        "url": "https://en.wikipedia.org/wiki/Example_disease",
        "content_text": "A grounded entity summary with epidemiologic and clinical context. " * 12,
    }
    pubmed = {
        "source_type": "pubmed",
        "status": "active",
        "review_status": "approved",
        "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
        "content_text": "This abstract describes transmission, manifestations, prevention, and surveillance. " * 10,
        "metadata": {"content_kind": "abstract"},
    }

    assert assess_knowledge_evidence([wikipedia]).sufficient is False
    assessment = assess_knowledge_evidence([wikipedia, pubmed])
    assert assessment.sufficient is True
    assert assessment.grounded_source_count == 2
    assert assessment.scholarly_source_count == 1


def test_evidence_gate_rejects_low_relevance_historical_sources() -> None:
    low_relevance = {
        "source_type": "pubmed",
        "status": "active",
        "review_status": "approved",
        "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
        "content_text": "A long but unrelated abstract. " * 60,
        "metadata": {"content_kind": "abstract", "relevance_score": 0.45},
    }

    assessment = assess_knowledge_evidence([low_relevance, low_relevance])

    assert assessment.sufficient is False
    assert assessment.grounded_source_count == 0


def test_evidence_gate_does_not_count_overlapping_storage_views_three_times() -> None:
    text = "Authoritative disease facts covering definition and prevention. " * 7
    source = {
        "source_type": "who",
        "status": "active",
        "review_status": "approved",
        "url": "https://www.who.int/example",
        "content_text": text,
        "raw_excerpt": text,
        "content_sections": [{"heading": "Overview", "text": text}],
    }

    assessment = assess_knowledge_evidence([source])

    assert assessment.content_characters == len(text.strip())
    assert assessment.sufficient is False


def test_evidence_manifest_budget_is_balanced_and_id_stable() -> None:
    schema = resolve_knowledge_profile_schema({"name_en": "Example infection"})
    sources = []
    for index in range(1, 9):
        sources.append(
            {
                "id": index,
                "content_text": (
                    f"Source {index} definition, symptoms, epidemiology, transmission, prevention, "
                    "surveillance and risk population evidence. " * 24
                ),
                "content_sections": [
                    {
                        "heading": f"Prevention {section_index}",
                        "text": (
                            f"Source {index} unique prevention and surveillance section {section_index}. "
                            * 35
                        ),
                    }
                    for section_index in range(4)
                ],
            }
        )
    targets = ["brief", *schema.applicable_fields]

    manifest = build_evidence_manifest(
        sources,
        schema,
        target_sections=targets,
    )
    changed_ids = build_evidence_manifest(
        [{**source, "id": source["id"] + 10_000} for source in sources],
        schema,
        target_sections=targets,
    )

    assert sum(len(fragment.text) for fragment in manifest.fragments) <= (
        MAX_EVIDENCE_MANIFEST_CHARACTERS
    )
    assert {fragment.citation_ref for fragment in manifest.fragments} == set(range(1, 9))
    assert manifest.manifest_id == changed_ids.manifest_id


def test_evidence_packet_deduplicates_canonical_pages_and_preserves_authority() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    schema = resolve_knowledge_profile_schema(disease)
    content = "Definition, clinical features, epidemiology, transmission, prevention, surveillance and risk groups. " * 12
    sources = [
        {
            "id": 1,
            "source_type": "web_search",
            "status": "active",
            "review_status": "approved",
            "url": "https://www.who.int/fact/example?utm_source=search",
            "resolved_url": "https://www.who.int/fact/example",
            "content_text": content,
            "metadata": {"relevance_score": 0.9},
        },
        {
            "id": 2,
            "source_type": "who",
            "status": "active",
            "review_status": "approved",
            "url": "https://www.who.int/fact/example",
            "content_text": content,
            "metadata": {"relevance_score": 1.0},
        },
    ]

    packet = prepare_evidence_packet(
        sources,
        schema,
        target_sections=["brief", *schema.required_fields],
    )

    assert [source["id"] for source in packet.sources] == [2]
    assert packet.assessment.grounded_source_count == 1


def test_profile_schema_resolution_is_entity_semantic_not_disease_id_specific() -> None:
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Unspecified viral condition"}
    ).profile_type == "classification_scope"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Work-related respiratory condition"}
    ).profile_type == "occupational_condition"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Domestic violence event"}
    ).profile_type == "violence_event"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Exogenous poisoning"}
    ).profile_type == "injury_poisoning_event"
    assert resolve_knowledge_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Hepatitis B",
            "category": "Viral",
            "description": "Clinical course unspecified by the reporting source",
        }
    ).profile_type == "infectious_disease"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Botulism", "name_zh": "肉毒杆菌中毒", "category": "Bacterial"}
    ).profile_type == "infectious_disease"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Example clinical syndrome", "category": "Bacterial"}
    ).profile_type == "clinical_syndrome_outcome"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Example post-exposure prophylaxis"}
    ).profile_type == "public_health_intervention"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Foodborne disease outbreak", "category": "Bacterial"}
    ).profile_type == "outbreak_event"
    assert resolve_knowledge_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Child exposed to HIV",
            "description": "Perinatal HIV exposure notification; not an infection diagnosis",
        }
    ).profile_type == "surveillance_event"
    assert resolve_knowledge_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "MRSA surveillance",
            "category": "Bacterial",
            "description": "Source-defined surveillance reports that may include colonization",
        }
    ).profile_type == "surveillance_event"
    assert resolve_knowledge_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Serious work accident",
            "description": "SINAN occupational health concept for serious work accidents",
        }
    ).profile_type == "surveillance_event"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Exanthematous diseases", "description": "Aggregate national surveillance concept"}
    ).profile_type == "classification_scope"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Meningitis (all reported etiologies)"}
    ).profile_type == "classification_scope"
    assert resolve_knowledge_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Source-reported combined infection category",
            "category": "Viral",
            "ontology_context": {
                "facet_tags": {
                    "surveillance_scope": ["surveillance_scope.aggregate"],
                }
            },
        }
    ).profile_type == "classification_scope"
    assert resolve_knowledge_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Condition-specific infection",
            "category": "Viral",
            "ontology_context": {
                "facet_tags": {
                    "surveillance_scope": ["surveillance_scope.condition_specific"],
                }
            },
        }
    ).profile_type == "infectious_disease"


def test_classification_profile_excludes_not_applicable_fields_from_completeness() -> None:
    disease = attach_profile_schema(
        {"disease_id": "ANY", "name_en": "Other classified infections"}
    )
    payload = {
        "language": "en",
        "status": "published",
        "brief": "This category groups source-defined surveillance conditions [1].",
        "definition": "The cited classification defines a residual surveillance category [1].",
        "clinical_features": "The category includes conditions assigned by the source classification boundary [1].",
        "epidemiology": "Reported burden reflects the composition of included conditions and coding practice [1].",
        "surveillance_note": "Trend interpretation must retain the source classification and reporting scope [1].",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "url": "https://example.org/classification"}],
        "metadata": {"profile_schema": disease["profile_schema"]},
    }

    assessment = assess_knowledge_brief(payload, "en", disease=disease)

    assert assessment.display_mode == "full"
    assert assessment.not_applicable_fields == ("prevention", "risk_groups")
    assert assessment.fields["prevention"].status == "not_applicable"
    assert assessment.missing_required_fields == ()


def test_classification_profile_requires_only_a_source_grounded_definition() -> None:
    disease = attach_profile_schema(
        {"disease_id": "ANY", "name_en": "Other classified infections"}
    )
    payload = {
        "language": "en",
        "status": "published",
        "brief": "This source-defined category has a classification overview [1].",
        "definition": "The cited classification defines this residual category [1].",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "url": "https://example.org/classification"}],
        "metadata": {"profile_schema": disease["profile_schema"]},
    }

    assessment = assess_knowledge_brief(payload, "en", disease=disease)

    assert assessment.required_fields == ("definition",)
    assert assessment.publishable


def test_targeted_queries_and_inherited_evidence_are_section_scoped() -> None:
    queries = DiseaseKnowledgeFetcher._web_search_queries(
        ["Example condition"], ["prevention"]
    )
    assert any("prevention control vaccination" in query for query in queries)
    assert len(queries) <= 3
    assert len(
        DiseaseKnowledgeFetcher._pubmed_search_terms(
            ["Example condition"], ["prevention"]
        )
    ) == 2

    schema = resolve_knowledge_profile_schema({"name_en": "Example infection"})
    manifest = build_evidence_manifest(
        [
            {
                "id": 9,
                "content_sections": [
                    {
                        "heading": "Symptoms and transmission",
                        "text": "Clinical symptoms occur, and transmission follows close exposure.",
                    }
                ],
                "metadata": {
                    "inherited_from_disease_id": "PARENT",
                    "allowed_sections": ["transmission"],
                },
            }
        ],
        schema,
        target_sections=["clinical_features", "transmission"],
    )

    assert manifest.fragments[0].supported_sections == ("transmission",)
    assert manifest.fragments[0].inherited_from_disease_id == "PARENT"


def test_evidence_coverage_rejects_absence_claims_and_weak_disease_name_terms() -> None:
    schema = resolve_knowledge_profile_schema({"name_en": "Endemic typhus fever"})
    unsupported = build_evidence_manifest(
        [
            {
                "id": 1,
                "content_text": (
                    "Endemic typhus fever. No population-level incidence or burden estimate "
                    "is present in the provided material."
                ),
            }
        ],
        schema,
        target_sections=["epidemiology"],
    )
    supported = build_evidence_manifest(
        [
            {
                "id": 2,
                "content_text": (
                    "Endemic typhus fever occurs in rural regions and reported cases are "
                    "concentrated in affected countries."
                ),
            }
        ],
        schema,
        target_sections=["epidemiology"],
    )

    assert unsupported.fragments == ()
    assert supported.fragments[0].supported_sections == ("epidemiology",)


def test_evidence_coverage_recognizes_concrete_route_and_quantified_claims() -> None:
    schema = resolve_knowledge_profile_schema({"name_en": "Example infection"})
    manifest = build_evidence_manifest(
        [
            {
                "id": 1,
                "content_text": (
                    "The infection is caused by contamination of a wound with spores. "
                    "It may affect about 3% of the world's population."
                ),
            }
        ],
        schema,
        target_sections=["epidemiology", "transmission"],
    )

    assert manifest.fragments[0].supported_sections == ("epidemiology", "transmission")


def test_evidence_scope_accepts_safe_specific_title_variants() -> None:
    schema = resolve_knowledge_profile_schema({"name_en": "Plasmodium falciparum malaria"})
    manifest = build_evidence_manifest(
        [
            {
                "id": 1,
                "title": "Plasmodium falciparum",
                "content_text": (
                    "Plasmodium falciparum is transmitted through the bite of a female "
                    "Anopheles mosquito and causes the deadliest form of malaria."
                ),
            }
        ],
        schema,
        target_sections=["clinical_features", "transmission"],
        entity_aliases=["Plasmodium falciparum malaria"],
    )

    assert manifest.fragments[0].supported_sections == (
        "clinical_features",
        "transmission",
    )


def test_evidence_coverage_scans_beyond_introductory_source_text() -> None:
    schema = resolve_knowledge_profile_schema({"name_en": "Example infection"})
    manifest = build_evidence_manifest(
        [
            {
                "id": 1,
                "title": "Example infection",
                "content_text": (
                    "Example infection is a bacterial condition. "
                    + "Background information. " * 35
                    + "It spreads by direct person-to-person contact via respiratory droplets."
                ),
            }
        ],
        schema,
        target_sections=["transmission"],
        entity_aliases=["Example infection"],
    )

    assert manifest.fragments[0].supported_sections == ("transmission",)


def test_source_discovery_distinguishes_transport_failure_from_evidence_gap() -> None:
    assert _source_discovery_state({"source_gap": False}) == "ready_for_generation"
    assert _source_discovery_state(
        {"source_gap": True, "adapter_outcomes": {"pubmed": "timeout"}}
    ) == "awaiting_source_transport"
    assert _source_discovery_state(
        {"source_gap": True, "adapter_outcomes": {"pubmed": "success_empty"}}
    ) == "awaiting_evidence"
    assert _source_discovery_state(
        {
            "source_gap": True,
            "adapter_outcomes": {
                "wikipedia": "timeout",
                "who": "success_empty",
                "pubmed": "success",
            },
        }
    ) == "awaiting_evidence"
    assert _source_discovery_state(
        {
            "source_gap": True,
            "adapter_outcomes": {
                "wikipedia": "cooldown",
                "pubmed": "busy",
            },
        }
    ) == "awaiting_source_transport"
    assert _source_transport_retry_delay_seconds(
        attempt=3,
        initial_delay_seconds=300,
        maximum_delay_seconds=21600,
    ) == 1200


def test_publication_evidence_sections_always_include_the_complete_required_profile() -> None:
    schema = resolve_knowledge_profile_schema({"name_en": "Example infection"})

    assert _publication_evidence_sections(schema) == [
        "brief",
        "definition",
        "clinical_features",
        "epidemiology",
        "transmission",
        "prevention",
    ]


def test_evidence_scope_rejects_related_disease_sections() -> None:
    schema = resolve_knowledge_profile_schema({"name_en": "Endemic typhus fever"})
    broad = build_evidence_manifest(
        [
            {
                "id": 1,
                "title": "Typhus",
                "content_sections": [
                    {
                        "heading": "Epidemiology",
                        "text": "Cases of epidemic typhus have been reported in several regions.",
                    }
                ],
            }
        ],
        schema,
        target_sections=["epidemiology"],
        entity_aliases=["Endemic typhus fever", "Murine typhus"],
    )
    scoped = build_evidence_manifest(
        [
            {
                "id": 2,
                "title": "Murine typhus",
                "content_sections": [
                    {
                        "heading": "Epidemiology",
                        "text": "Cases have been reported in several affected regions.",
                    }
                ],
            }
        ],
        schema,
        target_sections=["epidemiology"],
        entity_aliases=["Endemic typhus fever", "Murine typhus"],
    )

    assert broad.fragments == ()
    assert scoped.fragments[0].supported_sections == ("epidemiology",)


def test_entity_query_aliases_include_reviewed_source_series_labels() -> None:
    service = DiseaseKnowledgeUpdateService()

    disease = service._find_disease("D169")

    assert "Murine Typhus" in disease["query_aliases"]


def test_discovered_aliases_do_not_promote_a_broad_related_page() -> None:
    fetcher = DiseaseKnowledgeFetcher(source_hints_path=None)
    aliases = fetcher._discovered_query_aliases(
        {
            "name_en": "Endemic typhus fever",
            "query_aliases": ["Murine typhus"],
        },
        [
            SourceCandidate(
                disease_id="D169",
                source_type="wikipedia",
                source_name="Wikipedia",
                url="https://en.wikipedia.org/wiki/Typhus",
                title="Typhus - Wikipedia",
                content_text="Typhus is a group of distinct diseases.",
                review_status="approved",
            )
        ],
    )

    assert aliases == []


def test_translation_source_requires_available_target_fields() -> None:
    result = {
        "payload": {
            "language": "en",
            "brief": "A source-grounded overview of the condition [1].",
            "epidemiology": "No population-level incidence or burden estimate is present in the provided material.",
            "source_attribution": [{"source_id": 1, "url": "https://example.org/source"}],
            "metadata": {"citation_repair": {"final_failures": []}},
        },
        "trace": {"error": None, "citation_failures": []},
    }

    assert not _translation_source_usable(result, target_sections=["epidemiology"])


def test_post_repair_publishable_payload_is_promoted_from_stale_draft() -> None:
    payload = {
        "status": "draft",
        "metadata": {
            "automation_state": "awaiting_evidence",
            "block_reason": "citation_validation_failed",
        },
    }

    AIDiseaseBriefGenerator._promote_publishable_payload(
        payload,
        assessment=SimpleNamespace(publishable=True),
        citation_failures=[],
    )

    assert payload["status"] == "published"
    assert "automation_state" not in payload["metadata"]
    assert "block_reason" not in payload["metadata"]
    assert payload["metadata"]["publication_status_recovered"] == "post_repair_quality_gate"


def test_optional_fields_do_not_demote_a_complete_narrow_profile() -> None:
    payload = {
        "language": "en",
        "status": "published",
        "brief": "This surveillance entity has a source-grounded overview [1].",
        "definition": "This surveillance entity is defined by its reporting criteria [1].",
        "clinical_features": None,
        "epidemiology": None,
        "transmission": None,
        "prevention": None,
        "surveillance_note": None,
        "risk_groups": None,
        "source_confidence": "medium",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "citation_index": 1, "url": "https://example.org"}],
        "metadata": {
            "profile_schema": {
                "profile_type": "surveillance_event",
                "required_fields": ["definition"],
                "optional_fields": ["clinical_features", "epidemiology", "transmission", "surveillance_note", "risk_groups"],
                "not_applicable_fields": ["prevention"],
            }
        },
    }

    cleaned, assessment = apply_knowledge_quality_gate(payload)

    assert assessment.publishable
    assert cleaned["status"] == "published"
    assert cleaned["quality_score"] >= 0.85


def test_retained_evidence_republishes_only_when_every_cited_source_is_active() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    fields = {
        "brief": "A current, source-grounded overview [1].",
        "definition": "A current, source-grounded definition [1].",
        "clinical_features": "Current clinical features are source grounded [1].",
        "epidemiology": "Current epidemiology is source grounded [1].",
        "transmission": "Current transmission is source grounded [1].",
        "prevention": "Current prevention is source grounded [1].",
    }
    brief = SimpleNamespace(
        language="en",
        status="draft",
        **fields,
        source_attribution=[{"source_id": 101, "citation_index": 1}],
        metadata_={
            "automation_state": "awaiting_evidence",
            "block_reason": "section_coverage_missing",
            "profile_schema": disease["profile_schema"],
            "evidence_manifest": {
                "fragments": [
                    {
                        "source_id": 101,
                        "supported_sections": [
                            "brief",
                            *disease["profile_schema"]["required_fields"],
                        ],
                    }
                ]
            },
        },
    )

    retained = _assess_retained_profile_evidence(brief, disease, {101})
    assert retained.eligible
    assert not _assess_retained_profile_evidence(brief, disease, set()).eligible

    restored = _restore_brief_with_retained_evidence(brief, disease, {101})
    assert restored.eligible
    assert brief.status == "published"
    assert "automation_state" not in brief.metadata_
    assert brief.metadata_["pipeline_version"] == KNOWLEDGE_PIPELINE_VERSION


def test_retained_evidence_rebuilds_a_missing_manifest_from_active_sources() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    brief = SimpleNamespace(
        language="en",
        brief="A current source-grounded overview for example infection [1].",
        definition="Example infection is an infectious disease caused by a pathogen [1].",
        clinical_features="Clinical symptoms of example infection include fever and illness [1].",
        epidemiology="Example infection epidemiology includes geographic distribution and reported cases [1].",
        transmission="Example infection transmission occurs through direct contact exposure [1].",
        prevention="Example infection prevention includes vaccination and hygiene [1].",
        source_attribution=[{"source_id": 101, "citation_index": 1}],
        metadata_={"profile_schema": disease["profile_schema"]},
    )
    source_records = {
        101: {
            "id": 101,
            "source_type": "who",
            "source_name": "WHO",
            "title": "Example infection guidance",
            "url": "https://example.org/example-infection",
            "status": "active",
            "review_status": "approved",
            "content_text": (
                "Example infection is an infectious disease caused by a pathogen. "
                "Clinical symptoms include fever and illness. Epidemiology includes "
                "geographic distribution and reported cases. Transmission occurs through "
                "direct contact exposure. Prevention includes vaccination and hygiene."
            ),
            "metadata": {"relevance_score": 1.0},
        }
    }

    retained = _assess_retained_profile_evidence(
        brief,
        disease,
        {101},
        source_records,
    )

    assert retained.eligible
    assert retained.rebuilt_manifest is not None


def test_repair_planner_targets_required_gaps_and_preserves_citation_identity() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    existing = SimpleNamespace(
        language="en",
        brief="Existing overview [1].",
        definition="Existing definition remains source grounded [1].",
        clinical_features="Existing clinical features remain source grounded [1].",
        epidemiology="Existing epidemiology remains source grounded [1].",
        transmission="Existing transmission remains source grounded [1].",
        prevention=None,
        surveillance_note="Existing surveillance interpretation remains source grounded [1].",
        risk_groups="Existing risk groups remain source grounded [1].",
        source_ids=[101],
        source_attribution=[{"source_id": 101, "citation_index": 1, "url": "https://example.org/old"}],
        metadata_={
            "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
            "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
            "evidence_policy_version": EVIDENCE_POLICY_VERSION,
            "citation_version": 2,
        },
    )
    zh_existing = SimpleNamespace(
        **{
            **vars(existing),
            "language": "zh",
            "brief": "现有疾病概述具有来源支持[1]。",
            "definition": "现有疾病定义具有来源支持并保持不变[1]。",
            "clinical_features": "现有临床特征具有来源支持并保持不变[1]。",
            "epidemiology": "现有流行病学信息具有来源支持并保持不变[1]。",
            "transmission": "现有传播信息具有来源支持并保持不变[1]。",
            "prevention": "现有预防信息具有来源支持并保持不变[1]。",
            "surveillance_note": "现有监测说明具有来源支持并保持不变[1]。",
            "risk_groups": "现有风险人群信息具有来源支持并保持不变[1]。",
        }
    )
    targets = _profile_repair_sections([existing, zh_existing], disease)
    assert targets == ["prevention"]
    assert _profile_repair_sections_by_language([existing, zh_existing], disease) == {
        "en": ["prevention"],
        "zh": [],
    }

    zh_existing.status = "draft"
    assert _profile_repair_sections_by_language([existing, zh_existing], disease)["zh"] == [
        "brief",
        *disease["profile_schema"]["required_fields"],
        *disease["profile_schema"]["optional_fields"],
    ]

    merged = _merge_repair_payload(
        {
            "disease_id": "ANY",
            "language": "en",
            "brief": None,
            "prevention": "New prevention evidence is supported by the refreshed source [1].",
            "source_ids": [202],
            "source_attribution": [{"source_id": 202, "citation_index": 1, "url": "https://example.org/new"}],
            "metadata": {"profile_schema": disease["profile_schema"]},
        },
        existing,
        targets,
        disease,
    )

    assert merged["definition"] == "Existing definition remains source grounded [1]."
    assert merged["prevention"] == "New prevention evidence is supported by the refreshed source [2]."
    assert merged["source_ids"] == [101, 202]
    assert merged["status"] == "published"


def test_bilingual_publication_prerequisites_add_only_companion_required_gaps() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    current_metadata = {
        "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
        "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "citation_version": 2,
    }
    complete = SimpleNamespace(
        language="en",
        status="published",
        brief="Existing overview remains source grounded [1].",
        definition="Existing definition remains source grounded [1].",
        clinical_features="Existing clinical features remain source grounded [1].",
        epidemiology="Existing epidemiology remains source grounded [1].",
        transmission="Existing transmission remains source grounded [1].",
        prevention="Existing prevention remains source grounded [1].",
        surveillance_note=None,
        risk_groups=None,
        source_ids=[101],
        source_attribution=[{"source_id": 101, "citation_index": 1}],
        metadata_=current_metadata,
    )
    companion = SimpleNamespace(
        **{
            **vars(complete),
            "language": "zh",
            "status": "draft",
            "brief": "现有疾病概述仍有来源支撑[1]。",
            "definition": "现有定义仍有来源支撑[1]。",
            "clinical_features": "现有临床特征仍有来源支撑[1]。",
            "epidemiology": None,
            "transmission": None,
            "prevention": "现有预防内容仍有来源支撑[1]。",
        }
    )

    prerequisites = _bilingual_publication_prerequisites(
        [complete, companion], disease
    )

    assert prerequisites == {
        "en": [],
        "zh": ["epidemiology", "transmission"],
    }


def test_requested_single_language_repair_keeps_companion_publication_viable() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    current_metadata = {
        "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
        "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "citation_version": 2,
    }
    english = SimpleNamespace(
        language="en",
        status="draft",
        brief="Existing overview remains source grounded [1].",
        definition="Existing definition remains source grounded [1].",
        clinical_features=None,
        epidemiology=None,
        transmission=None,
        prevention="Existing prevention remains source grounded [1].",
        surveillance_note=None,
        risk_groups=None,
        source_ids=[101],
        source_attribution=[{"source_id": 101, "citation_index": 1}],
        metadata_=current_metadata,
    )
    chinese = SimpleNamespace(
        **{
            **vars(english),
            "language": "zh",
            "brief": "现有疾病概述仍有来源支撑[1]。",
            "definition": "现有定义仍有来源支撑[1]。",
            "clinical_features": "现有临床特征仍有来源支撑[1]。",
            "epidemiology": None,
            "transmission": None,
            "prevention": "现有预防内容仍有来源支撑[1]。",
        }
    )
    ordered = [
        "brief",
        *disease["profile_schema"]["required_fields"],
        *disease["profile_schema"]["optional_fields"],
    ]

    resolved = _resolve_repair_sections_by_language(
        [english, chinese],
        disease,
        ordered_sections=ordered,
        force=False,
        target_languages=["en"],
        requested_sections_by_language={
            "en": ["clinical_features", "epidemiology", "transmission"]
        },
    )

    assert resolved == {
        "en": ["clinical_features", "epidemiology", "transmission"],
        "zh": ["epidemiology", "transmission"],
    }


def test_repair_planner_invalidates_profiles_from_old_pipeline_versions() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    complete = SimpleNamespace(
        language="en",
        brief="Existing overview [1].",
        definition="Existing definition remains source grounded [1].",
        clinical_features="Existing clinical features remain source grounded [1].",
        epidemiology="Existing epidemiology remains source grounded [1].",
        transmission="Existing transmission remains source grounded [1].",
        prevention="Existing prevention remains source grounded [1].",
        surveillance_note="Existing surveillance interpretation remains source grounded [1].",
        risk_groups="Existing risk groups remain source grounded [1].",
        source_ids=[101],
        source_attribution=[{"source_id": 101, "citation_index": 1}],
        metadata_={"pipeline_version": 1},
    )
    zh_complete = SimpleNamespace(
        **{
            **vars(complete),
            "language": "zh",
            "brief": "现有疾病概述具有来源支持[1]。",
        }
    )

    targets = _profile_repair_sections_by_language(
        [complete, zh_complete], disease
    )

    expected = [
        "brief",
        *disease["profile_schema"]["required_fields"],
        *disease["profile_schema"]["optional_fields"],
    ]
    assert targets == {"en": expected, "zh": expected}


def test_repair_diagnostics_distinguish_content_gaps_from_policy_revalidation() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    complete = SimpleNamespace(
        language="en",
        status="published",
        brief="Existing overview [1].",
        definition="Existing definition remains source grounded [1].",
        clinical_features="Existing clinical features remain source grounded [1].",
        epidemiology="Existing epidemiology remains source grounded [1].",
        transmission="Existing transmission remains source grounded [1].",
        prevention="Existing prevention remains source grounded [1].",
        surveillance_note="Existing surveillance interpretation remains source grounded [1].",
        risk_groups="Existing risk groups remain source grounded [1].",
        source_ids=[101],
        source_attribution=[{"source_id": 101, "citation_index": 1}],
        metadata_={"pipeline_version": 1},
    )
    current_gap = SimpleNamespace(
        **{
            **vars(complete),
            "language": "zh",
            "brief": "该感染的现有概述具有来源支撑并可用于更新[1]。",
            "definition": "该感染的定义已由可追溯来源明确说明[1]。",
            "clinical_features": "该感染的临床特征已有来源支持的完整描述[1]。",
            "epidemiology": "该感染的流行病学分布已有来源支持的完整描述[1]。",
            "transmission": "该感染的传播路径已有来源支持的完整描述[1]。",
            "prevention": None,
            "surveillance_note": "该感染的监测解释已有来源支持的完整说明[1]。",
            "risk_groups": "该感染的重点风险人群已有来源支持的完整说明[1]。",
            "metadata_": {
                "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
                "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
                "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                "citation_version": 2,
            },
        }
    )

    diagnostics = _profile_repair_diagnostics_by_language(
        [complete, current_gap], disease
    )

    assert diagnostics["en"]["reasons"] == ["evidence_revalidation"]
    assert diagnostics["en"]["missing_required_sections"] == []
    assert diagnostics["zh"]["reasons"] == ["content_gap"]
    assert diagnostics["zh"]["missing_required_sections"] == ["prevention"]
    assert diagnostics["zh"]["sections"] == ["prevention"]


def test_profile_contract_migration_is_a_high_priority_source_first_repair() -> None:
    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Serious work accident",
            "description": "SINAN occupational health concept for serious work accidents",
        }
    )
    current_metadata = {
        "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
        "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "citation_version": 2,
        "profile_schema": {
            "profile_type": "occupational_condition",
            "required_fields": ["definition", "clinical_features", "epidemiology", "transmission", "prevention"],
            "optional_fields": ["surveillance_note", "risk_groups"],
            "not_applicable_fields": [],
        },
    }
    fields = {
        "brief": "A source-grounded occupational health overview [1].",
        "definition": "A registry-backed definition [1].",
        "clinical_features": "Included event scope [1].",
        "epidemiology": "Reported occurrence context [1].",
        "transmission": "Classification boundary [1].",
        "prevention": None,
        "surveillance_note": "Registry interpretation [1].",
        "risk_groups": "Workers covered by the registry [1].",
        "source_ids": [101],
        "source_attribution": [{"source_id": 101, "citation_index": 1}],
        "status": "draft",
        "metadata_": current_metadata,
    }
    english = SimpleNamespace(language="en", **fields)
    chinese = SimpleNamespace(language="zh", **fields)

    diagnostics = _profile_repair_diagnostics_by_language([english, chinese], disease)
    metadata = _profile_repair_metadata([english, chinese], disease)

    assert disease["knowledge_profile_type"] == "surveillance_event"
    assert diagnostics["en"]["reasons"] == [
        "draft_profile",
        "profile_contract_migration",
    ]
    assert metadata["repair_priority"] == "high"


def test_profile_contract_migration_clears_legacy_optional_sections() -> None:
    disease = attach_profile_schema(
        {
            "disease_id": "ANY",
            "name_en": "Serious work accident",
            "description": "SINAN occupational health concept for serious work accidents",
        }
    )
    old_schema = {
        "profile_type": "occupational_condition",
        "required_fields": ["definition", "clinical_features", "epidemiology", "transmission", "prevention"],
        "optional_fields": ["surveillance_note", "risk_groups"],
        "not_applicable_fields": [],
    }
    existing = SimpleNamespace(
        language="en",
        status="draft",
        brief="Legacy overview [1].",
        definition="Legacy definition [1].",
        clinical_features="Legacy clinical content [1].",
        epidemiology="Legacy epidemiology [1].",
        transmission="Legacy exposure content [1].",
        prevention="Legacy prevention content [1].",
        surveillance_note="Legacy surveillance note [1].",
        risk_groups="Legacy risk groups [1].",
        source_attribution=[{"source_id": 1, "citation_index": 1}],
        metadata_={"profile_schema": old_schema},
    )

    merged = _merge_repair_payload(
        {
            "language": "en",
            "brief": "Registry-backed surveillance overview [1].",
            "definition": "Registry-backed surveillance definition [1].",
            "source_attribution": [{"source_id": 1, "citation_index": 1}],
        },
        existing,
        ["brief", "definition"],
        disease,
    )

    for field in disease["profile_schema"]["optional_fields"]:
        assert merged[field] is None


def test_complete_draft_is_normal_revalidation_not_a_high_content_gap() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    current_metadata = {
        "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
        "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "citation_version": 2,
    }
    complete = SimpleNamespace(
        language="en",
        status="draft",
        brief="Existing overview [1].",
        definition="Existing definition remains source grounded [1].",
        clinical_features="Existing clinical features remain source grounded [1].",
        epidemiology="Existing epidemiology remains source grounded [1].",
        transmission="Existing transmission remains source grounded [1].",
        prevention="Existing prevention remains source grounded [1].",
        surveillance_note="Existing surveillance interpretation remains source grounded [1].",
        risk_groups="Existing risk groups remain source grounded [1].",
        source_ids=[101],
        source_attribution=[{"source_id": 101, "citation_index": 1}],
        metadata_=current_metadata,
    )
    translated = SimpleNamespace(
        **{
            **vars(complete),
            "language": "zh",
            "status": "published",
            "brief": "该感染的现有概述具有来源支撑并可用于更新[1]。",
            "definition": "该感染的定义已由可追溯来源明确说明[1]。",
            "clinical_features": "该感染的临床特征已有来源支持的完整描述[1]。",
            "epidemiology": "该感染的流行病学分布已有来源支持的完整描述[1]。",
            "transmission": "该感染的传播路径已有来源支持的完整描述[1]。",
            "prevention": "该感染的预防措施已有来源支持的完整描述[1]。",
            "surveillance_note": "该感染的监测解释已有来源支持的完整说明[1]。",
            "risk_groups": "该感染的重点风险人群已有来源支持的完整说明[1]。",
        }
    )

    metadata = _profile_repair_metadata([complete, translated], disease)

    assert metadata["repair_priority"] == "normal"


def test_repair_enqueue_candidate_selection_is_idempotent_and_priority_ordered() -> None:
    active = SimpleNamespace(
        task_uuid="task-d210",
        status="QUEUED",
        input_data={"disease_id": "D210"},
    )
    catalogue = [
        {
            "disease_id": "D211",
            "repair_priority": "high",
            "repair_sections": ["surveillance_note"],
        },
        {
            "disease_id": "D209",
            "repair_priority": "urgent",
            "repair_sections": ["brief", "definition"],
        },
        {
            "disease_id": "D210",
            "repair_priority": "high",
            "repair_sections": ["risk_groups"],
        },
        {
            "disease_id": "D001",
            "repair_priority": "none",
            "repair_sections": [],
        },
    ]

    selected, skipped = _select_knowledge_repair_candidates(
        catalogue,
        active_by_disease=_active_knowledge_tasks_by_disease([active]),
    )

    assert [item["disease_id"] for item in selected] == ["D209", "D211"]
    assert skipped == [
        {
            "disease_id": "D210",
            "reason": "already_running",
            "existing_task_uuid": "task-d210",
            "existing_status": "QUEUED",
        }
    ]
    assert _knowledge_repair_task_priority(None, "urgent").value == "urgent"
    assert _knowledge_repair_task_priority(None, "high").value == "high"
    assert _knowledge_repair_task_priority("normal", "urgent").value == "normal"


def test_ontology_context_exposes_scoped_parent_for_course_variant() -> None:
    disease = DiseaseKnowledgeUpdateService()._find_disease("D208")
    related = disease["ontology_context"]["related_entities"]

    assert related[0]["disease_id"] == "D008"
    assert related[0]["relation_type"] == "clinical_course_of"
    assert "clinical_features" not in related[0]["allowed_shared_sections"]


def test_numbered_pathogen_aliases_are_generic_and_pubmed_excludes_non_latin_terms() -> None:
    fetcher = DiseaseKnowledgeFetcher(min_interval_seconds=0)
    disease = {
        "disease_id": "DTEST",
        "name_en": "Enterovirus 68 infection",
        "name_zh": "肠道病毒68型感染",
    }

    candidates = fetcher._query_candidates(disease)
    assert "Enterovirus 68" in candidates
    assert "EV68" in candidates
    assert "EV-68" in candidates

    exposure_candidates = fetcher._query_candidates(
        {"disease_id": "DTEST", "name_en": "Child exposed to HIV"}
    )
    assert "HIV-exposed Child" in exposure_candidates

    pubmed_candidates = fetcher._pubmed_query_candidates(
        {
            "disease_id": "DTEST",
            "name_en": "Child exposed to HIV",
            "name_zh": "儿童HIV暴露",
            "description": "A long catalogue boundary that must not become a PubMed term",
        }
    )
    assert "HIV-exposed Child" in pubmed_candidates
    assert "儿童HIV暴露" not in pubmed_candidates
    assert not any("catalogue boundary" in item for item in pubmed_candidates)
    assert fetcher._relevance_score(
        pubmed_candidates,
        "",
        "The HIV-exposed, uninfected African child",
        "",
    ) >= 0.5
    assert fetcher._relevance_score(
        pubmed_candidates,
        "",
        "Canadian guideline on HIV postexposure prophylaxis",
        "Adult prevention recommendations",
    ) < 0.5

    assert "HTLV-1" in fetcher._query_candidates(
        {"disease_id": "DTEST", "name_en": "Human T-lymphotropic virus 1 or 2 infection"}
    )
    assert "penicillin-resistant Streptococcus pneumoniae" in fetcher._query_candidates(
        {
            "disease_id": "DTEST",
            "name_en": "Penicillin-non-susceptible/resistant pneumococcal surveillance",
        }
    )
    assert "Enterovirus A68" not in candidates
    search_terms = fetcher._pubmed_search_terms(candidates)
    assert search_terms
    assert all("肠道病毒" not in term for term in search_terms)


def test_generation_completion_gate_rejects_failed_or_missing_language() -> None:
    result = {
        "payload": {
            "language": "en",
            "status": "requires_review",
            "brief": None,
            "source_ids": [],
            "source_attribution": [],
        },
        "trace": {"error": "model unavailable"},
    }

    failures = _generated_profile_failures([result])
    assert any("generator error" in failure for failure in failures)
    assert any("status is not published" in failure for failure in failures)
    assert any("zh: profile was not generated" == failure for failure in failures)


def test_generation_completion_gate_requires_non_null_brief() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    payload = {
        "disease_id": "ANY",
        "language": "en",
        "status": "published",
        "brief": None,
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "citation_index": 1}],
        "metadata": {"profile_schema": disease["profile_schema"]},
    }
    for field in disease["profile_schema"]["required_fields"]:
        payload[field] = f"Substantive source-grounded {field} content [1]."
    zh_payload = {
        **payload,
        "language": "zh",
        **{
            field: "这是具有来源支持且内容充分的字段说明[1]。"
            for field in disease["profile_schema"]["required_fields"]
        },
    }

    failures = _generated_profile_failures(
        [
            {"payload": payload, "trace": {"error": None}},
            {"payload": zh_payload, "trace": {"error": None}},
        ]
    )

    assert "en: substantive brief is required" in failures
    assert "zh: substantive brief is required" in failures


def test_generation_completion_gate_rejects_grounded_bilingual_partial_profiles() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    shared = {
        "disease_id": "ANY",
        "status": "published",
        "source_ids": [1],
        "source_attribution": [
            {"source_id": 1, "citation_index": 1, "url": "https://example.org/source"}
        ],
        "metadata": {
            "profile_schema": disease["profile_schema"],
            "citation_validation": {"failures": []},
        },
    }
    en_payload = {
        **shared,
        "language": "en",
        "brief": "This is a substantive source-grounded overview of the condition [1].",
        "definition": "This is a substantive source-grounded definition of the condition [1].",
    }
    zh_payload = {
        **shared,
        "language": "zh",
        "brief": "这是对该疾病具有实质内容且可追溯来源的概述[1]。",
        "definition": "这是对该疾病具有实质内容且可追溯来源的定义[1]。",
    }

    assert assess_knowledge_brief(en_payload, "en").display_mode == "partial"
    assert assess_knowledge_brief(zh_payload, "zh").display_mode == "partial"
    failures = _generated_profile_failures(
        [
            {"payload": en_payload, "trace": {"error": None}},
            {"payload": zh_payload, "trace": {"error": None}},
        ]
    )
    assert any("en: missing required sections" in failure for failure in failures)
    assert any("zh: missing required sections" in failure for failure in failures)


def test_progressive_completion_gate_persists_a_valid_target_without_publishing_early() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    shared = {
        "disease_id": "ANY",
        "status": "draft",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "citation_index": 1}],
        "metadata": {
            "profile_schema": disease["profile_schema"],
            "citation_validation": {"failures": []},
        },
    }
    en_payload = {
        **shared,
        "language": "en",
        "brief": "A source-grounded overview [1].",
        "definition": "A source-grounded definition [1].",
        "clinical_features": "Source-grounded clinical detail [1].",
        "epidemiology": "Source-grounded epidemiology [1].",
        "transmission": "Source-grounded transmission detail [1].",
        "prevention": None,
    }
    zh_payload = {
        **shared,
        "language": "zh",
        "brief": "这是具有来源支持的概述[1]。",
        "definition": "这是具有来源支持的定义[1]。",
        "clinical_features": "这是具有来源支持的临床信息[1]。",
        "epidemiology": "这是具有来源支持的流行病学信息[1]。",
        "transmission": "这是具有来源支持的传播信息[1]。",
        "prevention": "这是具有来源支持的预防信息[1]。",
    }

    failures = _generated_profile_failures(
        [
            {"payload": en_payload, "trace": {"error": None}},
            {"payload": zh_payload, "trace": {"error": None}},
        ],
        target_sections_by_language={"en": ["transmission"], "zh": ["prevention"]},
        allow_progressive_drafts=True,
    )

    assert failures == []


def test_progressive_completion_gate_does_not_require_optional_targets() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    payload = {
        "disease_id": "ANY",
        "language": "en",
        "status": "draft",
        "brief": "A source-grounded overview [1].",
        "definition": "A source-grounded definition [1].",
        "source_ids": [1],
        "source_attribution": [{"source_id": 1, "citation_index": 1}],
        "metadata": {
            "profile_schema": disease["profile_schema"],
            "citation_validation": {"failures": []},
        },
    }
    zh_payload = {
        **payload,
        "language": "zh",
        "brief": "这是具有来源支持的概述[1]。",
        "definition": "这是具有来源支持的定义[1]。",
    }

    assert _generated_profile_failures(
        [
            {"payload": payload, "trace": {"error": None}},
            {"payload": zh_payload, "trace": {"error": None}},
        ],
        target_sections_by_language={
            "en": ["surveillance_note", "risk_groups"],
            "zh": ["surveillance_note", "risk_groups"],
        },
        allow_progressive_drafts=True,
    ) == []


def test_repair_merge_marks_valid_partial_progress_for_automatic_follow_up() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    existing = SimpleNamespace(
        language="en",
        status="draft",
        brief="Existing grounded overview [1].",
        definition="Existing grounded definition [1].",
        clinical_features="Existing grounded clinical content [1].",
        epidemiology="Existing grounded epidemiology [1].",
        transmission=None,
        prevention=None,
        surveillance_note=None,
        risk_groups=None,
        source_ids=[1],
        source_attribution=[{"source_id": 1, "citation_index": 1}],
        metadata_={"profile_schema": disease["profile_schema"]},
    )

    merged = _merge_repair_payload(
        {
            "language": "en",
            "transmission": "Grounded transmission detail [1].",
            "source_ids": [2],
            "source_attribution": [{"source_id": 2, "citation_index": 1}],
            "metadata": {"profile_schema": disease["profile_schema"]},
        },
        existing,
        ["transmission"],
        disease,
    )

    assert merged["status"] == "draft"
    assert merged["transmission"] == "Grounded transmission detail [2]."
    assert merged["metadata"]["automation_state"] == "repairing_remaining_sections"
    assert merged["metadata"]["progressive_repair"]["remaining_required_sections"] == [
        "prevention"
    ]


def test_repair_merge_clears_recovery_state_after_full_publication() -> None:
    disease = attach_profile_schema({"disease_id": "ANY", "name_en": "Example infection"})
    existing = SimpleNamespace(
        language="en",
        brief="Existing source-grounded overview remains complete and traceable [1].",
        definition="Existing source-grounded definition remains complete and traceable [1].",
        clinical_features="Existing source-grounded clinical content remains complete and traceable [1].",
        epidemiology="Existing source-grounded epidemiology remains complete and traceable [1].",
        transmission="Existing source-grounded transmission remains complete and traceable [1].",
        prevention=None,
        surveillance_note=None,
        risk_groups=None,
        source_ids=[1],
        source_attribution=[{"source_id": 1, "citation_index": 1, "url": "https://example.org/old"}],
        metadata_={
            "profile_schema": disease["profile_schema"],
            "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
            "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
            "evidence_policy_version": EVIDENCE_POLICY_VERSION,
            "citation_version": 2,
            "automation_state": "awaiting_evidence",
            "block_reason": "missing_required_sections",
        },
    )

    merged = _merge_repair_payload(
        {
            "language": "en",
            "prevention": "Source-grounded prevention detail remains complete and traceable [1].",
            "source_ids": [2],
            "source_attribution": [{"source_id": 2, "citation_index": 1, "url": "https://example.org/new"}],
            "source_confidence": "medium",
            "metadata": {"profile_schema": disease["profile_schema"]},
        },
        existing,
        ["prevention"],
        disease,
    )

    assert merged["status"] == "published"
    assert "automation_state" not in merged["metadata"]
    assert "block_reason" not in merged["metadata"]


def test_citation_validation_requires_inline_and_section_supported_sources() -> None:
    payload = normalize_knowledge_citations(
        {
            "brief": "Grounded overview [1].",
            "transmission": "Unsupported transmission claim [1].",
            "prevention": "Prevention claim without a marker.",
            "source_attribution": [
                {"source_id": 10, "citation_index": 1, "url": "https://example.org"}
            ],
            "metadata": {
                "evidence_manifest": {
                    "fragments": [
                        {
                            "source_id": 10,
                            "supported_sections": ["brief", "definition"],
                        }
                    ]
                }
            },
        },
        marker_mode="position",
        prune_uncited_sources=True,
    )

    failures = validate_knowledge_citations(
        payload,
        fields=["brief", "transmission", "prevention"],
    )

    assert "transmission: citation [1] does not support this section" in failures
    assert "prevention: missing inline citation" in failures


def test_site_disease_knowledge_fields_inject_bilingual_brief_and_sources() -> None:
    disease = {
        "disease_id": "influenza",
        "name_en": "Influenza",
        "name_zh": "流感",
        "description": "Catalogue description.",
    }
    enriched = apply_disease_knowledge_fields(
        disease,
        {
            "en": {
                "brief": "WHO-grounded influenza brief.",
                "definition": "Definition context.",
                "clinical_features": "Clinical context.",
                "epidemiology": "Epidemiology context.",
                "transmission": "Respiratory transmission context.",
                "prevention": "Public-health prevention context.",
                "surveillance_note": "Surveillance note context.",
                "risk_groups": "Source-supported risk groups.",
                "source_attribution": [{"source_name": "WHO", "url": "https://www.who.int"}],
                "updated_at": "2026-04-23T00:00:00",
                "status": "published",
            },
            "zh": {"brief": "基于来源的流感简介。", "status": "published"},
        },
    )

    assert enriched["official_intro_en"] == "WHO-grounded influenza brief."
    assert enriched["official_definition_en"] == "Definition context."
    assert enriched["clinical_features_en"] == "Clinical context."
    assert enriched["epidemiology_en"] == "Epidemiology context."
    assert enriched["official_intro_zh"] is None
    assert enriched["transmission_en"] == "Respiratory transmission context."
    assert enriched["surveillance_note_en"] == "Surveillance note context."
    assert enriched["knowledge_sources"][0]["source_name"] == "WHO"
    assert enriched["knowledge_status"] == "published"
    assert enriched["knowledge_profile_available"] is True
    assert enriched["knowledge_has_authoritative_sources"] is True


def test_site_disease_knowledge_fields_block_legacy_catalogue_content() -> None:
    disease = {
        "disease_id": "D081",
        "name_en": "Unspecified malaria",
        "name_zh": "未明示的疟疾",
        "description": "",
    }
    enriched = apply_disease_knowledge_fields(
        disease,
        {
            "en": {
                "brief": "Fallback brief.",
                "definition": "Fallback definition.",
                "clinical_features": "Fallback clinical features.",
                "epidemiology": "Fallback epidemiology.",
                "transmission": "Fallback transmission.",
                "prevention": "Fallback prevention.",
                "surveillance_note": "Fallback surveillance note.",
                "risk_groups": "Fallback risk groups.",
                "source_attribution": [],
                "updated_at": "2026-04-23T00:00:00",
                "status": "requires_review",
                "metadata": {
                    "brief_tier": LEGACY_CATALOGUE_BRIEF_TIER,
                    "fallback_reason": "metadata_only_sources",
                },
            }
        },
    )

    assert enriched["knowledge_status"] == "blocked"
    assert enriched["knowledge_tier"] == "blocked"
    assert enriched["knowledge_block_reason"] == "metadata_only_sources"
    assert enriched["knowledge_profile_available"] is False
    assert enriched["knowledge_profile_reason"] == "metadata_only_sources"
    assert enriched["official_intro_en"] is None
    assert enriched["official_definition_en"] is None


def test_site_disease_knowledge_fields_blocks_partial_public_non_authoritative_sources() -> None:
    disease = {
        "disease_id": "D003",
        "name_en": "SARS",
        "name_zh": "传染性非典型肺炎",
        "description": "",
    }
    enriched = apply_disease_knowledge_fields(
        disease,
        {
            "en": {
                "brief": "Public-source SARS brief.",
                "definition": "Public-source SARS definition.",
                "source_attribution": [
                    {"source_name": "Wikidata", "source_type": "wikidata", "url": "https://www.wikidata.org/wiki/Q103177"},
                    {"source_name": "Wikipedia", "source_type": "wikipedia", "url": "https://en.wikipedia.org/wiki/SARS"},
                ],
                "source_confidence": "medium",
                "status": "published",
                "updated_at": "2026-04-23T00:00:00",
            }
        },
    )

    assert enriched["knowledge_status"] == "blocked"
    assert enriched["knowledge_tier"] == "blocked"
    assert enriched["knowledge_profile_available"] is False
    assert enriched["knowledge_profile_reason"] == "insufficient_evidence"
    assert enriched["knowledge_display_mode"] == "blocked"
    assert enriched["knowledge_has_authoritative_sources"] is False
    assert enriched["official_intro_en"] is None


def test_site_disease_knowledge_fields_blocks_partial_content_without_placeholders() -> None:
    disease = {"disease_id": "D207", "name_en": "Example infection", "name_zh": "示例感染"}
    enriched = apply_disease_knowledge_fields(
        disease,
        {
            "en": {
                "language": "en",
                "status": "published",
                "brief": "Example infection is a source-documented viral disease [1].",
                "definition": "The cited public-health source defines Example infection as a viral disease [1].",
                "clinical_features": "Source-backed clinical detail is not yet available [1].",
                "transmission": "The supplied snippets do not describe routes of transmission [1].",
                "source_ids": [1],
                "source_attribution": [{"source_id": 1, "source_name": "Public source", "url": "https://example.org"}],
            },
            "zh": {
                "language": "zh",
                "status": "published",
                "brief": "示例感染是具有公开来源支持的病毒性疾病条目[1]。",
                "definition": "公开来源将示例感染定义为一种病毒性疾病[1]。",
                "source_ids": [1],
                "source_attribution": [{"source_id": 1, "source_name": "Public source", "url": "https://example.org"}],
            },
        },
    )

    assert enriched["knowledge_profile_available"] is False
    assert enriched["knowledge_display_mode"] == "blocked"
    assert enriched["clinical_features_en"] is None
    assert enriched["transmission_en"] is None
    assert enriched["clinical_features_zh"] is None
    assert enriched["knowledge_field_status"]["clinical_features"]["en"] == "insufficient_evidence"
    assert enriched["knowledge_language_quality"]["en"]["display_mode"] == "partial"


def test_site_country_brief_fields_fallback_from_source_context() -> None:
    country_data = {
        "country_name": "Exampleland",
        "country_code": "EX",
        "date_range": {"start": "2025-01-01", "end": "2026-01-01"},
        "disease_count": 2,
        "frequency_meta": {"source_frequency": "MONTHLY"},
        "source_info": {"sources": [{"label": "National surveillance bulletin"}]},
    }

    enriched = apply_country_brief_fields(country_data, None)

    assert "Exampleland page consolidates" in enriched["brief_en"]
    assert "National surveillance bulletin" in enriched["surveillance_system_en"]
    assert "2025-01-01 to 2026-01-01" in enriched["interpretation_en"]
    assert "MONTHLY" in enriched["reporting_cadence_en"]
    assert enriched["country_brief_status"] == "fallback"


def test_build_country_data_skips_non_public_summary_records() -> None:
    records = [
        {
            "disease_id": "D001",
            "date": "2026-01-01",
            "year_month": "2026-01",
            "cases": 5,
            "deaths": 1,
            "incidence_rate": 0.5,
            "incidence_rate_source": "wpp",
            "mortality_rate": 0.1,
        },
        {
            "disease_id": "D999",
            "date": "2026-01-01",
            "year_month": "2026-01",
            "cases": 500,
            "deaths": 50,
            "incidence_rate": 5.0,
            "incidence_rate_source": "wpp",
            "mortality_rate": 1.0,
        },
    ]

    payload = build_country_data(
        "CN",
        "China",
        records,
        {
            "D001": {
                "name_en": "Plague",
                "name_zh": "鼠疫",
                "category": "Bacterial",
                "slug": "plague",
            }
        },
    )

    assert payload["total_cases"] == 5
    assert payload["total_deaths"] == 1
    assert "D001" in payload["disease_series"]
    assert "D999" not in payload["disease_series"]


def test_site_disease_knowledge_fields_without_briefs_remain_blocked() -> None:
    disease = {
        "disease_id": "D066",
        "name_en": "Other viral infections characterized by skin lesions",
        "name_zh": "其他以皮损为特征的病毒感染",
        "description": "",
    }

    enriched = apply_disease_knowledge_fields(disease, None)

    assert enriched["knowledge_status"] == "blocked"
    assert enriched["knowledge_profile_available"] is False
    assert enriched["knowledge_profile_reason"] == "no_published_brief"
    assert enriched["official_intro_en"] is None


def test_resolve_knowledge_status_blocks_legacy_catalogue_content() -> None:
    status = resolve_disease_knowledge_status(
        [
            {
                "status": "requires_review",
                "metadata": {"brief_tier": LEGACY_CATALOGUE_BRIEF_TIER},
            }
        ]
    )

    assert status == "blocked"


def test_should_generate_public_disease_page_skips_summary_rows() -> None:
    assert not should_generate_public_disease_page(
        {
            "disease_id": "D999",
            "name_en": "Total",
            "category": "Summary",
            "description": "Aggregate total for reporting",
        }
    )


def test_should_generate_public_disease_page_skips_residual_bucket_rows() -> None:
    still_public_cases = [
        {
            "disease_id": "D066",
            "name_en": "Other viral infections characterized by skin lesions",
            "category": "",
            "description": "",
        },
        {
            "disease_id": "D081",
            "name_en": "Unspecified malaria",
            "category": "Parasitic",
            "description": "",
        },
        {
            "disease_id": "D149",
            "name_en": "Flavivirus infection (unspecified)",
            "category": "Viral",
            "description": "AU NINDSS surveillance concept",
        },
        {
            "disease_id": "D111",
            "name_en": "Novel influenza A (deprecated duplicate)",
            "category": "Viral",
            "description": "Deprecated duplicate of D016; do not use for new mappings",
        },
    ]

    for disease in still_public_cases[:-1]:
        assert should_generate_public_disease_page(disease)
        assert public_disease_page_exclusion_reason(disease) is None

    assert not should_generate_public_disease_page(still_public_cases[-1])
    assert public_disease_page_exclusion_reason(still_public_cases[-1]) is not None

    assert should_generate_public_disease_page(
        {
            "disease_id": "D047",
            "name_en": "Infectious Diarrhea",
            "category": "Bacterial",
            "description": "Other infectious diarrhea",
        }
    )


def test_ai_brief_json_parser_accepts_fenced_json() -> None:
    parsed = AIDiseaseBriefGenerator._parse_json(
        """```json
        {"brief":"A","definition":"B","clinical_features":"C","epidemiology":"D","transmission":"E","prevention":"F","surveillance_note":"G","risk_groups":"H"}
        ```"""
    )

    assert parsed["brief"] == "A"


def test_ai_brief_json_parser_accepts_a_complete_object_with_trailing_model_text() -> None:
    parsed = AIDiseaseBriefGenerator._parse_json(
        '{"brief":"A","definition":"B"}\n\nGeneration complete.'
    )

    assert parsed == {"brief": "A", "definition": "B"}


def test_ai_brief_json_parser_uses_the_first_complete_object_when_two_are_returned() -> None:
    parsed = AIDiseaseBriefGenerator._parse_json(
        '{"brief":"A"}\n{"debug":"do not use"}'
    )

    assert parsed == {"brief": "A"}


def test_ai_settings_knowledge_model_shards_falls_back_to_model_chain() -> None:
    settings = AISettings(
        model_chain_raw="model-a,model-b,model-a",
        knowledge_model_shards_raw="",
    )

    assert settings.model_chain == ["model-a", "model-b"]
    assert settings.knowledge_model_shards == ["model-a", "model-b"]


def test_ai_brief_preferred_models_follow_model_center_shard_rotation(monkeypatch) -> None:
    shard_models = ["model-a", "model-b", "model-c"]
    routes = [
        {
            "model_name": model_name,
            "has_api_key": True,
            "available_for_routing": True,
            "last_check_status": "available",
        }
        for model_name in shard_models
    ]
    async def fake_runtime_routes() -> list[dict[str, object]]:
        return routes

    monkeypatch.setattr(
        "src.knowledge.llm_brief_generator.get_runtime_routes",
        fake_runtime_routes,
    )

    preferred_models, shard_index, shard_key = asyncio.run(
        AIDiseaseBriefGenerator._preferred_models_for(
            disease_id="D001",
            language="zh",
        )
    )
    expected_key = "D001:zh"
    expected_index = int(hashlib.md5(expected_key.encode("utf-8")).hexdigest()[:8], 16) % len(shard_models)
    expected_models = shard_models[expected_index:] + shard_models[:expected_index]

    assert shard_key == expected_key
    assert shard_index == expected_index
    assert preferred_models == expected_models


def test_ai_brief_sharding_keeps_repeatedly_timing_out_routes_as_fallback(monkeypatch) -> None:
    healthy_models = ["model-a", "model-b"]
    routes = [
        {
            "model_name": "model-a",
            "has_api_key": True,
            "available_for_routing": True,
            "last_check_status": "available",
            "runtime_success_count": 24,
            "runtime_failure_count": 1,
            "runtime_timeout_count": 0,
        },
        {
            "model_name": "model-flaky",
            "has_api_key": True,
            "available_for_routing": True,
            "last_check_status": "available",
            "runtime_success_count": 4,
            "runtime_failure_count": 18,
            "runtime_timeout_count": 12,
        },
        {
            "model_name": "model-b",
            "has_api_key": True,
            "available_for_routing": True,
            "last_check_status": "available",
            "runtime_success_count": 14,
            "runtime_failure_count": 2,
            "runtime_timeout_count": 1,
        },
    ]

    async def fake_runtime_routes() -> list[dict[str, object]]:
        return routes

    monkeypatch.setattr(
        "src.knowledge.llm_brief_generator.get_runtime_routes",
        fake_runtime_routes,
    )

    preferred_models, shard_index, shard_key = asyncio.run(
        AIDiseaseBriefGenerator._preferred_models_for(
            disease_id="D119",
            language="en",
        )
    )
    expected_index = int(hashlib.md5(shard_key.encode("utf-8")).hexdigest()[:8], 16) % len(healthy_models)
    expected_healthy_order = healthy_models[expected_index:] + healthy_models[:expected_index]

    assert shard_index == expected_index
    assert preferred_models == [*expected_healthy_order, "model-flaky"]


def test_ai_brief_sharding_does_not_duplicate_when_only_degraded_routes_exist(monkeypatch) -> None:
    shard_models = ["model-a", "model-b"]
    routes = [
        {
            "model_name": model_name,
            "has_api_key": True,
            "available_for_routing": True,
            "last_check_status": "available",
            "runtime_success_count": 1,
            "runtime_failure_count": 8,
            "runtime_timeout_count": 4,
            "runtime_failure_streak": 1,
        }
        for model_name in shard_models
    ]

    async def fake_runtime_routes() -> list[dict[str, object]]:
        return routes

    monkeypatch.setattr(
        "src.knowledge.llm_brief_generator.get_runtime_routes",
        fake_runtime_routes,
    )

    preferred_models, shard_index, shard_key = asyncio.run(
        AIDiseaseBriefGenerator._preferred_models_for(
            disease_id="D119",
            language="en",
        )
    )
    expected_index = int(hashlib.md5(shard_key.encode("utf-8")).hexdigest()[:8], 16) % len(shard_models)
    expected_models = shard_models[expected_index:] + shard_models[:expected_index]

    assert shard_index == expected_index
    assert preferred_models == expected_models


def test_report_v4_disease_context_is_locale_first_and_evidence_bound() -> None:
    document = compose_report_document(
        evidence_packet={
            "summary_metrics": {
                "disease_count": 1,
                "total_cases": 100,
                "latest_cases": 10,
                "high_risk_diseases": 1,
            },
            "death_reporting": {
                "status": "reported_positive",
                "total_deaths": 1,
                "observed_periods": 2,
                "missing_periods": 0,
                "reported_zero_periods": 1,
                "display_note": {
                    "zh": "当前来源报告了死亡数，报告中的死亡指标均来自已存储证据。",
                    "en": "The current source reports death counts; mortality metrics in this report are evidence-bound.",
                },
            },
            "risk_ranking": [
                {
                    "disease_id": "D001",
                    "name_en": "Influenza",
                    "name_zh": "流感",
                    "latest_cases": 10,
                    "risk_level": "high",
                }
            ],
            "diseases": [
                {
                    "disease_id": "D001",
                    "name_en": "Influenza",
                    "name_zh": "流感",
                    "metrics": {"total_cases": 100},
                }
            ],
            "data_quality": {"score": 0.95, "confidence": "high"},
            "evidence_index": {"disease:D001.total_cases": 100},
        },
        country={"name_zh": "测试地区", "name_en": "Testland"},
        period_start=datetime(2026, 1, 1),
        period_end=datetime(2026, 1, 31),
    ).to_dict()

    disease_context = next(section for section in document["sections"] if section["type"] == "disease_context")
    assert disease_context["title"]["zh"] == "疾病背景"
    assert disease_context["title"]["en"] == "Disease Context"
    assert "流感" in disease_context["body"]["zh"]
    assert "Influenza" in disease_context["body"]["en"]
    assert "disease:D001.total_cases" in disease_context["evidence_refs"]
