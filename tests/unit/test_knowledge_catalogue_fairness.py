"""Real catalogue quality work must share the worker event loop with releases."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.knowledge.quality import KNOWLEDGE_TEXT_FIELDS
from src.services import disease_knowledge_service as module


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class CatalogueDatabase:
    """Immediate SELECTs ensure DB awaits cannot mask a blocking CPU loop."""
    def __init__(self, briefs, counts):
        self.results = iter((Rows(briefs), Rows(counts)))
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return next(self.results)


def catalogue_fixture(count=64):
    diseases = [{"disease_id": f"D{index:03d}", "name_en": f"Fixture infection {index}",
        "name_zh": f"样例感染{index}", "description": "Catalogue fairness fixture",
        "category": "Respiratory", "slug": f"fixture-{index}"} for index in range(1, count + 1)]
    text = {"en": "Patients can develop fever and respiratory symptoms after exposure. Public health teams monitor transmission and recommend prevention measures. " * 8,
        "zh": "这种感染可以引起发热和呼吸道症状。公共卫生部门监测传播，并采取预防措施。" * 8}
    briefs = [SimpleNamespace(disease_id=row["disease_id"], language=language,
        status="draft" if index % 4 == 0 else "published", updated_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
        metadata_={"pipeline_version": module.KNOWLEDGE_PIPELINE_VERSION,
            "knowledge_schema_version": module.KNOWLEDGE_SCHEMA_VERSION,
            "evidence_policy_version": module.EVIDENCE_POLICY_VERSION, "citation_version": 2},
        source_ids=[index], source_attribution="Official source",
        **{field: (None if index % 5 == 0 and field == "risk_groups" else text[language])
           for field in KNOWLEDGE_TEXT_FIELDS})
        for index, row in enumerate(diseases, 1) for language in ("en", "zh")
        if not (index % 3 == 0 and language == "zh")]
    counts = [SimpleNamespace(disease_id=row["disease_id"], source_count=index % 4)
        for index, row in enumerate(diseases)]
    # Only constructor I/O is replaced: actual schema, quality, publication
    # policy and repair assessment run unchanged for every stored brief.
    service = object.__new__(module.DiseaseKnowledgeUpdateService)
    service.load_standard_diseases = lambda: list(diseases)
    service._attach_ontology_profile_context = lambda row: row
    return service, diseases, briefs, counts


@pytest.mark.asyncio
async def test_large_real_catalogue_allows_ticker_and_ready_data_callback_before_completion(monkeypatch):
    service, diseases, briefs, counts = catalogue_fixture()
    before = deepcopy((diseases, briefs, counts))
    expected = await service.list_catalogue(CatalogueDatabase(briefs, counts))
    processed = 0
    ticks, data_callback = [], []
    ready = asyncio.get_running_loop().create_future()
    real_repair = module._profile_repair_metadata

    def repair(*args):
        nonlocal processed
        value = real_repair(*args)
        processed += 1
        if processed == 1:
            ready.set_result(None)
        return value

    monkeypatch.setattr(module, "_profile_repair_metadata", repair)
    async def data_release_callback():
        await ready
        data_callback.append((processed, catalogue.done()))

    async def ticker():
        while not catalogue.done():
            ticks.append(processed)
            await asyncio.sleep(0)

    data = asyncio.create_task(data_release_callback())
    database = CatalogueDatabase(briefs, counts)
    catalogue = asyncio.create_task(service.list_catalogue(database))
    heartbeat = asyncio.create_task(ticker())
    actual, _, _ = await asyncio.gather(catalogue, data, heartbeat)
    assert actual == expected
    assert (diseases, briefs, counts) == before
    # A callback made ready during one disease finishes before the next one,
    # rather than waiting for all 64 bilingual quality assessments.
    assert data_callback == [(1, False)]
    assert set(range(len(diseases))) <= set(ticks)
    assert len(database.statements) == 2
    assert all(statement.startswith("SELECT") for statement in database.statements)


@pytest.mark.asyncio
async def test_catalogue_cancellation_propagates_at_disease_boundary_without_partial_result(monkeypatch):
    service, diseases, briefs, counts = catalogue_fixture()
    before = deepcopy((diseases, briefs, counts))
    processed = 0
    ready = asyncio.Event()
    real_repair = module._profile_repair_metadata

    def repair(*args):
        nonlocal processed
        value = real_repair(*args)
        processed += 1
        ready.set()
        return value

    monkeypatch.setattr(module, "_profile_repair_metadata", repair)
    async def cancel_after_one():
        await ready.wait()
        catalogue.cancel()

    canceller = asyncio.create_task(cancel_after_one())
    catalogue = asyncio.create_task(service.list_catalogue(CatalogueDatabase(briefs, counts)))
    with pytest.raises(asyncio.CancelledError):
        await catalogue
    await canceller
    assert catalogue.cancelled()
    assert processed == 1
    await asyncio.sleep(0)
    assert processed == 1
    assert (diseases, briefs, counts) == before
