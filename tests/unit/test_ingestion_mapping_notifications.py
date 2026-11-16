"""Historical source discovery can retain categories without mailing reviewers."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.ingestion import series_rows
from src.services.disease_mapping_registry_service import disease_mapping_registry_service


@pytest.mark.parametrize("optout,expected", [(False, True), (True, False)])
async def test_mapping_notification_policy_preserves_default(monkeypatch, optout, expected):
    class Session:
        pass

    rows = [{"SourceDiseaseCode": "15", "Cases": "1"}]
    discover = AsyncMock(return_value={"source_category_count": 1, "new_category_count": 1})
    monkeypatch.setattr(series_rows, "AsyncSession", Session)
    monkeypatch.setattr(disease_mapping_registry_service, "discover_rows", discover)
    result = SimpleNamespace(
        upserted=1, skipped_unmatched=0, skipped_ambiguous=0,
        skipped_invalid=0, skipped_registry_not_synced=0,
        quality_report=SimpleNamespace(issues=(), highest_severity=None),
    )
    store = SimpleNamespace(
        enrich_registry_identities=lambda source_rows, *args, **kwargs: source_rows,
        save_rows=AsyncMock(return_value=result),
    )
    updater = SimpleNamespace(country_code="TH", ontology_source_id="SRC_TH_DDS_WEEKLY")
    if optout:
        updater.mapping_notifications_enabled = False
    saved = await series_rows.save_series_rows(
        Session(), updater, rows, store_type=lambda: store,
        save_batch=AsyncMock(), quality_policy_for=lambda _: SimpleNamespace(mode="off"),
        source_ids={}, mutation_lock=AsyncMock(),
        logger=SimpleNamespace(info=lambda *args: None),
    )
    assert saved.upserted == 1
    assert discover.await_args.kwargs["notify"] is expected
    if optout:
        assert discover.await_args.kwargs["notifications_enabled"] is False
    else:
        assert "notifications_enabled" not in discover.await_args.kwargs
    assert store.save_rows.await_args.args[1] == rows


async def test_review_completion_respects_persisted_notification_optout():
    from src.services.disease_mapping_ai_service import DiseaseMappingAIService

    db = SimpleNamespace(execute=AsyncMock())
    await DiseaseMappingAIService()._queue_suggestion_notification(
        db, SimpleNamespace(metadata_={"notifications_enabled": False}),
        1, outcome="new_concept_pending",
    )
    db.execute.assert_not_awaited()


async def test_delivery_skips_events_queued_by_a_worker_with_older_policy(monkeypatch):
    from contextlib import asynccontextmanager
    from src.services import mapping_notification_service as module

    event = SimpleNamespace(metadata_={"category_id": 7}, status="pending", last_error=None)

    def result(items):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items),
                               scalar_one_or_none=lambda: None)

    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        result([]), result([]), result([]), result([event]), result([7]),
    ]), commit=AsyncMock())

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setenv("MAPPING_EMAIL_DIGEST_ENABLED", "true")
    service = module.MappingNotificationService()
    service.transport = SimpleNamespace(send=AsyncMock())
    summary = await service.process_once(force=True)
    assert summary["claimed"] == summary["messages_sent"] == 0
    assert event.status == "skipped"
    db.commit.assert_awaited_once()
    service.transport.send.assert_not_awaited()


async def test_mixed_optout_claims_retained_events_before_releasing_locks(monkeypatch):
    from contextlib import asynccontextmanager
    from src.services import mapping_notification_service as module

    opted_out = SimpleNamespace(id=7, metadata_={"category_id": 7}, status="pending", last_error=None)
    retained = SimpleNamespace(id=8, metadata_={"category_id": 8}, status="pending", last_error=None,
                               attempts=0, provider="mock", recipients=["test@example.invalid"])
    commits = []

    def result(items):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items),
                               scalar_one_or_none=lambda: None)

    async def commit():
        commits.append((opted_out.status, retained.status))
        # Simulates a second consumer immediately after PostgreSQL releases
        # FOR UPDATE locks: no eligible retained event may still be pending.
        assert retained.status not in {"pending", "retry"}

    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        result([]), result([]), result([]), result([opted_out, retained]), result([7]), result([retained]),
    ]), commit=commit)

    @asynccontextmanager
    async def database():
        yield db

    monkeypatch.setattr(module, "get_database", database)
    monkeypatch.setenv("MAPPING_EMAIL_DIGEST_ENABLED", "true")
    service = module.MappingNotificationService()
    service._digest_content = AsyncMock(return_value=("subject", "text", "html"))
    service.transport = SimpleNamespace(send=AsyncMock(return_value=module.DeliveryResult(True, False, "mock", {}, None)))
    summary = await service.process_once(force=True)
    assert commits == [("skipped", "sending"), ("skipped", "sent")]
    assert summary["claimed"] == summary["sent"] == 1
    service.transport.send.assert_awaited_once()
