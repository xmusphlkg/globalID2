"""
GlobalID V2 Record Store

Batch-writes standardised disease records to PostgreSQL.

Key improvements over the original DataProcessor._save_to_database:
1. Batch Disease lookup eliminates N+1 queries.
2. Single PostgreSQL INSERT … ON CONFLICT DO UPDATE statement per batch.
3. ``dedup_deleted`` variable is always initialised before any retry loop.
4. Decoupled from DataProcessor for independent testability.
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_logger
from src.core.database import get_db
from src.core.missing_values import normalize_rate_value
from src.domain import Country, Disease, DiseaseRecord

logger = get_logger(__name__)


def _normalize_report_time(value: object) -> datetime:
    """Anchor a report date to UTC midnight."""
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"Invalid report time: {value!r}")
    return datetime.combine(ts.date(), time.min, tzinfo=timezone.utc)


class RecordStore:
    """
    Disease record persistence layer.

    Responsibilities:
    - Batch-upsert a normalised DataFrame into the ``disease_records`` table.
    - Optionally clean up adjacent duplicate snapshots.

    Does NOT handle HTTP fetching, HTML parsing, or disease name mapping.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Public methods
    # ─────────────────────────────────────────────────────────────────────────

    async def save_dataframe(
        self,
        df: pd.DataFrame,
        country_code: str,
        *,
        cleanup_adjacent_duplicates: bool = True,
        db: AsyncSession | None = None,
    ) -> Tuple[int, int, int]:
        """
        Batch-upsert a standardised DataFrame into the database.

        Args:
            df: DataFrame with columns:
                ``Date``, ``disease_id`` (e.g. "D004"), ``Cases``, ``Deaths``,
                ``Incidence``, ``Mortality``, ``Source``, ``Diseases``, ``DiseasesCN``,
                ``Province``, ``ProvinceCN``, ``YearMonth``.
            country_code:               Uppercase country code (e.g. "CN").
            cleanup_adjacent_duplicates: Whether to clean up adjacent duplicate snapshots
                                         before committing.
            db:                         Optional caller-owned session.  Supplying it lets
                                        legacy and source-series writes share one transaction.

        Returns:
            Tuple of ``(upserted_count, skipped_count, dedup_deleted_count)``.
        """
        if df.empty:
            logger.warning(f"[RecordStore][{country_code}] DataFrame is empty, skipping write")
            return 0, 0, 0

        try:
            if db is not None:
                return await self._save_dataframe_in_session(
                    db,
                    df,
                    country_code,
                    cleanup_adjacent_duplicates=cleanup_adjacent_duplicates,
                )

            async with get_db() as owned_db:
                return await self._save_dataframe_in_session(
                    owned_db,
                    df,
                    country_code,
                    cleanup_adjacent_duplicates=cleanup_adjacent_duplicates,
                )
        except Exception:
            logger.exception(f"[RecordStore][{country_code}] DB write failed")
            raise

    async def _save_dataframe_in_session(
        self,
        db: AsyncSession,
        df: pd.DataFrame,
        country_code: str,
        *,
        cleanup_adjacent_duplicates: bool,
    ) -> Tuple[int, int, int]:
        """Write using ``db`` without committing the caller-owned transaction."""
        country = await self._get_country(db, country_code)
        if country is None:
            logger.warning(f"[RecordStore] Country not found | code={country_code}")
            return 0, len(df), 0

        # 1️⃣  Batch-load disease_code → diseases.id mappings (eliminates N+1)
        disease_map = await self._batch_load_diseases(db, df)

        # 2️⃣  Build record list
        records, skipped = self._build_records(df, country.id, disease_map)

        if not records:
            logger.warning(
                f"[RecordStore][{country_code}] No valid records to write (all skipped)"
            )
            return 0, skipped, 0

        # 3️⃣  PostgreSQL batch upsert (single SQL statement)
        upserted = await self._upsert_records(db, records)

        # 4️⃣  Optional: clean up adjacent duplicate snapshots
        dedup_deleted = 0
        if cleanup_adjacent_duplicates:
            dedup_deleted = await self._cleanup_adjacent_duplicate_snapshots(
                db, country.id
            )

        if upserted > 0:
            logger.info(
                f"[RecordStore][{country_code}] Upsert done"
                f" | upserted={upserted} skipped={skipped} dedup_deleted={dedup_deleted}"
            )
        return upserted, skipped, dedup_deleted

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def _get_country(db: AsyncSession, country_code: str) -> Optional[Country]:
        result = await db.execute(
            select(Country).where(Country.code == country_code.upper())
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _batch_load_diseases(
        db: AsyncSession, df: pd.DataFrame
    ) -> Dict[str, int]:
        """
        Load all disease records referenced in the DataFrame in a single query.

        ``Disease.name`` stores the disease_code (e.g. "D004").
        Returns ``{disease_code: diseases.id}``.
        """
        if "disease_id" not in df.columns:
            return {}

        codes = [
            str(c) for c in df["disease_id"].dropna().unique() if str(c).strip()
        ]
        if not codes:
            return {}

        result = await db.execute(
            select(Disease.name, Disease.id).where(Disease.name.in_(codes))
        )
        return {row[0]: row[1] for row in result.all()}

    @staticmethod
    def _build_records(
        df: pd.DataFrame,
        country_id: int,
        disease_map: Dict[str, int],
    ) -> Tuple[List[dict], int]:
        """
        Convert DataFrame rows into a list of dicts ready for ORM insertion.

        Returns ``(records, skipped_count)``.
        """
        records: List[dict] = []
        skipped = 0
        # Deduplicate within the batch to avoid intra-batch primary key conflicts
        seen_keys: set = set()

        for _, row in df.iterrows():
            disease_code = str(row.get("disease_id", "") or "").strip()
            date_val = row.get("Date")

            if not disease_code or pd.isna(date_val):
                skipped += 1
                continue

            db_disease_id = disease_map.get(disease_code)
            if db_disease_id is None:
                logger.debug(f"[RecordStore] Disease code not in DB, skipping | code={disease_code}")
                skipped += 1
                continue

            try:
                record_time = _normalize_report_time(date_val)
            except ValueError as exc:
                logger.debug(f"[RecordStore] Invalid date, skipping | error={exc}")
                skipped += 1
                continue

            key = (record_time, db_disease_id, country_id)
            if key in seen_keys:
                skipped += 1
                continue
            seen_keys.add(key)

            records.append(
                {
                    "time": record_time,
                    "disease_id": db_disease_id,
                    "country_id": country_id,
                    "cases": _to_int(row.get("Cases")),
                    "deaths": _to_int(row.get("Deaths")),
                    "incidence_rate": normalize_rate_value(row.get("Incidence")),
                    "mortality_rate": normalize_rate_value(row.get("Mortality")),
                    "data_source": _to_str(row.get("Source")),
                    "metadata_": {
                        "disease_name_en": _to_str(row.get("Diseases")),
                        "disease_name_zh": _to_str(row.get("DiseasesCN")),
                        "province": _to_str(row.get("Province")),
                        "province_cn": _to_str(row.get("ProvinceCN")),
                        "year_month": _to_str(row.get("YearMonth")),
                        "disease_code": disease_code,
                    },
                }
            )

        return records, skipped

    @staticmethod
    async def _upsert_records(db: AsyncSession, records: List[dict]) -> int:
        """
        Execute a single PostgreSQL INSERT … ON CONFLICT DO UPDATE for all records.

        Eliminates O(n) additional round-trips compared to row-by-row SELECT + INSERT/UPDATE.
        """
        stmt = pg_insert(DiseaseRecord).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["time", "disease_id", "country_id"],
            set_={
                "cases": stmt.excluded.cases,
                "deaths": stmt.excluded.deaths,
                "incidence_rate": stmt.excluded.incidence_rate,
                "mortality_rate": stmt.excluded.mortality_rate,
                "data_source": stmt.excluded.data_source,
                DiseaseRecord.metadata_: stmt.excluded["metadata"],
            },
        )
        result = await db.execute(stmt)
        # rowcount reflects rows actually modified; INSERT ON CONFLICT returns 1 per row
        return result.rowcount or len(records)

    @staticmethod
    async def _cleanup_adjacent_duplicate_snapshots(
        db: AsyncSession, country_id: int
    ) -> int:
        """
        Delete adjacent-day duplicate snapshots with identical cases and deaths.

        Retention rule: prefer end-of-month values (day=1 keeps the older row;
        all other days keep the newer row).
        """
        result = await db.execute(
            text(
                """
                WITH candidate AS (
                    SELECT
                        a.ctid AS old_ctid,
                        b.ctid AS new_ctid,
                        EXTRACT(DAY FROM b.time::date) AS new_day
                    FROM disease_records a
                    JOIN disease_records b
                        ON b.country_id = a.country_id
                        AND b.disease_id = a.disease_id
                        AND b.time::date = a.time::date + 1
                        AND COALESCE(b.cases, -1) = COALESCE(a.cases, -1)
                        AND COALESCE(b.deaths, -1) = COALESCE(a.deaths, -1)
                    WHERE a.country_id = :country_id
                ),
                targets AS (
                    SELECT CASE WHEN new_day = 1 THEN old_ctid ELSE new_ctid END AS del_ctid
                    FROM candidate
                )
                DELETE FROM disease_records d
                USING targets t
                WHERE d.ctid = t.del_ctid
                """
            ),
            {"country_id": country_id},
        )
        return result.rowcount or 0


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_int(value: object) -> Optional[int]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _to_str(value: object) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s if s else None
