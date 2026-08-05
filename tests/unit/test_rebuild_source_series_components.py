from __future__ import annotations

import argparse
import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import rebuild_source_series_components as rebuild


def _write_transitions(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "country_code",
        "source_id",
        "local_name",
        "old_disease_id",
        "new_disease_id",
        "action",
        "evidence_field",
        "evidence_value",
        "reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _args(
    transitions: Path,
    source: Path,
    *,
    country: str = "JP",
    apply: bool = False,
    allow_partial: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        transitions=transitions,
        country=[country],
        input=[f"{country}={source}"],
        apply=apply,
        quality_mode="quarantine",
        allow_partial=allow_partial,
    )


def test_jp_plan_rebuilds_exact_component_and_preserves_missing(tmp_path: Path) -> None:
    transitions = tmp_path / "transitions.csv"
    _write_transitions(
        transitions,
        [
            {
                "country_code": "JP",
                "source_id": "SRC_JP_NIID",
                "local_name": "Bacterial dysentery",
                "old_disease_id": "D024",
                "new_disease_id": "D105",
                "action": "remap_and_reingest",
                "evidence_field": "Disease",
                "evidence_value": "Bacterial dysentery",
                "reason": "test",
            }
        ],
    )
    source = tmp_path / "jp.csv"
    source.write_text(
        "Reporting Area,Current MMWR Year,MMWR WEEK,Disease,Current week\n"
        "総数,2025,1,Bacterial dysentery,4\n"
        "総数,2025,2,Bacterial dysentery,0\n"
        "総数,2025,3,Bacterial dysentery,\n"
        "TOKYO,2025,1,Bacterial dysentery,99\n"
        "総数,2025,1,Amoebic dysentery,8\n",
        encoding="utf-8",
    )

    summary, ready_rows = rebuild.build_plan(_args(transitions, source))

    report = summary["transitions_report"][0]
    assert summary["mode"] == "dry_run"
    assert report["status"] == "ready"
    assert report["series_code"] == "SER_JP_SHIGELLOSIS_WEEKLY"
    assert report["matched_rows"] == 3
    assert report["non_empty"] == 2
    assert report["nonzero"] == 1
    assert report["zero"] == 1
    assert report["missing"] == 1
    assert report["rebuildable_observations"] == 2
    assert report["time_coverage"]["distinct_periods"] == 2
    assert len(ready_rows["JP"]) == 2
    assert all(row["Current week"] != "" for row in ready_rows["JP"])


def test_overlapping_extract_conflict_blocks_transition(tmp_path: Path) -> None:
    transitions = tmp_path / "transitions.csv"
    _write_transitions(
        transitions,
        [
            {
                "country_code": "AU",
                "source_id": "SRC_AU_NINDSS",
                "local_name": "Rubella congenital",
                "old_disease_id": "D040",
                "new_disease_id": "D168",
                "action": "source_reingest",
                "evidence_field": "",
                "evidence_value": "",
                "reason": "test",
            }
        ],
    )
    first = tmp_path / "au_history.csv"
    second = tmp_path / "au_current.csv"
    header = "Disease,DiseaseFull,Group,Year,Month,Date,Cases\n"
    first.write_text(
        header + "Rubella congenital,Rubella congenital,location_aggregated,"
        "2025,1,2025-01-01,1\n",
        encoding="utf-8",
    )
    second.write_text(
        header + "Rubella congenital,Rubella congenital,national_total,"
        "2025,1,2025-01-01,2\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(
        transitions=transitions,
        country=["AU"],
        input=[f"AU={first}", f"AU={second}"],
        apply=False,
        quality_mode="quarantine",
    )

    summary, ready_rows = rebuild.build_plan(args)

    report = summary["transitions_report"][0]
    assert report["status"] == "blocked"
    assert report["conflict_count"] == 1
    assert "conflicting" in report["unresolved_reason"]
    assert ready_rows == {}


def test_br_specialized_count_transition_is_explicitly_blocked(tmp_path: Path) -> None:
    transitions = tmp_path / "transitions.csv"
    _write_transitions(
        transitions,
        [
            {
                "country_code": "BR",
                "source_id": "SRC_BR_SINAN",
                "local_name": "Trachoma survey positive cases",
                "old_disease_id": "D193",
                "new_disease_id": "D200",
                "action": "source_reingest",
                "evidence_field": "",
                "evidence_value": "",
                "reason": "test",
            }
        ],
    )
    args = argparse.Namespace(
        transitions=transitions,
        country=["BR"],
        input=[],
        apply=False,
        quality_mode="quarantine",
    )

    summary, _ = rebuild.build_plan(args)

    report = summary["transitions_report"][0]
    assert report["status"] == "blocked"
    assert "NU_CASOPOS" in report["unresolved_reason"]
    assert report["rebuildable_observations"] == 0


def test_us_mmwr_week_53_uses_cdc_calendar(tmp_path: Path) -> None:
    transitions = tmp_path / "transitions.csv"
    _write_transitions(
        transitions,
        [
            {
                "country_code": "US",
                "source_id": "SRC_US_NNDSS",
                "local_name": "Hepatitis B, chronic, Confirmed",
                "old_disease_id": "D008",
                "new_disease_id": "D208",
                "action": "source_reingest",
                "evidence_field": "",
                "evidence_value": "",
                "reason": "test",
            }
        ],
    )
    source = tmp_path / "us.csv"
    source.write_text(
        "Reporting Area,Current MMWR Year,MMWR WEEK,Label,Current week\n"
        'U.S. Residents,2025,53,"Hepatitis B, chronic, Confirmed",34\n',
        encoding="utf-8",
    )

    summary, ready_rows = rebuild.build_plan(_args(transitions, source, country="US"))

    report = summary["transitions_report"][0]
    assert report["status"] == "ready"
    assert report["rebuildable_observations"] == 1
    assert report["time_coverage"]["start"].startswith("2026-01-03")
    assert ready_rows["US"][0]["Date"] == "2026-01-03"
    assert ready_rows["US"][0]["IsProvisional"] == "true"


