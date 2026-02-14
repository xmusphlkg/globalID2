"""
Seeds the database with standard diseases from the CSV file.

This script ensures that the `diseases` table is populated with the
official list of diseases from `configs/standard_diseases.csv`.
It uses the `disease_id` as the primary identifier.
"""
import asyncio
import sys
from pathlib import Path

import pandas as pd

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src.core.database import get_db
from src.core.logging import get_logger
from src.domain.disease import Disease

logger = get_logger(__name__)

STANDARD_DISEASES_CSV = ROOT / "configs/standard_diseases.csv"


async def seed_diseases():
    """
    Reads the standard diseases CSV and populates the diseases table.
    """
    logger.info("Starting disease seeding process...")

    if not STANDARD_DISEASES_CSV.exists():
        logger.error(f"Standard diseases file not found at: {STANDARD_DISEASES_CSV}")
        return

    df = pd.read_csv(STANDARD_DISEASES_CSV).fillna('')
    logger.info(f"Loaded {len(df)} diseases from CSV and filled NA values.")

    diseases_to_add = []
    async with get_db() as db_session:
        for _, row in df.iterrows():
            disease_id = row["disease_id"]
            
            disease = Disease(
                name=disease_id,  # Using disease_id as the main name identifier
                name_en=row.get("standard_name_en"),
                category=row.get("category"),
                icd_10=str(row.get("icd_10")) if pd.notna(row.get("icd_10")) else None,
                icd_11=str(row.get("icd_11")) if pd.notna(row.get("icd_11")) else None,
                description=str(row.get("description_zh")) if pd.notna(row.get("description_zh")) else None,
                metadata_={"standard_name_zh": str(row.get("standard_name_zh")) if pd.notna(row.get("standard_name_zh")) else None}
            )
            diseases_to_add.append(disease)

        records = []
        for _, row in df.iterrows():
            disease_id = row["disease_id"]
            records.append({
                "name": disease_id,
                "name_en": row.get("standard_name_en") or None,
                "category": row.get("category") or None,
                "icd_10": str(row.get("icd_10")) if pd.notna(row.get("icd_10")) and row.get("icd_10") != "" else None,
                "icd_11": str(row.get("icd_11")) if pd.notna(row.get("icd_11")) and row.get("icd_11") != "" else None,
                "aliases": [],
                "keywords": [],
                "description": str(row.get("description_zh")) if pd.notna(row.get("description_zh")) and row.get("description_zh") != "" else None,
                "metadata": {"standard_name_zh": str(row.get("standard_name_zh")) if pd.notna(row.get("standard_name_zh")) and row.get("standard_name_zh") != "" else None},
                "is_active": True,
            })

        if records:
            logger.info(f"Upserting {len(records)} diseases into the database (no truncation).")
            async with get_db() as db_session:
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                table = Disease.__table__
                stmt = pg_insert(table).values(records)
                stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
                await db_session.execute(stmt)
                logger.info("Seeding script finished. Records are staged for commit.")
        else:
            logger.info("No diseases to seed.")


async def main():
    try:
        await seed_diseases()
    except Exception as e:
        logger.error(f"An unexpected error occurred during seeding: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())