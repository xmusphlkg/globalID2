"""Source exclusions must survive beyond transient collector logs."""
from sqlalchemy import select

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