def test_au_adapter_rejects_non_national_group(tmp_path: Path) -> None:
    transitions = tmp_path / "transitions.csv"
    _write_transitions(
        transitions,
        [
            {
                "country_code": "AU",
                "source_id": "SRC_AU_NINDSS",
                "local_name": "Rubella congenital",
                "old_disease_id": "D040",
                "new_disease_id": "D168",
                "action": "source_reingest",
                "evidence_field": "",
                "evidence_value": "",
                "reason": "test",
            }
        ],
    )
    source = tmp_path / "au_regional.csv"
    source.write_text(
        "Disease,DiseaseFull,Group,Year,Month,Date,Cases\n"
        "Rubella congenital,Rubella congenital,state_total,"
        "2025,1,2025-01-01,7\n",
        encoding="utf-8",
    )

    summary, ready_rows = rebuild.build_plan(_args(transitions, source, country="AU"))

    report = summary["transitions_report"][0]
    assert report["status"] == "blocked"
    assert report["matched_rows"] == 0
    assert report["rebuildable_observations"] == 0
    assert ready_rows == {}


@pytest.mark.asyncio
async def test_apply_uses_required_registry_quality_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transitions = tmp_path / "transitions.csv"
    _write_transitions(
        transitions,
        [
            {
                "country_code": "JP",
                "source_id": "SRC_JP_NIID",
                "local_name": "Bacterial dysentery",
                "old_disease_id": "D024",
                "new_disease_id": "D105",
                "action": "remap_and_reingest",
                "evidence_field": "Disease",
                "evidence_value": "Bacterial dysentery",
                "reason": "test",
            }
        ],
    )
    source = tmp_path / "jp.csv"
    source.write_text(
        "Reporting Area,Current MMWR Year,MMWR WEEK,Disease,Current week\n"
        "総数,2025,1,Bacterial dysentery,4\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    class FakeStore(rebuild.SeriesObservationStore):
        async def save_rows(self, db, rows, country_code, **kwargs):
            calls.append(
                {"db": db, "rows": rows, "country_code": country_code, **kwargs}
            )
            return SimpleNamespace(
                upserted=1,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(to_dict=lambda: {"issue_count": 0}),
            )

    db = object()

    class FakeDBContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(rebuild, "SeriesObservationStore", FakeStore)
    monkeypatch.setattr(rebuild, "get_db", lambda: FakeDBContext())

    summary = await rebuild.run(_args(transitions, source, apply=True))

    assert summary["saved"]["JP"]["upserted"] == 1
    assert len(calls) == 1
    policy = calls[0]["quality_policy"]
    assert isinstance(policy, rebuild.SeriesObservationQualityPolicy)
    assert policy.mode == "quarantine"
    assert policy.registry_coverage == "required"


@pytest.mark.asyncio
async def test_allow_partial_uses_only_ready_source_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transitions = tmp_path / "transitions.csv"
    _write_transitions(
        transitions,
        [
            {
                "country_code": "US",
                "source_id": "SRC_US_NNDSS",
                "local_name": "Hepatitis B, chronic, Confirmed",
                "old_disease_id": "D008",
                "new_disease_id": "D208",
                "action": "source_reingest",
                "evidence_field": "",
                "evidence_value": "",
                "reason": "ready component",
            },
            {
                "country_code": "US",
                "source_id": "*",
                "local_name": "Arboviral diseases",
                "old_disease_id": "D128",
                "new_disease_id": "D089",
                "action": "source_reingest",
                "evidence_field": "",
                "evidence_value": "",
                "reason": "mapping-only wildcard",
            },
        ],
    )
    source = tmp_path / "us.csv"
    source.write_text(
        "Reporting Area,Current MMWR Year,MMWR WEEK,Label,Current week\n"
        'US RESIDENTS,2025,1,"Hepatitis B, chronic, Confirmed",34\n',
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    class FakeStore(rebuild.SeriesObservationStore):
        async def save_rows(self, db, rows, country_code, **kwargs):
            calls.append(
                {"db": db, "rows": rows, "country_code": country_code, **kwargs}
            )
            return SimpleNamespace(
                upserted=1,
                skipped_unmatched=0,
                skipped_ambiguous=0,
                skipped_invalid=0,
                skipped_registry_not_synced=0,
                quality_report=SimpleNamespace(to_dict=lambda: {"issue_count": 0}),
            )

    class FakeDBContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(rebuild, "SeriesObservationStore", FakeStore)
    monkeypatch.setattr(rebuild, "get_db", lambda: FakeDBContext())

    summary = await rebuild.run(
        _args(
            transitions,
            source,
            country="US",
            apply=True,
            allow_partial=True,
        )
    )

    assert summary["ready_transitions"] == 1
    assert summary["blocked_transitions"] == 1
    assert summary["saved"]["US"]["upserted"] == 1
    assert len(calls) == 1
    assert calls[0]["source_id"] == "SRC_US_NNDSS"
    assert calls[0]["rows"][0]["IsProvisional"] == "true"


@pytest.mark.asyncio
async def test_apply_requires_explicit_country(tmp_path: Path) -> None:
    args = argparse.Namespace(
        transitions=tmp_path / "unused.csv",
        country=None,
        input=[],
        apply=True,
        quality_mode="quarantine",
    )

    with pytest.raises(ValueError, match="explicit --country"):
        await rebuild.run(args)
