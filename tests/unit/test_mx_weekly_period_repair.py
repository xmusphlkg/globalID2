"""A period repair must reject changed evidence, concurrent facts and replay drift."""
from copy import deepcopy
import csv
import gzip
import json
from types import SimpleNamespace

import pytest

from scripts.repair_mx_weekly_periods import (
    MexicoWeeklyPeriodRepair, RepairPlan, SOURCE, compare_candidate, sha,
)


@pytest.fixture
def frozen_plan(tmp_path):
    candidate, backup = tmp_path / "candidate", tmp_path / "backup"
    candidate.mkdir(); backup.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]")

    def raw(index, corrected=False):
        return {
            "Date": "2026-10-25" if corrected else "2026-10-18",
            "SourceDiseaseCode": f"dge_{index:020x}__all__case_notifications",
            "DefinitionVersion": "reviewed", "GeographyKey": "country:MX:national",
            "Cases": str(index), "RawSHA256": "a" * 64, "SourceURL": "https://official/report.pdf",
            "SourcePage": "17", "RawDiseaseLabel": f"Category {index}",
            "Dimensions": json.dumps({"source_epi_week": 43 if corrected else 42}),
            "PeriodStart": "2026-10-25" if corrected else "2026-10-18",
            "PeriodEnd": "2026-10-31" if corrected else "2026-10-24",
            "SourceReportPeriodStart": "2026-10-25" if corrected else "2026-10-18",
            "SourceReportPeriodEnd": "2026-10-31" if corrected else "2026-10-24",
        }

    rows = [raw(i, i < 9) for i in range(1272)]
    baseline = [{"time": "2026-10-18T00:00:00+00:00", "series_code": f"SER_{i}",
                 "geography_key": "country:MX:national", "dimension_key": "all",
                 "raw_data": raw(i), "metadata": {"source_id": SOURCE}, "id": i,
                 "quality_status": "raw", "value": float(i), "unit": "count", "suppressed": False,
                 "suppression_reason": None, "dimensions": {"source_epi_week": 42}}
                for i in range(761)]
    baseline_file = backup / "mx_high_frequency_observations.jsonl.gz"
    with gzip.open(baseline_file, "wt") as stream:
        stream.write("\n".join(json.dumps(row) for row in baseline))
    (backup / "baseline.json").write_text(json.dumps({"files": {
        "mx_high_frequency_observations": {"sha256": sha(baseline_file)}}}))
    with (candidate / "observations.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    (candidate / "source_audit.json").write_text(json.dumps({
        "failed_count": 0, "excluded_source_cells": [{}] * 16, "coverage_complete": False}))
    corrections = [{"source_system": SOURCE, "source_code": raw(i)["SourceDiseaseCode"],
                    "old_date": "2026-10-18", "new_date": "2026-10-25", "source_page": 17,
                    "source_caption": "Explicit W43 exception", "original_sha256": "a" * 64,
                    "original_url": "https://official/report.pdf", "before": raw(i), "after": rows[i]}
                   for i in range(9)]
    (candidate / "period_corrections.json").write_text(json.dumps({"count": 9, "corrections": corrections}))
    return candidate, backup, manifest


def test_repair_refuses_tampered_baseline(frozen_plan):
    candidate, backup, manifest = frozen_plan
    with (backup / "mx_high_frequency_observations.jsonl.gz").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="baseline SHA"):
        RepairPlan(candidate, backup, manifest)


def test_repair_refuses_value_edit_disguised_as_period_correction(frozen_plan):
    candidate, backup, manifest = frozen_plan
    file = candidate / "period_corrections.json"
    document = json.loads(file.read_text())
    document["corrections"][0]["after"]["Cases"] = "9999"
    file.write_text(json.dumps(document))
    csv_file = candidate / "observations.csv"
    with csv_file.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["Cases"] = "9999"
    with csv_file.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    with pytest.raises(ValueError, match="more than the explicit week"):
        RepairPlan(candidate, backup, manifest)


def test_repair_detects_inputs_changed_after_review(frozen_plan):
    plan = RepairPlan(*frozen_plan)
    plan.paths["audit"].write_text("{}")
    with pytest.raises(ValueError, match="inputs changed"):
        plan.verify_files()


def test_replayed_originals_must_match_every_candidate_value(frozen_plan):
    plan = RepairPlan(*frozen_plan)
    replay = deepcopy(plan.rows)
    replay[-1]["Cases"] = "42"
    with pytest.raises(ValueError, match="original replay differs"):
        compare_candidate(plan.rows, replay)
    replay = deepcopy(plan.rows)
    replay[-1] = replay[0]
    with pytest.raises(ValueError, match="1272 unique"):
        compare_candidate(plan.rows, replay)


def test_rejection_preserves_original_and_points_to_new_identity(frozen_plan):
    plan = RepairPlan(*frozen_plan)
    strategy = MexicoWeeklyPeriodRepair(plan)
    old = plan.old[0]
    original = deepcopy(old)
    target = {**old, "time": "2026-10-25T00:00:00+00:00", "dimension_key": "corrected"}
    rejected = strategy.rejection(old, plan.corrections[0], target)
    assert old == original and rejected["raw_data"] == original["raw_data"]
    assert rejected["quality_status"] == "rejected" and rejected["value"] == old["value"]
    evidence = rejected["metadata"]["period_correction"]
    assert evidence["superseded_by"]["time"] == "2026-10-25T00:00:00+00:00"
    assert evidence["superseded_by"]["dimension_key"] == "corrected"
    assert evidence["baseline_sha256"] == plan.hashes["baseline"]


def test_idempotent_check_rejects_concurrent_value_and_evidence_changes():
    active = [{"time": "2026-10-25T00:00:00+00:00", "series_code": f"SER_{i}",
               "geography_key": "country:MX:national", "dimension_key": "all", "value": i,
               "quality_status": "raw", "metadata": {}} for i in range(1272)]
    rejected = [{**active[i], "time": "2026-10-18T00:00:00+00:00", "quality_status": "rejected",
                 "metadata": {"period_correction": {"plan_id": "reviewed"}}} for i in range(9)]
    strategy = MexicoWeeklyPeriodRepair(SimpleNamespace())
    actual = deepcopy(active + rejected)
    strategy.verify_final(actual, active, rejected)
    actual[0]["value"] = 999
    with pytest.raises(ValueError, match="Stored observation differs"):
        strategy.verify_final(actual, active, rejected)
    actual = deepcopy(active + rejected)
    actual[-1]["metadata"] = {}
    with pytest.raises(ValueError, match="Stored observation differs"):
        strategy.verify_final(actual, active, rejected)
