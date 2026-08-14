from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.generation.site_data_literature import (
    append_historical_seed_articles,
    attach_surveillance_evidence,
    build_pipeline_funnel,
    build_hotspot_visualizations,
    build_publication_pulse,
    build_related_surveillance,
    build_surveillance_coverage_matrix,
    build_surveillance_evidence,
    empty_literature_export,
    load_historical_seed_articles,
)
from src.literature.classification import classify_candidate
from src.literature.clients.crossref import CrossrefClient
from src.literature.enrichment import LiteratureSummaryGenerator
from src.literature.knowledge_graph import build_knowledge_graph
from src.literature.normalization import normalize_crossref
from src.literature.normalization import normalize_europe_pmc
from src.services.literature_gap_service import build_gap_query_plan
from src.services.literature_automation_service import (
    decide_article,
    decide_evidence_link,
    decide_summary,
)
from dashboard.api.routers.literature import _publication_blockers


ROOT = Path(__file__).resolve().parents[2]


def _crossref_payload() -> dict:
    return {
        "DOI": "10.1000/Test.Article",
        "title": ["Dengue surveillance and vaccination policy in Japan"],
        "container-title": ["Emerging Infectious Diseases"],
        "ISSN": ["1080-6040"],
        "publisher": "Example Publisher",
        "type": "journal-article",
        "published-online": {"date-parts": [[2026, 8, 12]]},
        "indexed": {"date-time": "2026-08-13T01:02:03Z"},
        "abstract": "<jats:p>A surveillance study of dengue vaccination and outbreak response.</jats:p>",
        "author": [{"given": "Ada", "family": "Lovelace", "ORCID": "https://orcid.org/0000-0001"}],
        "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
        "URL": "https://doi.org/10.1000/test.article",
    }


def test_crossref_normalization_is_stable_and_strips_markup():
    first = normalize_crossref(_crossref_payload())
    second = normalize_crossref(_crossref_payload())
    assert first is not None and second is not None
    assert first.article_id == second.article_id
    assert first.slug == second.slug
    assert first.doi == "10.1000/test.article"
    assert first.abstract_text == "A surveillance study of dengue vaccination and outbreak response."
    assert first.authors == [{"name": "Ada Lovelace", "orcid": "0000-0001"}]
    assert first.open_access_status == "open"


def test_europe_pmc_search_result_normalization_preserves_dates_and_identifiers():
    candidate = normalize_europe_pmc({
        "title": "Dengue surveillance in Japan",
        "doi": "10.1000/EPMC.Test",
        "pmid": "123456",
        "pmcid": "PMC123456",
        "firstPublicationDate": "2026-08-12",
        "dateOfCreation": "2026-08-13",
        "journalTitle": "Example Journal",
        "isOpenAccess": "Y",
        "authorList": {"author": [{"fullName": "Ada Lovelace"}]},
    })
    assert candidate is not None
    assert candidate.doi == "10.1000/epmc.test"
    assert candidate.published_at == datetime(2026, 8, 12, tzinfo=timezone.utc)
    assert candidate.indexed_at == datetime(2026, 8, 13, tzinfo=timezone.utc)
    assert candidate.open_access_status == "open"
    assert candidate.authors == [{"name": "Ada Lovelace"}]


class _FakeCrossrefClient(CrossrefClient):
    def __init__(self):
        super().__init__(mailto="tests@example.org")
        self.requests = []

    async def get_json(self, _client, path, *, params):
        self.requests.append((path, params))
        issn = path.split("/")[2]
        items = {
            "1111-1111": [
                {"DOI": "10.1000/old", "indexed": {"date-time": "2026-08-12T00:00:00Z"}},
                {"DOI": "10.1000/new", "indexed": {"date-time": "2026-08-14T00:00:00Z"}},
            ],
            "2222-2222": [
                {"DOI": "10.1000/mid", "indexed": {"date-time": "2026-08-13T00:00:00Z"}},
            ],
        }[issn]
        return {
            "message": {
                "items": items[: int(params["rows"])],
                "next-cursor": "done",
                "total-results": len(items),
            }
        }


