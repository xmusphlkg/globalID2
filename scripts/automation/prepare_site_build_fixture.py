#!/usr/bin/env python3
"""Create minimal ignored site-data inputs for clean-checkout CI builds.

Production releases always export real database-backed data. This helper is
restricted to CI and writes only files that are absent, allowing type, route,
build, and performance budgets to run in a clean checkout without a production
database or committed generated data.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SITE_DIR = ROOT / "astro-site"
SITUATION_FIXTURE = ROOT / "tests" / "fixtures" / "situation" / "public_report_v3.json"


class FixturePreparationError(RuntimeError):
    """Raised when the guarded CI fixture cannot be prepared."""


def _write_missing(path: Path, payload: Any) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    return True


def fixture_payloads(situation: Mapping[str, Any]) -> dict[str, Any]:
    sample_article = {
        "article_id": "ci-research-review-1",
        "slug": "ci-research-review",
        "title": "CI fixture review of respiratory surveillance evidence",
        "doi": "10.0000/gids-ci-review",
        "journal": "GIDS Fixture Journal",
        "publisher": "GIDS",
        "authors": ["GIDS CI"],
        "study_type": "Systematic review",
        "article_type": "review-article",
        "published_at": "2000-01-01",
        "updated_at": "2000-01-01T00:00:00Z",
        "open_access_status": "open",
        "open_access_url": "https://example.org/gids-ci-review",
        "source_urls": {
            "doi": "https://doi.org/10.0000/gids-ci-review",
            "publisher": "https://example.org/gids-ci-review",
        },
        "peer_review_status": "peer_reviewed",
        "editorial_status": "published",
        "integrity_status": "clear",
        "indexable": True,
        "discovery_score": 1.0,
        "diseases": [
            {
                "disease_id": "D001",
                "slug": "influenza",
                "name_en": "Influenza",
                "name_zh": "流感",
            }
        ],
        "countries": [
            {
                "code": "US",
                "slug": "us",
                "name_en": "United States",
                "name_zh": "美国",
            }
        ],
        "topics": [{"name": "Surveillance", "slug": "surveillance"}],
        "pathogens": [{"id": "influenza-virus", "name": "Influenza virus"}],
        "pathogen_types": [{"id": "virus", "name": "Virus"}],
        "populations": [{"id": "general-population", "name": "General population"}],
        "related_surveillance": [
            {
                "signal_id": "ci-signal-1",
                "url": "/situation/",
                "disease_name_en": "Influenza",
                "disease_name_zh": "流感",
                "relation_level": "exact_disease_geography",
            }
        ],
        "related_signals": [
            {
                "signal_id": "ci-signal-1",
                "visibility": "public",
                "title": "CI fixture surveillance signal",
                "disease_id": "D001",
                "disease_name_en": "Influenza",
                "disease_name_zh": "流感",
                "geographies": [{"code": "US", "name_en": "United States"}],
                "data_through": "2000-01-01",
                "relation_level": "exact_disease_geography",
                "situation_url": "/situation/",
            }
        ],
        "why_it_matters_en": (
            "This CI fixture keeps Research Radar pages and tests populated "
            "without using production literature data."
        ),
        "why_it_matters_zh": "该 CI 夹具在不使用生产文献数据的情况下填充研究雷达页面和测试。",
        "summary": {
            "en": {
                "research_question": "Can CI render a populated Research Radar release?",
                "study_design": "Synthetic review fixture",
                "population_setting": "Public deterministic build inputs",
                "main_findings": "The fixture provides one cited review, one topic, and one surveillance link.",
                "public_health_relevance": "It validates rendering only and is not epidemiological evidence.",
                "limitations": "Synthetic data for CI only.",
                "gids_interpretation": "Use production exports for public evidence.",
                "provenance": {
                    "generated_by": "ci_fixture",
                    "provider": "repository",
                    "model": "none",
                    "quality_score": 1.0,
                    "publication_gate": "ci_fixture",
                    "automation_policy_version": "ci_fixture_v1",
                    "editorially_approved": False,
                },
            },
            "zh": {
                "research_question": "CI 能否渲染已填充的研究雷达发布？",
                "study_design": "合成综述夹具",
                "population_setting": "公开确定性构建输入",
                "main_findings": "该夹具提供一条有引用的综述、一个主题和一个监测关联。",
                "public_health_relevance": "它只验证渲染，不是流行病学证据。",
                "limitations": "仅用于 CI 的合成数据。",
                "gids_interpretation": "公开证据应使用生产导出。",
                "provenance": {
                    "generated_by": "ci_fixture",
                    "provider": "repository",
                    "model": "none",
                    "quality_score": 1.0,
                    "publication_gate": "ci_fixture",
                    "automation_policy_version": "ci_fixture_v1",
                    "editorially_approved": False,
                },
            },
        },
        "knowledge_graph": {"stats": {"edges": 1}, "edges": []},
    }
    sample_signal = {
        "signal_id": "ci-signal-1",
        "visibility": "public",
        "title": "CI fixture surveillance signal",
        "disease_id": "D001",
        "disease_name_en": "Influenza",
        "disease_name_zh": "流感",
        "geographies": [{"code": "US", "name_en": "United States"}],
        "data_through": "2000-01-01",
        "relation_level": "exact_disease_geography",
        "situation_url": "/situation/",
        "exact_articles": [sample_article],
        "context_articles": [],
    }
    research = {
        "schema_version": 1,
        "last_updated": "2000-01-01T00:00:00Z",
        "metrics": {
            "total_public_articles": 1,
            "historical_baseline_articles": 0,
            "papers_last_7_days": 1,
            "diseases_last_7_days": 1,
            "countries_last_7_days": 1,
            "reviews_and_guidelines_last_7_days": 1,
        },
        "featured": [sample_article],
        "articles": [sample_article],
        "preprints": [],
        "integrity_alerts": [],
        "historical_baseline": [],
        "reviews_and_guidelines": [sample_article],
        "emerging_topics": [
            {
                "name": "Surveillance",
                "count_28_days": 1,
                "growth": 1,
                "share_delta": 0.1,
            }
        ],
        "knowledge_graph": {
            "nodes": [
                {
                    "id": "article:ci-research-review-1",
                    "type": "article",
                    "label": sample_article["title"],
                    "url": "/research/articles/ci-research-review/",
                },
                {
                    "id": "disease:D001",
                    "type": "disease",
                    "label": "Influenza",
                    "url": "/research/diseases/influenza/",
                },
            ],
            "edges": [
                {
                    "source": "article:ci-research-review-1",
                    "target": "disease:D001",
                    "relation": "ABOUT_DISEASE",
                    "confidence": 1.0,
                    "provenance": "ci_fixture",
                }
            ],
            "stats": {"nodes": 2, "edges": 1, "articles": 1},
            "quality": {"skipped_low_confidence_edges": 0},
        },
        "facets": {
            "diseases": [
                {
                    "disease_id": "D001",
                    "slug": "influenza",
                    "name_en": "Influenza",
                    "name_zh": "流感",
                    "count": 1,
                    "url": "/research/diseases/influenza/",
                }
            ],
            "countries": [
                {
                    "code": "US",
                    "slug": "us",
                    "name_en": "United States",
                    "name_zh": "美国",
                    "count": 1,
                    "url": "/research/countries/us/",
                }
            ],
            "topics": [
                {
                    "slug": "surveillance",
                    "name": "Surveillance",
                    "count": 1,
                    "url": "/research/topics/surveillance/",
                }
            ],
            "weeks": [
                {
                    "week": "2000-W01",
                    "start_date": "2000-01-03",
                    "end_date": "2000-01-09",
                    "count": 1,
                    "url": "/research/weekly/2000-W01/",
                }
            ],
        },
        "publication_timeline": [{"month": "2000-01", "publication_count": 1}],
        "pipeline_funnel": [],
        "completeness": [],
        "visualizations": {
            "hotspots": {
                "interpretation_note": {
                    "en": "Research attention is not disease risk or incidence.",
                    "zh": "研究关注度不代表疾病风险或发病率。",
                }
            }
        },
        "weekly_briefs": [
            {
                "week": "2000-W01",
                "start_date": "2000-01-03",
                "end_date": "2000-01-09",
                "articles": [sample_article],
                "top_topics": ["Surveillance"],
                "disease_count": 1,
                "country_count": 1,
                "brief_status": "automatically_compiled_not_editorially_reviewed",
                "byline": {
                    "name_en": "GIDS Research Radar automated compiler",
                    "name_zh": "GIDS Research Radar 自动编译器",
                },
            }
        ],
        "surveillance_evidence": {
            "available": True,
            "visibility": "public",
            "signals": [sample_signal],
            "methodology": {
                "en": "CI fixture links one public signal to one published article.",
                "zh": "CI 夹具将一个公开信号关联到一篇已发布文章。",
            },
            "evidence_gaps": [],
        },
    }
    hotspots = {
        "schema_version": "research_hotspots.v1",
        "generated_at": "2000-01-01T00:00:00Z",
        "grain": {},
        "streamgraph": {"periods": [], "series": [], "method": {}},
        "heatmap": {"periods": [], "rows": [], "method": {}},
        "burst_timeline": {"bursts": [], "method": {}},
        "alluvial": {"periods": [], "topics": [], "nodes": [], "links": [], "method": {}},
        "interpretation_note": {
            "en": "Research attention is not disease risk or incidence.",
            "zh": "研究关注度不代表疾病风险或发病率。",
        },
    }
    fixture_diseases = [
        ("D001", "influenza", "Influenza"),
        ("CI002", "example-disease", "Example disease"),
        ("CI003", "erythema-infectiosum-fifth-disease", "Erythema infectiosum (fifth disease)"),
        ("CI004", "respiratory-syncytial-virus-infection-rsv", "Respiratory syncytial virus infection (RSV)"),
        ("CI005", "roseola-exanthem-subitum", "Roseola (exanthem subitum)"),
        ("CI006", "flavivirus-infection-unspecified", "Flavivirus infection (unspecified)"),
        ("CI007", "haemolytic-uraemic-syndrome-hus", "Haemolytic uraemic syndrome (HUS)"),
        ("CI008", "meningitis-all-reported-etiologies", "Meningitis (all reported etiologies)"),
        ("CI009", "methicillin-resistant-staphylococcus-aureus-mrsa-surveillance", "MRSA surveillance"),
        ("CI010", "escherichia-coli-enteritis-all-reported-pathotypes", "Escherichia coli enteritis"),
    ]
    payloads = {
        "src/data/meta.json": {
            "generated_at": "2000-01-01T00:00:00Z",
            "total_countries": 0,
            "total_diseases": 0,
            "total_reports": 0,
            "countries": [],
            "knowledge_quality": {},
            "disease_ontology": {},
        },
        "src/data/diseases/index.json": [
            {
                "disease_id": disease_id,
                "slug": slug,
                "name_en": name,
                "name_zh": name,
                "category": "Other",
                "country_count": 0,
            }
            for disease_id, slug, name in fixture_diseases
        ],
        "src/data/reports/index.json": [],
        "src/data/about.json": {
            "generated_at": "2000-01-01T00:00:00Z",
            "summary": {},
            "metrics": [],
            "pipeline_steps": [],
            "architecture": {},
            "features": [],
            "data_sources": [],
            "country_coverage": [],
        },
        "src/data/downloads.json": {
            "schema_version": 4,
            "generated_at": "2000-01-01T00:00:00Z",
            "countries": [],
            "diseases": [],
            "formats": [],
        },
        "src/data/research/index.json": research,
        "src/data/research/hotspots.json": hotspots,
        "src/data/countries/jp.json": {
            "country_code": "JP",
            "country_name": "Japan",
            "country_name_en": "Japan",
            "country_name_zh": "日本",
            "total_cases": 12,
            "total_deaths": 0,
            "disease_count": 1,
            "date_range": {"start": "2000-01-01", "end": "2000-02-01"},
            "disease_series": {
                "D038": {
                    "disease_id": "D038",
                    "name_en": "Influenza",
                    "name_zh": "流感",
                    "dates": ["2000-01-01", "2000-01-08", "2000-01-15", "2000-01-22"],
                    "cases": [1, 3, 2, 6],
                }
            },
        },
        "src/data/countries/cn.json": {
            "country_code": "CN",
            "country_name": "China",
            "country_name_en": "China",
            "country_name_zh": "中国",
            "total_cases": 14,
            "total_deaths": 0,
            "disease_count": 1,
            "date_range": {"start": "2000-01-01", "end": "2000-02-01"},
            "disease_series": {
                "D038": {
                    "disease_id": "D038",
                    "name_en": "Influenza",
                    "name_zh": "流感",
                    "dates": ["2000-01-01", "2000-01-08", "2000-01-15", "2000-01-22"],
                    "cases": [2, 4, 1, 7],
                }
            },
        },
        "src/data/situation/v3/latest.json": dict(situation),
        "src/data/situation/latest.json": dict(situation),
        "public/site-data/situation/v3/latest.json": dict(situation),
        "public/site-data/situation/latest.json": dict(situation),
    }
    for disease_id, slug, name in fixture_diseases:
        payloads[f"src/data/diseases/{disease_id.lower()}.json"] = {
            "disease_id": disease_id,
            "slug": slug,
            "name_en": name,
            "name_zh": name,
            "category": "Other",
            "total_cases": 0,
            "total_deaths": 0,
            "country_count": 0,
            "country_series": {},
            "date_range": {"start": None, "end": None},
            "knowledge_status": "published",
            "knowledge_sources": [
                {"title": "CI fixture source", "url": "https://example.org/ci-fixture"}
            ],
        }
    return payloads


def prepare_fixture(
    site_dir: Path,
    *,
    environment: Mapping[str, str],
    allow_local: bool = False,
) -> dict[str, Any]:
    if not allow_local and str(environment.get("CI") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        raise FixturePreparationError("ci_environment_required")
    site_dir = site_dir.resolve()
    if not (site_dir / "package.json").is_file():
        raise FixturePreparationError("astro_site_directory_required")
    try:
        situation = json.loads(SITUATION_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixturePreparationError("situation_fixture_unreadable") from exc
    if not isinstance(situation, dict):
        raise FixturePreparationError("situation_fixture_object_required")
    created: list[str] = []
    preserved: list[str] = []
    for relative, payload in fixture_payloads(situation).items():
        if _write_missing(site_dir / relative, payload):
            created.append(relative)
        else:
            preserved.append(relative)
    return {
        "status": "prepared",
        "created": created,
        "preserved": preserved,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=DEFAULT_SITE_DIR)
    parser.add_argument(
        "--allow-local",
        action="store_true",
        help="Allow guarded missing-only fixture creation outside CI",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = prepare_fixture(
            args.site_dir,
            environment=os.environ,
            allow_local=args.allow_local,
        )
    except FixturePreparationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
