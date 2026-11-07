"""Missing reviewed series recover without overwriting mappings or committing facts."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import DiseaseSurveillanceSeries
from src.ontology import load_disease_ontology
from src.services.disease_ontology_sync_service import restore_missing_registry_series


@pytest.mark.asyncio
async def test_recovery_is_scoped_idempotent_and_transactional(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}")
    table = DiseaseSurveillanceSeries.__table__
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: table.create(conn))
    ontology = load_disease_ontology()
    codes = {
        "SER_CA_PHAC_CNDSS_D9_ANNUAL", "SER_CA_PHAC_CNDSS_D251_ANNUAL",
        "SER_CA_PHAC_CNDSS_D252_ANNUAL", "SER_CA_PHAC_CNDSS_D257_ANNUAL",
    }
    async with async_sessionmaker(engine)() as db:
        assert await restore_missing_registry_series(
            db, ontology, codes | {"UNKNOWN", "SER_US_NHSS_HIV_ANNUAL"}, country_code="CA",
        ) == codes
        rows = (await db.execute(select(DiseaseSurveillanceSeries))).scalars().all()
        assert {row.series_code for row in rows} == codes
        assert all(row.metadata_["registry_id"] == ontology.to_dict()["registry_id"] for row in rows)
        rows[0].source_label = "Existing reviewed label"
        await db.flush()
        assert await restore_missing_registry_series(db, ontology, codes, country_code="CA") == codes
        await db.refresh(rows[0])
        assert rows[0].source_label == "Existing reviewed label"
        await db.rollback()
        assert not (await db.execute(select(table.c.series_code))).all()
    await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_series_does_not_touch_database():
    class NoWrites:
        async def execute(self, *_args):
            pytest.fail("Unknown definitions must not be inserted")

    assert await restore_missing_registry_series(
        NoWrites(), load_disease_ontology(), {"UNKNOWN"}, country_code="CA",
    ) == set()