async def test_crossref_incremental_uses_ascending_index_checkpoint_when_capped():
    client = _FakeCrossrefClient()
    result = await client.fetch_incremental(
        journals=[
            {"issn": "1111-1111", "title": "One"},
            {"issn": "2222-2222", "title": "Two"},
        ],
        since=datetime(2026, 8, 12, tzinfo=timezone.utc),
        until=datetime(2026, 8, 14, tzinfo=timezone.utc),
        max_records=2,
        concurrency=2,
    )
    assert [item["DOI"] for item in result.records] == ["10.1000/old", "10.1000/mid"]
    assert result.checkpoint["truncated"] is True
    assert result.checkpoint["records_seen"] == 3
    assert result.checkpoint["next_from_indexed_at"] == "2026-08-13T00:00:00+00:00"
    assert all(params["sort"] == "indexed" and params["order"] == "asc" for _, params in client.requests)
    assert "T00:00:00" in client.requests[0][1]["filter"]


def test_gap_query_plan_is_bounded_transparent_and_provider_specific():
    plan = build_gap_query_plan(
        disease_id="D021",
        disease_name="Dengue",
        aliases=["DENV", "Dengue fever", "Dengue"],
        country_names=["Japan", "Japan"],
        lookback_days=730,
    )
    assert plan["schema_version"] == "literature_gap_query.v1"
    assert plan["disease_terms"] == ["Dengue", "DENV", "Dengue fever"]
    assert plan["geography_terms"] == ["Japan"]
    assert plan["crossref"]["exact"] == "Dengue Japan"
    assert plan["europe_pmc"]["exact"] == '("Dengue" OR "DENV" OR "Dengue fever") AND ("Japan")'


def test_classifier_links_disease_country_topics_and_study_type():
    candidate = normalize_crossref(_crossref_payload())
    assert candidate is not None
    result = classify_candidate(
        candidate,
        diseases=[{"disease_id": "D021", "name_en": "Dengue", "name_zh": "登革热", "aliases": ["DENV"]}],
        countries=[{"code": "JP", "name": "Japan", "name_en": "Japan", "name_zh": "日本"}],
        taxonomy={
            "topics": {
                "Surveillance": ["surveillance"],
                "Vaccination": ["vaccination"],
                "Outbreak investigation": ["outbreak"],
            },
            "study_types": {"Outbreak investigation": ["outbreak response"]},
        },
        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
        auto_publish_min_score=0.6,
    )
    assert [item.key for item in result.diseases] == ["D021"]
    assert [item.key for item in result.countries] == ["JP"]
    assert {item.key for item in result.topics} == {"Surveillance", "Vaccination", "Outbreak investigation"}
    assert result.study_type == "Outbreak investigation"
    assert result.publication_status == "published"
    assert result.discovery_score >= 0.89


def test_short_terms_do_not_create_substring_false_positives():
    candidate = normalize_crossref(_crossref_payload())
    assert candidate is not None
    result = classify_candidate(
        candidate,
        diseases=[{"disease_id": "D999", "name_en": "US", "name_zh": "", "aliases": ["AI"]}],
        countries=[],
        taxonomy={},
    )
    assert result.diseases == []
    assert result.publication_status == "excluded"


def test_aggregate_catalogue_rows_never_become_literature_diseases():
    candidate = normalize_crossref(_crossref_payload())
    assert candidate is not None
    candidate.title = "Total surveillance reports across participating sites"
    result = classify_candidate(
        candidate,
        diseases=[{"disease_id": "D999", "name_en": "Total", "name_zh": "总计", "aliases": []}],
        countries=[],
        taxonomy={"topics": {"Surveillance": ["surveillance"]}},
    )
    assert result.diseases == []
    assert result.publication_status == "excluded"


def test_recommendations_in_prose_do_not_misclassify_an_article_as_a_guideline():
    candidate = normalize_crossref(_crossref_payload())
    assert candidate is not None
    candidate.abstract_text = "The discussion offers recommendations for future vaccination research."
    result = classify_candidate(
        candidate,
        diseases=[{"disease_id": "D021", "name_en": "Dengue", "name_zh": "登革热", "aliases": []}],
        countries=[],
        taxonomy={
            "topics": {"Vaccination": ["vaccination"]},
            "study_types": {
                "Guideline": ["guideline", "practice guidance", "clinical guidance", "consensus statement"]
            },
        },
    )
    assert result.study_type == "Journal article"


