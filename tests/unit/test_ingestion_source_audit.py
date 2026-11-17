"""Source exclusions must survive beyond transient collector logs."""
from sqlalchemy import select
from types import SimpleNamespace

import pytest

from src.domain.collection import RunStage
from src.services.ingestion import runner
from tests.unit.test_cn_us_jp_crawl_pipelines import db, inputs


async def test_runner_persists_source_exclusion_audit_with_organize_stage(db):
    params = inputs()
    original = params["connector"].organize
    audit = [{"file": "COQUBR26.dbc", "assigned_count": 3120,
              "unknown_residence_count": 13, "undated_count": 0}]

    def organize(*args, **kwargs):
        fetched = original(*args, **kwargs)
        fetched.source_audit = audit
        return fetched

    params["connector"].organize = organize
    await runner.run_surveillance(**params)
    async with db() as session:
        stage = (await session.execute(select(RunStage).where(RunStage.stage == "organize"))).scalar_one()
        assert stage.status == "completed"
        assert stage.metrics["source_audit"] == audit


@pytest.mark.parametrize("process", [True, False])
async def test_unavailable_source_keeps_audit_and_cannot_complete_a_numerical_import(db, process):
    params = inputs()
    params["process"] = process
    params["updater"].requires_verified_observations = True
    audit = {"status": "source_access_blocked", "coverage_complete": False,
             "failures": [{"stage": "pdf", "error": "timeout"}]}
    params["connector"].organize = lambda *a, **kw: SimpleNamespace(rows=[], source_audit=audit)
    if process:
        with pytest.raises(ValueError, match="source_access_blocked"):
            await runner.run_surveillance(**params)
    else:
        result = await runner.run_surveillance(**params)
        assert result.total_records == 0 and result.processed_reports == 0
    params["service"]._import_rows_with_series.assert_not_awaited()
    async with db() as session:
        organize = (await session.execute(select(RunStage).where(RunStage.stage == "organize"))).scalar_one()
        store = (await session.execute(select(RunStage).where(RunStage.stage == "store"))).scalar_one()
        assert organize.metrics["source_audit"] == audit
        assert store.status == ("failed" if process else "completed")
