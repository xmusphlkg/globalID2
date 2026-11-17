"""Source-native NCDC observations and explicit archive coverage accounting."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from sqlalchemy import text

from src.data.crawlers.ng import DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, ONTOLOGY_SOURCE_ID

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV = ROOT / "data/current/ng/nigeria_ncdc_surveillance.csv"


@dataclass(frozen=True)
class NigeriaNCDCFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class NigeriaNCDCImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class NigeriaNCDCUpdater:
    country_code = "NG"
    source_scope = DEFAULT_SCOPE
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_geography_key = "country:NG:national"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "reuse_terms_unverified"
    source_aliases = {"all", "ng", "nigeria", DEFAULT_SCOPE}
    default_output_csv = DEFAULT_OUTPUT_CSV

    def __init__(self, *, output_csv=None, full_history_start_year=2016, refresh_recent_months=3, **kwargs):
        self.output_csv = Path(output_csv or self.default_output_csv)
        self.full_history_start_year = int(full_history_start_year)
        self.refresh_recent_months = int(refresh_recent_months)

    @classmethod
    def normalize_source(cls, source):
        if str(source or "all").strip().casefold() not in cls.source_aliases:
            raise ValueError(f"Unsupported NG source: {source!r}")

    async def get_db_latest_date(self, db):
        value = (await db.execute(text("SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    async def get_db_months(self, db):
        rows = (await db.execute(text("SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:code AND series.source_system=:source_id"), {"code": self.country_code, "source_id": self.ontology_source_id})).fetchall()
        return {(int(row[0]), int(row[1])) for row in rows}

    def prepare_rows(self, rows, *, source="all", start_year=None, source_audit=None, **kwargs):
        self.normalize_source(source)
        start_year = self.full_history_start_year if start_year is None else int(start_year)
        # Comparative historical cells in the first report can predate 2016.
        # Keep them when the full archive is requested rather than silently
        # discarding official observations discovered in a 2016 publication.
        selected = list(rows) if start_year == self.full_history_start_year else [r for r in rows if int(r.get("SourcePeriodYear", r["Date"][:4])) >= start_year]
        if not selected:
            raise ValueError("No verified NCDC observations; inspect the source audit")
        latest = max(date.fromisoformat(r["Date"]) for r in selected)
        audit = dict(source_audit or {})
        audit["observations"] = len(selected)
        return NigeriaNCDCFetchResult(selected, latest, self.output_csv, [f"[source] {DEFAULT_SOURCE_NAME}: {len(selected)} verified source observations", f"[coverage] {audit.get('status', 'partial')}; complete public archive discovery is distinct from validated numerical extraction", "[release] public release pending source review and verified snapshot"], audit)

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        # The shared v3 store performs all source-series writes atomically.
        return NigeriaNCDCImportResult(0, 0, db_latest_date, source_latest_date, False)


def write_review_export(fetched):
    fetched.source_csv.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for row in fetched.rows for k in row))
    with fetched.source_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(fetched.rows)
    (fetched.source_csv.parent / "source_audit.json").write_text(json.dumps(fetched.source_audit, ensure_ascii=False, indent=2), encoding="utf-8")