def test_empty_public_export_has_a_stable_contract():
    payload = empty_literature_export()
    assert payload["schema_version"] == 1
    assert payload["articles"] == []
    assert payload["metrics"]["public_article_limit"] is None
    assert payload["metrics"]["historical_baseline_articles"] == 0
    assert payload["metrics"]["papers_last_7_days"] == 0
    assert "disease_articles" in payload
    assert payload["visualizations"]["pipeline_funnel"][0]["stage"] == "indexed"
    assert payload["visualizations"]["completeness"] == []
    assert payload["visualizations"]["hotspots"]["schema_version"] == "research_hotspots.v1"
    assert payload["knowledge_graph"]["stats"] == {"nodes": 0, "edges": 0, "articles": 0}


def test_historical_seed_articles_append_with_labels_and_deduplicate():
    seed_path = ROOT / "configs/literature/historical_seed.json"
    seed_articles = load_historical_seed_articles(seed_path)
    assert len(seed_articles) >= 12

    diseases_by_id = {
        "D050": {"slug": "ebola", "name_en": "Ebola", "name_zh": "埃博拉出血热"},
        "D017": {"slug": "measles", "name_en": "Measles", "name_zh": "麻疹"},
    }
    projected: list[dict] = []
    added = append_historical_seed_articles(
        projected,
        diseases_by_id=diseases_by_id,
        surveillance_coverage={"D050": {"CD"}},
        seed_path=seed_path,
    )

    assert added == len(seed_articles)
    assert append_historical_seed_articles(
        projected,
        diseases_by_id=diseases_by_id,
        surveillance_coverage={"D050": {"CD"}},
        seed_path=seed_path,
    ) == 0
    ebola_1976 = next(article for article in projected if article["pmid"] == "307456")
    assert ebola_1976["source_kind"] == "historical_seed"
    assert ebola_1976["historical_baseline"] is True
    assert ebola_1976["publication_date_label_en"] == "1978"
    assert ebola_1976["publication_date_label_zh"] == "1978年"
    assert ebola_1976["source_urls"]["pubmed"] == "https://pubmed.ncbi.nlm.nih.gov/307456/"
    assert ebola_1976["source_urls"]["pmc"] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC2395567/"
    assert ebola_1976["related_surveillance"][0]["country_code"] == "CD"


def test_pipeline_funnel_distinguishes_status_and_public_catalogue_counts():
    funnel = build_pipeline_funnel(
        total=804,
        review=21,
        published=201,
        public_catalogue=200,
        excluded=582,
        summarized=36,
        exact_linked=25,
    )
    by_stage = {item["stage"]: item for item in funnel}
    assert by_stage["published"]["label"] == "Published status"
    assert by_stage["published"]["count"] == 201
    assert by_stage["public_catalogue"]["label"] == "Public catalogue"
    assert by_stage["public_catalogue"]["count"] == 200


def test_research_collection_source_keeps_structured_data_and_render_caps():
    source = (ROOT / "astro-site/src/components/research/ResearchCollectionPage.astro").read_text(encoding="utf-8")
    assert "const initialVisibleArticles = 30" in source
    assert "articles.slice(0, 100).map" in source
    assert "data-load-more-collection" in source


def test_public_knowledge_graph_is_deterministic_and_uses_classifier_relations_only():
    article = {
        "article_id": "lit_123",
        "slug": "example-article",
        "title": "Dengue surveillance in Japan",
        "published_at": "2026-08-12T00:00:00+00:00",
        "study_type": "Outbreak investigation",
        "abstract_text": "must never enter the graph",
        "source_payload": {"private": True},
        "diseases": [{"disease_id": "D021", "name_en": "Dengue", "slug": "dengue", "confidence": 0.9}],
        "countries": [{"code": "JP", "name_en": "Japan", "confidence": 0.8}],
        "topics": [{"name": "Surveillance", "confidence": 0.7}],
    }
    first = build_knowledge_graph([article])
    second = build_knowledge_graph([article])
    assert first == second
    assert first["stats"] == {"nodes": 5, "edges": 4, "articles": 1}
    assert {edge["relation"] for edge in first["edges"]} == {
        "ABOUT_DISEASE", "STUDIED_IN", "ADDRESSES_TOPIC", "USES_STUDY_DESIGN",
    }
    serialized = str(first)
    assert "must never enter the graph" not in serialized
    assert "source_payload" not in serialized


