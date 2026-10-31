"""Lossless persistence for Chinese province-level source observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.cn_provinces import (
    DATACENTER_SOURCE_ID,
    MONTHLY_REPORT_SOURCE_ID,
    province_configs,
)
from src.data.storage.series_observation_store import (
    SeriesObservationQualityPolicy,
    SeriesObservationStore,
)


@dataclass(frozen=True)
class CNProvinceImportResult:
    inserted_or_updated: int
    source_rows: int
    skipped_rows: int
    source_ids: tuple[str, ...]
    source_latest_date: date | None
    imported_new_data: bool


class CNProvinceUpdater:
    """Persist province facts without writing the CN national legacy record."""

    country_code = "CN"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    supported_source_ids = frozenset(
        {DATACENTER_SOURCE_ID, MONTHLY_REPORT_SOURCE_ID}
    )

    def __init__(self, store: SeriesObservationStore | None = None, *, country_code: str = "CN") -> None:
        if country_code != "CN" and country_code not in province_configs():
            raise ValueError(f"Unknown Chinese province: {country_code}")
        self.country_code = country_code
        self.refresh_recent_months = 3
        self.store = store or SeriesObservationStore()

    async def import_rows(
        self,
        db: AsyncSession,
        rows: Iterable[dict[str, object]],
    ) -> CNProvinceImportResult:
        materialized = list(rows)
        by_source: dict[str, list[dict[str, object]]] = {}
        latest: date | None = None
        for row in materialized:
            source_id = str(row.get("SourceID") or "")
            if source_id not in self.supported_source_ids:
                raise ValueError(f"Unsupported Chinese province source: {source_id!r}")
            geography_key = str(row.get("GeographyKey") or "")
            if self.country_code != "CN" and geography_key != f"country:{self.country_code}:national":
                raise ValueError(f"Observation belongs to a different province: {geography_key!r}")
            if not geography_key.startswith("country:CN-"):
                raise ValueError(
                    "Province observations must not use the CN national geography: "
                    f"{geography_key!r}"
                )
            by_source.setdefault(source_id, []).append(row)
            parsed = date.fromisoformat(str(row["Date"])[:10])
            latest = max(latest, parsed) if latest else parsed

        upserted = 0
        skipped = 0
        for source_id, source_rows in sorted(by_source.items()):
            # Narrative reports contain only explicitly stated disease counts
            # and explicit no-case statements. Their sparse disease coverage
            # can trigger the fail-closed all-zero tail heuristic even when
            # the source text has been verified. Keep critical mapping and
            # registry failures blocked, while allowing report-only anomalies
            # for this explicitly marked sparse extraction path.
            narrative_only = bool(source_rows) and all(
                isinstance(row.get("Raw"), dict)
                and "explicit_narrative_value" in row["Raw"]
                for row in source_rows
            )
            result = await self.store.save_rows(
                db,
                source_rows,
                "CN",
                source_id=source_id,
                quality_policy=SeriesObservationQualityPolicy(
                    mode="quarantine" if narrative_only else "fail_closed",
                    registry_coverage="required",
                ),
            )
            upserted += result.upserted
            skipped += (
                result.skipped_unmatched
                + result.skipped_ambiguous
                + result.skipped_invalid
                + result.skipped_registry_not_synced
            )

        return CNProvinceImportResult(
            inserted_or_updated=upserted,
            source_rows=len(materialized),
            skipped_rows=skipped,
            source_ids=tuple(sorted(by_source)),
            source_latest_date=latest,
            imported_new_data=upserted > 0,
        )


__all__ = ["CNProvinceImportResult", "CNProvinceUpdater"]
