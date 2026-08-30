"""Lossless persistence for Chinese province-level source observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from src.data.crawlers.cn_provinces import (
    DATACENTER_SOURCE_ID,
    MONTHLY_REPORT_SOURCE_ID,
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

    def __init__(self, store: SeriesObservationStore | None = None) -> None:
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
            result = await self.store.save_rows(
                db,
                source_rows,
                "CN",
                source_id=source_id,
                quality_policy=SeriesObservationQualityPolicy(
                    mode="fail_closed",
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