def test_public_knowledge_graph_filters_low_confidence_classifier_edges():
    article = {
        "article_id": "lit_weak",
        "slug": "weak-article",
        "title": "Weak geography match",
        "diseases": [{"disease_id": "D021", "name_en": "Dengue", "confidence": 0.62}],
        "countries": [{"code": "US", "name_en": "United States", "confidence": 0.62}],
        "topics": [{"name": "Surveillance", "confidence": 0.66}],
    }
    graph = build_knowledge_graph([article])
    assert {edge["relation"] for edge in graph["edges"]} == {"ADDRESSES_TOPIC"}
    assert graph["thresholds"] == {"disease": 0.78, "country": 0.78, "topic": 0.66}
    assert graph["min_relation_confidence"] == 0.66
    assert graph["quality"]["skipped_low_confidence_edges"] == 2


def test_publication_pulse_ignores_future_publication_dates():
    pulse = build_publication_pulse(
        [
            {"published_at": "2026-08-10T00:00:00+00:00"},
            {"published_at": "2026-08-24T00:00:00+00:00"},
        ],
        now=datetime(2026, 8, 14, tzinfo=timezone.utc),
        weeks=2,
    )
    assert sum(item["publication_count"] for item in pulse) == 1
    assert pulse[-1]["week"] == "2026-W33"


def test_hotspot_visualizations_cover_four_time_based_views_and_quality_gates():
    articles = [
        {
            "article_id": "lit-1",
            "slug": "dengue-surveillance-june",
            "title": "Dengue surveillance in June",
            "journal": "Example Journal",
            "published_at": "2026-06-05T00:00:00+00:00",
            "discovery_score": 0.8,
            "diseases": [{"disease_id": "D021", "slug": "dengue", "name_en": "Dengue", "confidence": 0.9}],
            "topics": [{"name": "Surveillance", "confidence": 0.7}],
        },
        {
            "article_id": "lit-2",
            "slug": "dengue-surveillance-august",
            "title": "Dengue surveillance in August",
            "journal": "Example Journal",
            "published_at": "2026-08-04T00:00:00+00:00",
            "discovery_score": 0.82,
            "diseases": [{"disease_id": "D021", "slug": "dengue", "name_en": "Dengue", "confidence": 0.9}],
            "topics": [{"name": "Surveillance", "confidence": 0.7}],
        },
        {
            "article_id": "lit-3",
            "slug": "dengue-surveillance-august-2",
            "title": "Dengue surveillance follow-up",
            "journal": "Example Journal",
            "published_at": "2026-08-08T00:00:00+00:00",
            "discovery_score": 0.78,
            "diseases": [{"disease_id": "D021", "slug": "dengue", "name_en": "Dengue", "confidence": 0.9}],
            "topics": [{"name": "Surveillance", "confidence": 0.7}],
        },
        {
            "article_id": "lit-4",
            "slug": "weak-disease-link",
            "title": "Weak disease link",
            "journal": "Example Journal",
            "published_at": "2026-08-09T00:00:00+00:00",
            "discovery_score": 0.7,
            "diseases": [{"disease_id": "D999", "name_en": "Weak match", "confidence": 0.6}],
            "topics": [{"name": "Diagnostics", "confidence": 0.7}],
        },
        {
            "article_id": "future",
            "slug": "future-topic",
            "title": "Future topic",
            "journal": "Example Journal",
            "published_at": "2026-09-01T00:00:00+00:00",
            "discovery_score": 0.99,
            "diseases": [{"disease_id": "D021", "name_en": "Dengue", "confidence": 0.9}],
            "topics": [{"name": "Future topic", "confidence": 0.9}],
        },
    ]
    hotspots = build_hotspot_visualizations(
        articles,
        now=datetime(2026, 8, 14, tzinfo=timezone.utc),
        months=3,
        quarters=2,
        top_topics=4,
    )
    assert hotspots["schema_version"] == "research_hotspots.v1"
    assert hotspots["streamgraph"]["periods"][-1]["period"] == "2026-08"
    assert [item["topic"] for item in hotspots["streamgraph"]["series"]] == ["Surveillance", "Diagnostics"]
    assert "Future topic" not in [item["topic"] for item in hotspots["streamgraph"]["series"]]
    assert hotspots["burst_timeline"]["bursts"][0]["topic"] == "Surveillance"
    assert hotspots["burst_timeline"]["bursts"][0]["articles"][0]["slug"] == "dengue-surveillance-august"
    heatmap_keys = {row["key"] for row in hotspots["heatmap"]["rows"]}
    assert "D021::surveillance" in heatmap_keys
    assert all("D999::diagnostics" != key for key in heatmap_keys)
    assert hotspots["alluvial"]["periods"][-1]["period"] == "2026-Q3"
    assert hotspots["alluvial"]["nodes"]


