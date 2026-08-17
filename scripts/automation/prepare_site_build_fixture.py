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
    empty_research = {
        "schema_version": 1,
        "last_updated": None,
        "metrics": {},
        "featured": [],
        "articles": [],
        "preprints": [],
        "integrity_alerts": [],
        "historical_baseline": [],
        "reviews_and_guidelines": [],
        "emerging_topics": [],
        "knowledge_graph": {"nodes": [], "edges": [], "stats": {}, "quality": {}},
        "facets": {"diseases": [], "countries": [], "topics": [], "weeks": []},
        "publication_timeline": [],
        "pipeline_funnel": [],
        "completeness": [],
        "visualizations": {"hotspots": {}},
        "weekly_briefs": [],
        "surveillance_evidence": {"available": False},
    }
    empty_hotspots = {
        "schema_version": "research_hotspots.v1",
        "generated_at": None,
        "grain": {},
        "streamgraph": {"periods": [], "series": [], "method": {}},
        "heatmap": {"periods": [], "rows": [], "method": {}},
        "burst_timeline": {"bursts": [], "method": {}},
        "alluvial": {"periods": [], "topics": [], "nodes": [], "links": [], "method": {}},
        "interpretation_note": {"en": "CI fixture", "zh": "CI fixture"},
    }
    return {
        "src/data/meta.json": {
            "generated_at": "2000-01-01T00:00:00Z",
            "total_countries": 0,
            "total_diseases": 0,
            "total_reports": 0,
            "countries": [],
            "knowledge_quality": {},
            "disease_ontology": {},
        },
        "src/data/diseases/index.json": [],
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
        "src/data/research/index.json": empty_research,
        "src/data/research/hotspots.json": empty_hotspots,
        "src/data/situation/v3/latest.json": dict(situation),
        "src/data/situation/latest.json": dict(situation),
        "public/site-data/situation/v3/latest.json": dict(situation),
        "public/site-data/situation/latest.json": dict(situation),
    }


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
