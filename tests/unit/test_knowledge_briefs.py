import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from requests import Response

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import AISettings
from src.generation.generator import ReportGenerator
from src.knowledge.brief_generator import DISCLAIMER_EN, SourceGroundedBriefGenerator
from src.knowledge.catalogue import (
    CATALOGUE_FALLBACK_BRIEF_TIER,
    build_catalogue_disease_brief,
    build_catalogue_disease_brief_payload,
    public_disease_page_exclusion_reason,
    resolve_disease_knowledge_status,
    should_generate_public_disease_page,
)
from src.knowledge.llm_brief_generator import AIDiseaseBriefGenerator
from src.knowledge.sources import DiseaseKnowledgeFetcher, SourceCandidate

from scripts.generate_site_data import (
    apply_country_brief_fields,
    apply_disease_knowledge_fields,
    build_country_data,
)


def test_source_grounded_brief_publishes_with_authoritative_source() -> None:
    generator = SourceGroundedBriefGenerator()
    payload = generator.generate(
        disease={
            "disease_id": "influenza",
            "name_en": "Influenza",
            "name_zh": "流感",
            "category": "Viral",
            "description": "An acute respiratory infection.",
        },
        sources=[
            {
                "id": 11,
                "source_type": "who",
                "source_name": "WHO Fact Sheet",
                "title": "Influenza",
                "url": "https://www.who.int/news-room/fact-sheets/detail/influenza-(seasonal)",
                "license": "WHO website terms",
                "review_status": "approved",
                "raw_excerpt": "Influenza is an acute respiratory infection caused by influenza viruses that circulate in all parts of the world.",
            }
        ],
        language="en",
    )

    assert payload["status"] == "published"
    assert payload["source_ids"] == [11]
    assert payload["source_confidence"] == "high"
    assert payload["source_attribution"][0]["source_name"] == "WHO Fact Sheet"
    assert payload["disclaimer"] == DISCLAIMER_EN
    assert "acute respiratory infection" in payload["definition"]
    assert "clinical" in payload["clinical_features"].lower()
    assert "surveillance" in payload["surveillance_note"].lower()


def test_source_grounded_brief_requires_review_for_msd_only() -> None:
    generator = SourceGroundedBriefGenerator()
    payload = generator.generate(
        disease={"disease_id": "example", "name_en": "Example disease", "category": "Bacterial"},
        sources=[
            {
                "id": 22,
                "source_type": "msd",
                "source_name": "MSD Manual Professional Edition",
                "title": "MSD Manual search metadata for Example disease",
                "url": "https://www.msdmanuals.com/professional/SearchResults?query=Example%20disease",
                "review_status": "requires_review",
            }
        ],
        language="en",
    )

    assert payload["status"] == "requires_review"
    assert payload["source_ids"] == [22]
    assert payload["source_confidence"] == "low"
    assert "MSD-only" in payload["review_notes"]


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


def test_ai_brief_user_prompt_uses_content_text_and_sections() -> None:
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
    assert '"content_sections"' in prompt
    assert '"resolved_url"' in prompt


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


def test_site_disease_knowledge_fields_mark_catalogue_fallback_for_review() -> None:
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
                    "brief_tier": CATALOGUE_FALLBACK_BRIEF_TIER,
                    "fallback_reason": "metadata_only_sources",
                },
            }
        },
    )

    assert enriched["knowledge_status"] == "requires_review"
    assert enriched["knowledge_tier"] == "requires_review"
    assert enriched["knowledge_fallback_reason"] == "metadata_only_sources"
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
    assert enriched["knowledge_profile_reason"] is None
    assert enriched["knowledge_has_authoritative_sources"] is False
    assert enriched["official_intro_en"] == "Public-source SARS brief."


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


def test_site_disease_knowledge_fields_without_briefs_keep_metadata_only() -> None:
    disease = {
        "disease_id": "D066",
        "name_en": "Other viral infections characterized by skin lesions",
        "name_zh": "其他以皮损为特征的病毒感染",
        "description": "",
    }

    enriched = apply_disease_knowledge_fields(disease, None)

    assert enriched["knowledge_status"] == "fallback"
    assert enriched["knowledge_profile_available"] is False
    assert enriched["knowledge_profile_reason"] == "no_published_brief"
    assert enriched["official_intro_en"] is None


def test_catalogue_fallback_provides_interpretive_fields() -> None:
    payload = build_catalogue_disease_brief(
        {
            "disease_id": "D017",
            "name_en": "Measles",
            "name_zh": "麻疹",
            "category": "Viral",
            "description": "Highly contagious viral disease",
        },
        "en",
    )

    assert "surveillance catalogue" in payload["brief"].lower()
    assert "clinical" in payload["clinical_features"].lower()
    assert "surveillance" in payload["surveillance_note"].lower()
    assert "respiratory" in payload["transmission"].lower()
    assert "Risk groups are not asserted" in payload["risk_groups"]


def test_catalogue_payload_marks_fallback_for_review() -> None:
    payload = build_catalogue_disease_brief_payload(
        {
            "disease_id": "D081",
            "name_en": "Unspecified malaria",
            "name_zh": "未明示的疟疾",
            "category": "Parasitic",
            "description": "",
        },
        "en",
        fallback_reason="metadata_only_sources",
    )

    assert payload["status"] == "requires_review"
    assert payload["source_confidence"] == "low"
    assert payload["source_ids"] == []
    assert payload["metadata"]["brief_tier"] == CATALOGUE_FALLBACK_BRIEF_TIER
    assert payload["metadata"]["fallback_reason"] == "metadata_only_sources"


def test_resolve_knowledge_status_treats_catalogue_fallback_as_review() -> None:
    status = resolve_disease_knowledge_status(
        [
            {
                "status": "requires_review",
                "metadata": {"brief_tier": CATALOGUE_FALLBACK_BRIEF_TIER},
            }
        ]
    )

    assert status == "requires_review"


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


def test_structured_report_disease_card_markdown_contains_official_brief_and_sources() -> None:
    markdown = ReportGenerator._render_disease_card(
        {
            "name_en": "Influenza",
            "official_brief": "Source-grounded official brief.",
            "transmission": "Transmission context.",
            "prevention": "Prevention context.",
            "risk_groups": "Risk group context.",
            "current_interpretation": "Current surveillance interpretation.",
            "trend_assessment": "Increasing",
            "risk_note": "Monitor recent increases.",
            "data_limitations": "Reported surveillance records only.",
            "disclaimer": "This brief is for surveillance and public information only. It is not medical advice.",
            "source_attribution": [{"title": "WHO Influenza", "url": "https://www.who.int"}],
            "metrics": {
                "total_cases": 100,
                "total_deaths": 1,
                "latest_cases": 10,
                "latest_deaths": 0,
            },
        },
        "en",
    )

    assert "### Official Brief" in markdown
    assert "Source-grounded official brief." in markdown
    assert "- Total cases: 100" in markdown
    assert "[WHO Influenza](https://www.who.int)" in markdown
