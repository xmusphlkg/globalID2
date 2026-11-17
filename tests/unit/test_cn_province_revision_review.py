"""Scheduled province refresh preserves reviewed facts without blocking new data."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.data.processors.cn_provinces import CNProvinceUpdater, expand_province_metric_rows
from src.data.storage.series_observation_store import SeriesObservationStore
from src.domain import TaskStatus
from src.services.automation_service import AutomationJobConfig, _ingestion_job_health
from src.services.ingestion.countries import cn_province as connector


def row(month=9, cases=3, **changes):
    return {
        "Date": f"2026-{month:02d}-01", "Cases": cases,
        "JurisdictionCode": "CN-BJ", "GeographyKey": "country:CN-BJ:national",
        "SourceID": "SRC_CN_PROV_MONTHLY_REPORT", "SourceDiseaseCode": "hepatitis_a",
        "RawDiseaseLabel": "甲型肝炎", "DefinitionVersion": "CN_PROVINCE_MONTHLY_REPORT_V1",
        "SourceURL": "https://official.example/report", "Raw": {"artifact_sha256": "a" * 64},
        **changes,
    }


def observations(rows):
    return SeriesObservationStore().build_observations(
        expand_province_metric_rows(rows), "CN", source_id="SRC_CN_PROV_MONTHLY_REPORT",
    ).observations


def existing(observation, **changes):
    return SimpleNamespace(**{**observation, "quality_status": "revised", **changes})


def setup_commit(monkeypatch, current, audit=None):
    task = SimpleNamespace(id=9, metadata_={"scheduled_trigger": True})
    stage = SimpleNamespace(metrics={"rows": 4})
    db = SimpleNamespace(execute=AsyncMock(side_effect=[
        Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=current)))),
        Mock(scalar_one=Mock(return_value=task)),
        Mock(scalar_one_or_none=Mock(return_value=stage)),
    ]), add=Mock())
    lock, dirty = AsyncMock(), AsyncMock()
    monkeypatch.setattr(connector, "acquire_country_data_mutation_lock", lock)
    monkeypatch.setattr(connector, "mark_dirty", dirty)
    store = SeriesObservationStore()
    async def save_rows(db, rows, country_code, **kwargs):
        return SimpleNamespace(upserted=len(rows), skipped_unmatched=0, skipped_ambiguous=0,
                               skipped_invalid=0, skipped_registry_not_synced=0)
    store.save_rows = AsyncMock(side_effect=save_rows)
    return db, task, stage, CNProvinceUpdater(store, country_code="CN-BJ"), lock, dirty


async def test_protected_case_is_quarantined_while_deaths_and_new_month_import(monkeypatch):
    current = existing(observations([row(cases=2)])[0])
    db, task, stage, updater, lock, dirty = setup_commit(monkeypatch, [current])
    fetched = SimpleNamespace(rows=[row(Deaths=0), row(10, Deaths=1)], source_audit={"status": "parsed"})
    result = await connector.ProvinceCommitStrategy().commit(db, updater=updater, fetched=fetched, task_uuid="task")
    assert result.inserted_or_updated == 3
    saved = updater.store.save_rows.await_args.args[1]
    assert [(r["Date"], r["SourceDiseaseCode"], r["Cases"]) for r in saved] == [
        ("2026-09-01", "hepatitis_a__deaths", 0),
        ("2026-10-01", "hepatitis_a", 3),
        ("2026-10-01", "hepatitis_a__deaths", 1),
    ]
    assert current.value == 2 and current.quality_status == "revised"
    assert all(not r.get("AuthoritativeRevision") and not r.get("DataStatus") for r in saved)
    audit = task.metadata_["province_ingestion"]
    candidate = audit["revision_candidates"][0]
    assert (candidate["old_value"], candidate["new_value"]) == (2, 3)
    assert candidate["artifact_sha256"] == "a" * 64
    assert candidate["source_url"] == row()["SourceURL"]
    assert candidate["raw_candidate"]["Deaths"] == 0
    assert audit["status"] == stage.metrics["source_audit"]["status"] == "needs_review"
    assert task.metadata_["scheduled_trigger"] is True
    assert db.add.call_args.args[0].metadata_["level"] == "warning"
    lock.assert_awaited_once_with(db, "CN")
    dirty.assert_awaited_once_with(db, line="surveillance", scope_key="CN-BJ", task_uuid="task")


async def test_protected_death_does_not_get_reexpanded_from_accepted_case(monkeypatch):
    current = existing(observations([row(Deaths=1)])[1], quality_status="final")
    db, task, stage, updater, lock, dirty = setup_commit(monkeypatch, [current])
    fetched = SimpleNamespace(rows=[row(Deaths=0)], source_audit={"status": "partial", "errors": [{"error": "other month unavailable"}]})
    result = await connector.ProvinceCommitStrategy().commit(db, updater=updater, fetched=fetched, task_uuid="task")
    saved = updater.store.save_rows.await_args.args[1]
    assert len(saved) == result.inserted_or_updated == 1
    assert saved[0]["SourceDiseaseCode"] == "hepatitis_a"
    assert task.metadata_["province_ingestion"]["source_audit"]["collection_status"] == "partial"
    assert task.metadata_["province_ingestion"]["revision_candidates"][0]["old_quality_status"] == "final"


async def test_all_protected_changes_leave_facts_clean_but_record_review(monkeypatch):
    current = existing(observations([row(cases=2)])[0])
    db, task, stage, updater, lock, dirty = setup_commit(monkeypatch, [current])
    result = await connector.ProvinceCommitStrategy().commit(db, updater=updater,
        fetched=SimpleNamespace(rows=[row()], source_audit={"status": "parsed"}), task_uuid="task")
    assert result.inserted_or_updated == 0
    updater.store.save_rows.assert_not_awaited()
    dirty.assert_not_awaited()
    assert len(task.metadata_["province_ingestion"]["revision_candidates"]) == 1


async def test_partial_collection_is_persisted_even_without_revision_conflicts(monkeypatch):
    db, task, stage, updater, lock, dirty = setup_commit(monkeypatch, [])
    await connector.ProvinceCommitStrategy().commit(db, updater=updater,
        fetched=SimpleNamespace(rows=[row()], source_audit={"status": "narrative_sparse"}), task_uuid="task")
    assert task.metadata_["province_ingestion"]["status"] == "narrative_sparse"
    assert stage.metrics["source_audit"]["revision_candidate_count"] == 0
    db.add.assert_not_called()


def copied_case_rows(month, *, increase=0):
    diseases = (
        ("hepatitis_a", "甲型肝炎", 101),
        ("hepatitis_b", "乙型病毒性肝炎", 202),
        ("influenza", "流行性感冒", 303),
        ("tuberculosis", "肺结核", 404),
    )
    return [row(month, cases=value + increase, SourceDiseaseCode=code, RawDiseaseLabel=label)
            for code, label, value in diseases]


async def test_new_month_copied_from_previous_is_held_as_a_whole(monkeypatch):
    previous = [existing(item, quality_status="raw") for item in observations(copied_case_rows(8))]
    db, task, stage, updater, lock, dirty = setup_commit(monkeypatch, previous)
    current = copied_case_rows(9)
    current[0]["Deaths"] = 1
    fetched = SimpleNamespace(rows=[*current, row(10, cases=7)], source_audit={"status": "parsed"})

    result = await connector.ProvinceCommitStrategy().commit(
        db, updater=updater, fetched=fetched, task_uuid="task")

    assert result.inserted_or_updated == 1
    saved = updater.store.save_rows.await_args.args[1]
    assert [(item["Date"], item["Cases"]) for item in saved] == [("2026-10-01", 7)]
    audit = task.metadata_["province_ingestion"]
    assert audit["status"] == "needs_review"
    assert len(audit["revision_candidates"]) == 5
    assert {item["reason"] for item in audit["revision_candidates"]} == {"suspected_previous_month_copy"}
    assert audit["source_audit"]["suspected_previous_month_copies"][0]["matched_count"] == 4


async def test_monthly_copy_review_does_not_hold_independent_datacenter_fact(monkeypatch):
    previous = [existing(item, quality_status="raw") for item in observations(copied_case_rows(8))]
    db, task, stage, updater, lock, dirty = setup_commit(monkeypatch, previous)
    center = row(9, cases=17, SourceID="SRC_CN_PROV_DATACENTER")
    fetched = SimpleNamespace(rows=[*copied_case_rows(9), center], source_audit={"status": "parsed"})

    result = await connector.ProvinceCommitStrategy().commit(
        db, updater=updater, fetched=fetched, task_uuid="task")

    assert result.inserted_or_updated == 1
    saved = updater.store.save_rows.await_args.args[1]
    assert len(saved) == 1 and saved[0]["SourceID"] == "SRC_CN_PROV_DATACENTER"
    assert len(task.metadata_["province_ingestion"]["revision_candidates"]) == 4


async def test_old_month_copied_from_previous_blocks_silent_replacement(monkeypatch):
    previous = [existing(item, quality_status="raw") for item in observations(copied_case_rows(8))]
    copied = [existing(item, quality_status="raw") for item in observations(copied_case_rows(9))]
    db, task, stage, updater, lock, dirty = setup_commit(monkeypatch, [*previous, *copied])
    fetched = SimpleNamespace(rows=copied_case_rows(9, increase=20), source_audit={"status": "parsed"})

    result = await connector.ProvinceCommitStrategy().commit(
        db, updater=updater, fetched=fetched, task_uuid="task")

    assert result.inserted_or_updated == 0
    updater.store.save_rows.assert_not_awaited()
    dirty.assert_not_awaited()
    audit = task.metadata_["province_ingestion"]
    assert len(audit["revision_candidates"]) == 4
    assert all(item["old_value"] != item["new_value"] for item in audit["revision_candidates"])
    assert audit["source_audit"]["suspected_previous_month_copies"][0]["previous_month"] == "2026-08"


@pytest.mark.parametrize("changes,expected", [
    ({}, True), ({"DataStatus": "final"}, False),
    ({"AuthoritativeRevision": True}, False),
    ({"DataStatus": "revised", "AllowEqualQualityOverwrite": False}, True),
    ({"DataStatus": "revised"}, False), ({"Cases": 2}, False),
])
def test_review_uses_store_quality_policy_without_promoting_authority(changes, expected):
    incoming = observations([row(**changes)])[0]
    current = existing(observations([row(cases=2)])[0])
    assert (connector._protected_revision_candidate(incoming, current) is not None) is expected


@pytest.mark.parametrize("audit_status,health", [
    ("needs_review", "needs_review"), ("partial", "partial"),
    ("narrative_sparse", "partial"), ("parsed", "healthy"),
])
def test_automation_health_cannot_report_partial_or_unreviewed_data_as_healthy(audit_status, health):
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(status=TaskStatus.COMPLETED, completed_at=now, metadata_={
        "province_ingestion": {"status": audit_status, "revision_candidates": [{"old_value": 2}]},
    })
    job = AutomationJobConfig(job_id="cn-bj", name="Beijing", country_code="CN-BJ", interval_minutes=10080)
    assert _ingestion_job_health(job, task, task, now=now)["health_status"] == health
