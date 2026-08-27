from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from src.literature.health import expected_source_names
from src.literature.publisher_coverage import build_publisher_coverage_report


ROOT = Path(__file__).resolve().parents[2]


def _row(scope: str, period: str, total: int, *, journal: str | None = None, **values: int):
    return {
        "scope": scope,
        "period": period,
        "journal": journal,
        "total": total,
        "doi": values.get("doi", total),
        "pmid": values.get("pmid", 0),
        "europe_pmc": values.get("europe_pmc", 0),
        "crossref": values.get("crossref", total),
        "openalex": values.get("openalex", 0),
        "crossref_without_pmid": values.get("crossref_without_pmid", 0),
        "crossref_without_europe_pmc": values.get("crossref_without_europe_pmc", 0),
        "core_provenance_gap": values.get("core_provenance_gap", 0),
    }


def test_report_distinguishes_pubmed_enrichment_from_crossref_discovery() -> None:
    rows = [
        _row("overall", "all_time", 300, pmid=210, europe_pmc=205, openalex=270),
        _row("overall", "recent", 30, pmid=18, europe_pmc=17, openalex=20),
        _row("springer_nature", "all_time", 200, pmid=140, europe_pmc=138, openalex=180),
        _row("springer_nature", "recent", 20, pmid=10, europe_pmc=9, openalex=12),
        _row("springer_nature", "recent", 12, journal="B Journal", pmid=6),
        _row("springer_nature", "recent", 8, journal="A Journal", pmid=4),
        _row("elsevier", "all_time", 100, pmid=70, europe_pmc=67, openalex=90),
        _row("elsevier", "recent", 10, pmid=8, europe_pmc=8, openalex=8),
    ]

    report = build_publisher_coverage_report(
        rows,
        as_of=date(2026, 8, 27),
        top_journals=1,
        generated_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
        source_configuration={
            "springer_nature": {"enabled": False, "credential_configured": False},
            "elsevier": {"enabled": False, "credential_configured": False},
        },
    )

    springer = report["coverage"]["publishers"]["springer_nature"]["recent"]
    assert springer["coverage"]["pmid"] == 0.5
    assert springer["coverage"]["crossref"] == 1.0
    assert [row["journal"] for row in springer["top_journals"]] == ["B Journal"]
    assert report["recent_window"] == {
        "days": 365,
        "start": "2025-08-28",
        "end_inclusive": "2026-08-27",
    }
    assert report["source_strategy"] == {
        "policy_code": "core_sources_sufficient_for_curated_scope",
        "pubmed_is_complete_replacement": False,
        "publisher_apis_required_now": False,
        "recommendation": (
            "Keep Springer Nature and Elsevier APIs disabled without credentials; "
            "retain Crossref as discovery and Europe PMC/PubMed as biomedical enrichment."
        ),
        "publisher_catalogue_gap_is_measurable": False,
    }
    assert report["source_configuration"]["elsevier"]["credential_configured"] is False


def test_report_escalates_a_recent_core_provenance_gap() -> None:
    rows = [
        _row("springer_nature", "recent", 10, crossref=9, core_provenance_gap=1),
        _row("elsevier", "recent", 5),
    ]

    report = build_publisher_coverage_report(rows, as_of=date(2026, 8, 27))

    assert report["source_strategy"]["policy_code"] == "review_core_discovery_gap"
    assert report["source_strategy"]["publisher_apis_required_now"] is True


def test_health_expected_sources_tracks_only_enabled_optional_connectors() -> None:
    settings = SimpleNamespace(
        europe_pmc_enabled=True,
        openalex_enabled=True,
        unpaywall_enabled=True,
        publisher_rss_enabled=False,
        springer_nature_enabled=False,
        elsevier_enabled=True,
        preprint_discovery_enabled=True,
        official_guidance_enabled=True,
        controlled_discovery_enabled=True,
    )

    sources = expected_source_names(settings)

    assert "springer-nature" not in sources
    assert "elsevier" in sources
    assert "biorxiv-api" in sources


def test_cli_rejects_invalid_limits_without_opening_a_database() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/audit_literature_publisher_coverage.py"),
            "--recent-days",
            "0",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["error_code"] == "positive_limits_required"