def test_surveillance_links_require_precise_geography_and_real_series_coverage():
    diseases = [{"disease_id": "D021", "name_en": "Dengue", "name_zh": "登革热", "slug": "dengue"}]
    countries = [
        {"code": "CN", "name_en": "China", "confidence": 0.78},
        {"code": "BR", "name_en": "Brazil", "confidence": 0.62},
        {"code": "XX", "name_en": "No series", "confidence": 0.9},
    ]
    links = build_related_surveillance(diseases, countries, {"D021": {"CN", "BR"}})
    assert [(item["disease_id"], item["country_code"]) for item in links] == [("D021", "CN")]
    assert links[0]["url"] == "/diseases/dengue/"


def test_signal_evidence_separates_exact_context_and_catalogue_gaps():
    articles = [
        {
            "article_id": "exact",
            "slug": "exact-dengue-japan",
            "title": "Dengue in Japan",
            "published_at": "2026-08-12T00:00:00+00:00",
            "diseases": [{"disease_id": "D021", "confidence": 0.9}],
            "countries": [{"code": "JP", "confidence": 0.8}],
        },
        {
            "article_id": "context",
            "slug": "dengue-context",
            "title": "Dengue vaccine evidence",
            "published_at": "2026-08-11T00:00:00+00:00",
            "diseases": [{"disease_id": "D021", "confidence": 0.82}],
            "countries": [],
        },
        {
            "article_id": "low-confidence-place",
            "slug": "dengue-place-unconfirmed",
            "title": "Dengue geography not confirmed",
            "published_at": "2026-08-10T00:00:00+00:00",
            "diseases": [{"disease_id": "D021", "confidence": 0.82}],
            "countries": [{"code": "JP", "confidence": 0.62}],
        },
    ]
    dengue_signal = {
        "id": "signal:dengue-jp",
        "kind": "statistical_signal",
        "disease_id": "D021",
        "disease_name": "Dengue",
        "country_code": "JP",
        "country_name": "Japan",
        "data_through": "2026-08-01",
        "window": {"current": 30, "previous": 12, "change_pct": 150},
        "risk": {"score": 52, "level": "high", "confidence": "low"},
    }
    snapshot = {
        "snapshot_id": "situation-test",
        "generated_at": "2026-08-13T00:00:00+00:00",
        "data_through": "2026-08-01",
        "method_version": "situation_room_v2.0",
        "public_enabled": True,
        "increasing": [dengue_signal],
        "unusual": [dengue_signal],
        "emerging": [{
            "id": "event:ebola-cd",
            "kind": "official_event",
            "disease_id": "D050",
            "disease_name": "Ebola",
            "published_at": "2026-08-02",
            "geographies": [{"code": "CD", "name": "Democratic Republic of the Congo"}],
        }],
    }
    result = build_surveillance_evidence(articles, snapshot)
    assert result["visibility"] == "public"
    assert result["metrics"] == {
        "active_signals": 2,
        "signals_with_exact_evidence": 1,
        "exact_evidence_links": 1,
        "signals_with_disease_context": 1,
        "contextual_evidence_links": 2,
        "evidence_gaps": 1,
    }
    dengue = result["signals"][0]
    assert [article["article_id"] for article in dengue["exact_articles"]] == ["exact"]
    assert {article["article_id"] for article in dengue["context_articles"]} == {
        "context",
        "low-confidence-place",
    }
    assert result["evidence_gaps"][0]["gap_type"] == "catalogue_coverage_gap"


