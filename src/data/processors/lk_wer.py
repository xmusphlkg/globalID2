"""Sri Lanka WER source identity; numeric ingestion awaits a verified original."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from src.data.crawlers.lk_wer import (
    DEFAULT_SCOPE, DEFAULT_SOURCE_NAME, HISTORY_START_YEAR, ONTOLOGY_SOURCE_ID,
    SriLankaWERContractError,
)


@dataclass(frozen=True)
class SriLankaWERFetchResult:
    rows: list[dict]
    source_latest_date: date | None
    source_csv: Path
    script_logs: list[str]
    source_audit: dict


@dataclass(frozen=True)
class SriLankaWERImportResult:
    inserted_or_updated: int
    skipped_unmapped: int
    db_latest_date: date | None
    source_latest_date: date | None
    imported_new_data: bool


class SriLankaWERUpdater:
    country_code = "LK"
    source_scope = DEFAULT_SCOPE
    source_name = DEFAULT_SOURCE_NAME
    ontology_source_id = ONTOLOGY_SOURCE_ID
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = False
    mapping_notifications_enabled = False
    license_review_status = "reuse_terms_unverified"
    # Verified for archived English reports using the stable Table 5 WRCD
    # layout (2007 sample and matching legacy reports); current site formats
    # remain unverified and public release stays disabled.
    numerical_contract_verified = True

    def __init__(self, *, output_csv=None, full_history_start_year=HISTORY_START_YEAR, **kwargs):
        self.output_csv = Path(output_csv or Path(__file__).resolve().parents[3] /
                               "data/current/lk/sri_lanka_wer_weekly.csv")
        self.full_history_start_year = int(full_history_start_year)

    @staticmethod
    def normalize_source(source):
        if str(source or "all").strip().casefold() not in {"all", "lk", "sri_lanka", DEFAULT_SCOPE}:
            raise ValueError(f"Unsupported Sri Lanka WER source: {source!r}")

    async def get_db_latest_date(self, db):
        value = (await db.execute(text(
            "SELECT MAX(obs.time) FROM disease_series_observations obs "
            "JOIN disease_surveillance_series series ON series.series_code=obs.series_code "
            "WHERE series.country_code=:code AND series.source_system=:source_id"
        ), {"code": self.country_code, "source_id": self.ontology_source_id})).scalar()
        return value.date() if isinstance(value, datetime) else value

    def prepare_rows(self, rows, *, source=DEFAULT_SCOPE, source_audit=None, **kwargs):
        self.normalize_source(source)
        if not rows or any("Date" not in row for row in rows):
            raise SriLankaWERContractError("No verified Sri Lanka WER observations")
        latest = max(date.fromisoformat(row["Date"]) for row in rows)
        audit = dict(source_audit or {})
        audit.setdefault("status", "validated_legacy_table5")
        audit["observations"] = len(rows)
        return SriLankaWERFetchResult(rows, latest, self.output_csv,
            [f"[LK] {len(rows)} source-native Table 5 notification rows",
             "[coverage] archived legacy WER Table 5; A=current week and B=year cumulative",
             "[release] public release remains disabled pending rights and snapshot review"], audit)

    async def import_rows(self, db, rows, *, db_latest_date=None, source_latest_date=None, force=False):
        # Shared source-series store owns writes; this compatibility result is
        # retained for connectors that call updater.import_rows directly.
        return SriLankaWERImportResult(0, 0, db_latest_date, source_latest_date, False)
