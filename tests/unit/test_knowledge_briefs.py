import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from requests import Response

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import AISettings
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
    _generated_profile_failures,
    _knowledge_repair_task_priority,
    _merge_repair_payload,
    _profile_repair_sections,
    _profile_repair_sections_by_language,
    _select_knowledge_repair_candidates,
)

from scripts.generate_site_data import (
    apply_country_brief_fields,
    apply_disease_knowledge_fields,
    build_country_data,
)


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
    assert "set it to null" in user_prompt
    assert "absence explanation" in user_prompt
    assert AIDiseaseBriefGenerator._field({"prevention": None}, "prevention") is None


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
    assert result["payload"]["status"] == "requires_review"
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
    assert result["payload"]["status"] == "requires_review"
    assert result["payload"]["metadata"]["citation_repair"]["fallback_fields"] == [
        "brief",
        "prevention",
    ]
    assert len(agent.invalidated) == 2


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
    assert cleaned["status"] == "requires_review"
    assert cleaned["definition"] is None
    assert cleaned["clinical_features"] is None
    assert cleaned["metadata"]["knowledge_schema_version"] == KNOWLEDGE_SCHEMA_VERSION


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
        {"disease_id": "ANY", "name_en": "Exanthematous diseases", "description": "Aggregate national surveillance concept"}
    ).profile_type == "classification_scope"
    assert resolve_knowledge_profile_schema(
        {"disease_id": "ANY", "name_en": "Meningitis (all reported etiologies)"}
    ).profile_type == "classification_scope"


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


def test_targeted_queries_and_inherited_evidence_are_section_scoped() -> None:
    queries = DiseaseKnowledgeFetcher._web_search_queries(
        ["Example condition"], ["prevention"]
    )
    assert any("prevention control vaccination" in query for query in queries)

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


def test_generation_completion_gate_accepts_grounded_bilingual_partial_profiles() -> None:
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
    assert _generated_profile_failures(
        [
            {"payload": en_payload, "trace": {"error": None}},
            {"payload": zh_payload, "trace": {"error": None}},
        ]
    ) == []


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
    assert enriched["official_intro_zh"] == "基于来源的流感简介。"
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


def test_site_disease_knowledge_fields_show_profile_for_public_non_authoritative_sources() -> None:
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

    assert enriched["knowledge_status"] == "published"
    assert enriched["knowledge_tier"] == "published"
    assert enriched["knowledge_profile_available"] is True
    assert enriched["knowledge_profile_reason"] == "partial_profile"
    assert enriched["knowledge_display_mode"] == "partial"
    assert enriched["knowledge_has_authoritative_sources"] is False
    assert enriched["official_intro_en"] == "Public-source SARS brief."


def test_site_disease_knowledge_fields_publish_partial_content_without_placeholders() -> None:
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

    assert enriched["knowledge_profile_available"] is True
    assert enriched["knowledge_display_mode"] == "partial"
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


def test_ai_settings_knowledge_model_shards_falls_back_to_model_chain() -> None:
    settings = AISettings(
        model_chain_raw="model-a,model-b,model-a",
        knowledge_model_shards_raw="",
    )

    assert settings.model_chain == ["model-a", "model-b"]
    assert settings.knowledge_model_shards == ["model-a", "model-b"]


def test_ai_brief_preferred_models_follow_shard_rotation(monkeypatch) -> None:
    shard_models = ["model-a", "model-b", "model-c"]
    fake_config = SimpleNamespace(ai=SimpleNamespace(knowledge_model_shards=shard_models))
    monkeypatch.setattr("src.knowledge.llm_brief_generator.get_config", lambda: fake_config)

    preferred_models, shard_index, shard_key = AIDiseaseBriefGenerator._preferred_models_for(
        disease_id="D001",
        language="zh",
    )
    expected_key = "D001:zh"
    expected_index = int(hashlib.md5(expected_key.encode("utf-8")).hexdigest()[:8], 16) % len(shard_models)
    expected_models = shard_models[expected_index:] + shard_models[:expected_index]

    assert shard_key == expected_key
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