def test_signal_evidence_backlinks_are_attached_without_mutating_articles():
    payload = {
        **empty_literature_export(),
        "articles": [{
            "article_id": "lit-1",
            "slug": "influenza-review",
            "title": "Influenza review",
            "diseases": [{"disease_id": "D038", "confidence": 0.9}],
            "countries": [],
        }],
    }
    snapshot = {
        "snapshot_id": "shadow-snapshot",
        "public_enabled": False,
        "increasing": [{
            "id": "signal:flu-ie",
            "disease_id": "D038",
            "disease_name": "Influenza",
            "country_code": "IE",
            "country_name": "Ireland",
        }],
    }
    result = attach_surveillance_evidence(payload, snapshot)
    assert "related_signals" not in payload["articles"][0]
    assert result["surveillance_evidence"]["visibility"] == "shadow"
    assert result["articles"][0]["related_signals"][0]["relation_level"] == "disease_context"
    assert "coverage_matrix" in result["visualizations"]


def test_surveillance_coverage_matrix_summarizes_exact_context_and_gaps():
    matrix = build_surveillance_coverage_matrix({
        "signals": [
            {
                "disease_id": "D021",
                "disease_name_en": "Dengue",
                "geographies": [{"code": "JP", "name_en": "Japan"}],
                "coverage_status": "exact_evidence",
                "exact_article_count": 2,
                "context_article_count": 1,
            },
            {
                "disease_id": "D021",
                "disease_name_en": "Dengue",
                "geographies": [{"code": "BR", "name_en": "Brazil"}],
                "coverage_status": "coverage_gap",
                "exact_article_count": 0,
                "context_article_count": 0,
            },
        ],
    })
    assert [item["disease_id"] for item in matrix["diseases"]] == ["D021"]
    assert {item["country_code"]: item for item in matrix["cells"]}["JP"]["exact_links"] == 2
    assert {item["country_code"]: item for item in matrix["cells"]}["BR"]["gaps"] == 1


def test_editor_relationship_decisions_override_or_suppress_classifier_links():
    articles = [{
        "article_id": "lit-1",
        "slug": "dengue-context",
        "title": "Dengue context",
        "diseases": [{"disease_id": "D021", "confidence": 0.9}],
        "countries": [],
    }]
    snapshot = {
        "snapshot_id": "snapshot-1",
        "public_enabled": True,
        "increasing": [{
            "id": "signal-1", "disease_id": "D021", "disease_name": "Dengue",
            "country_code": "JP", "country_name": "Japan",
        }],
    }
    confirmed = build_surveillance_evidence(articles, snapshot, relation_decisions=[{
        "signal_id": "signal-1", "article_id": "lit-1", "status": "confirmed",
        "relation_level": "exact_disease_geography",
    }])
    assert confirmed["signals"][0]["exact_article_count"] == 1
    assert confirmed["metrics"]["evidence_gaps"] == 0
    rejected = build_surveillance_evidence(articles, snapshot, relation_decisions=[{
        "signal_id": "signal-1", "article_id": "lit-1", "status": "rejected",
        "relation_level": "disease_context",
    }])
    assert rejected["signals"][0]["context_article_count"] == 0
    assert rejected["metrics"]["evidence_gaps"] == 1


class _FakeLiteratureAgent:
    async def process(self, **_kwargs):
        return {"raw_response": """{
          "research_question": {"text": "What patterns were observed?", "evidence": ["abstract"], "confidence": 0.8},
          "study_design": {"text": "This was a descriptive surveillance study.", "evidence": ["abstract"], "confidence": 0.9},
          "population_setting": null,
          "main_findings": {"text": "one two three four five six seven eight nine ten eleven twelve", "evidence": ["abstract"], "confidence": 0.9},
          "public_health_relevance": {"text": "The record is relevant to outbreak surveillance.", "evidence": ["classifier_links"], "confidence": 0.7},
          "limitations": null,
          "gids_interpretation": {"text": "Use the original article before applying its findings.", "evidence": ["bibliographic_metadata"], "confidence": 0.8}
        }"""}

    def get_latest_conversation(self):
        return {"model": "test-model", "provider": "test-provider", "tokens": {"total_tokens": 10}}


async def test_model_enrichment_rejects_verbatim_overlap_and_stays_reviewable():
    candidate = normalize_crossref(_crossref_payload())
    assert candidate is not None
    candidate.abstract_text = "zero one two three four five six seven eight nine ten eleven twelve thirteen"
    result = await LiteratureSummaryGenerator(agent=_FakeLiteratureAgent()).generate(
        article=candidate,
        language="en",
        diseases=["Dengue"],
        countries=["Japan"],
        topics=["Surveillance"],
        timeout_seconds=10,
        preferred_models=[],
    )
    assert result.fields["main_findings"] is None
    assert result.fields["study_design"] == "This was a descriptive surveillance study."
    assert result.model == "test-model"
    assert "Removed verbatim-overlap fields: main_findings" in result.review_notes


def _autopilot_config():
    return SimpleNamespace(
        autopilot_auto_reject_weak_links=True,
        autopilot_auto_exclude_incomplete=True,
        autopilot_auto_exclude_preprints=True,
        autopilot_article_min_score=0.70,
        autopilot_article_exclude_below_score=0.60,
        autopilot_disease_min_confidence=0.82,
        autopilot_exact_relation_min_confidence=0.78,
        autopilot_context_relation_min_confidence=0.82,
        autopilot_summary_min_quality=0.90,
    )


def _autopilot_article(**overrides):
    values = {
        "article_id": "lit-auto",
        "title": "Peer-reviewed dengue surveillance evidence in Japan",
        "journal": "Example Journal",
        "authors": [{"name": "Ada Lovelace"}],
        "doi": "10.1000/autopilot",
        "pmid": None,
        "pmcid": None,
        "published_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "peer_review_status": "peer_reviewed",
        "integrity_status": "current",
        "discovery_score": 0.74,
        "publication_status": "review",
        "metadata_": {},
        "abstract_text": "A complete source abstract used only to compute a stable fingerprint.",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_manual_publication_gate_blocks_future_and_incomplete_records():
    article = _autopilot_article(
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        peer_review_status="preprint",
        doi=None,
        pmid=None,
        pmcid=None,
        authors=[],
    )
    blockers = _publication_blockers(article, now=datetime(2026, 8, 14, tzinfo=timezone.utc))
    assert "publication date is in the future" in blockers
    assert "record is not peer reviewed" in blockers
    assert "DOI/PMID/PMCID is missing" in blockers
    assert "authors are missing" in blockers


def test_autopilot_confirms_exact_relationship_and_publishes_eligible_article():
    article = _autopilot_article()
    link = SimpleNamespace(
        status="review",
        relation_level="exact_disease_geography",
        confidence=0.78,
    )
    relation = decide_evidence_link(
        link,
        article,
        _autopilot_config(),
        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    publication = decide_article(
        article,
        _autopilot_config(),
        max_disease_confidence=0.82,
        confirmed_relation_levels={"exact_disease_geography"},
        now=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    assert relation.action == "confirm"
    assert publication.action == "publish"


def test_autopilot_holds_preprints_and_rejects_weak_relationships():
    article = _autopilot_article(peer_review_status="preprint")
    exact = SimpleNamespace(status="review", relation_level="exact_disease_geography", confidence=0.95)
    weak = SimpleNamespace(status="review", relation_level="candidate", confidence=0.70)
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    assert decide_evidence_link(exact, article, _autopilot_config(), now=now).action == "reject"
    assert decide_evidence_link(weak, article, _autopilot_config(), now=now).action == "reject"


def test_autopilot_publishes_only_current_well_grounded_model_summaries():
    from src.literature.enrichment import source_fingerprint

    article = _autopilot_article(publication_status="published")
    fields = {
        "research_question": "What was measured?",
        "study_design": "A descriptive surveillance study.",
        "population_setting": "National surveillance records.",
        "main_findings": "The abstract reports the observed pattern.",
        "public_health_relevance": "The study informs surveillance interpretation.",
        "limitations": "The design is descriptive.",
        "gids_interpretation": "Use the primary paper for decisions.",
    }
    summary = SimpleNamespace(
        article_id=article.article_id,
        status="review",
        generated_by="literature-evidence-agent",
        quality_score=0.94,
        generation_metadata={"source_fingerprint": source_fingerprint(article)},
        evidence_map={
            field: {"sources": ["abstract"], "confidence": 0.88}
            for field in fields
        },
        review_notes="Grounded generation passed.",
        **fields,
    )
    assert decide_summary(summary, article, _autopilot_config()).action == "publish"
    summary.generation_metadata["source_fingerprint"] = "stale"
    assert decide_summary(summary, article, _autopilot_config()).action == "hold"
    summary.status = "published"
    summary.generation_metadata["autopilot"] = {"policy_version": "research-radar-autopilot.v1"}
    published_stale = decide_summary(summary, article, _autopilot_config())
    assert published_stale.action == "hold"
    assert "fingerprint is stale" in published_stale.reasons[0]
