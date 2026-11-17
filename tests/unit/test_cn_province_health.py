from dataclasses import asdict
from datetime import date
import json
from pathlib import Path

import pytest

from src.data.crawlers.cn_province_adapters.base import ProvinceSourceConfig
from src.services.cn_province_health import _digest, build_health_report, render_health_markdown


CONFIG = ProvinceSourceConfig("CN-GS", "Gansu, China", "甘肃省", "620000", "https://official.test/list", "table_or_attachment")
REGISTRY = {CONFIG.code: CONFIG}


def row(disease="influenza", cases=10, deaths=None, *, month="2026-10", narrative=False):
    item = {"Date": month + "-01", "SourceDiseaseCode": disease, "Cases": cases,
            "JurisdictionCode": "CN-GS", "GeographyKey": "country:CN-GS:national",
            "SourceID": "SRC_CN_PROV_MONTHLY_REPORT", "Raw": {}}
    if deaths is not None:
        item["Deaths"] = deaths
    if narrative:
        item["Raw"]["explicit_narrative_value"] = "明确病例原文"
    return item


def report(month, rows=(), *, evidence="table", status="parsed", complete=False, diagnostics=()):
    return {"date": month + "-01", "url": f"https://official.test/{month}", "status": status,
            "evidence_type": evidence, "rows": list(rows), "source_table_complete": complete,
            "diagnostics": list(diagnostics)}


def save_batch(tmp_path, reports, **metadata):
    path = tmp_path / "provinces" / "CN-GS.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"province": "CN-GS", "reports": reports, "errors": [],
                               "started_at": "2026-11-16T00:00:00Z", "finished_at": "2026-11-16T00:01:00Z", **metadata}))
    return tmp_path


def assess(path, **kwargs):
    return build_health_report(path, as_of=date(2026, 11, 16), registry=REGISTRY, **kwargs)["provinces"][0]


def save_review(path, *, approved=True, validated=True):
    folder = path / "image_review"
    folder.mkdir(exist_ok=True)
    review = {"province_code": "CN-GS", "report_month": "2026-10",
              "status": "approved_for_import" if approved else "ready_for_root_review_not_imported",
              "validation": {"all_checks_passed": validated}, "coverage": {"uncertain_fields": 0},
              "official_report_url": "https://official.test/2026-10",
              "source_images": [{"url": "https://official.test/october.png"}],
              "rows": [row("influenza", 10, 0), row("tuberculosis", 2, 1)]}
    (folder / "CN-GS_202610.json").write_text(json.dumps(review))


def test_sparse_latest_does_not_masquerade_as_complete_or_tabular(tmp_path):
    save_batch(tmp_path, [
        report("2026-08", [row("influenza", month="2026-08"), row("tuberculosis", month="2026-08")], complete=True),
        report("2026-09", status="failed"),
        report("2026-10", [row("influenza", narrative=True)], evidence="narrative_sparse"),
    ])
    health = assess(tmp_path)
    assert health["latest_discovered_month"] == health["latest_parsed_month"] == "2026-10"
    assert health["latest_table_month"] == health["latest_complete_table_month"] == "2026-08"
    assert health["unresolved_failed_months"] == ["2026-09"]
    latest = health["months"][-1]
    assert latest["cases_disease_codes"] == ["influenza"]
    assert latest["deaths_disease_count"] == 0
    assert latest["narrative_only"]
    decrease = health["coverage_decreases"][0]
    assert decrease["missing_previously_reported_diseases"] == ["tuberculosis"]
    assert decrease["action"] == "review_source_scope_do_not_fill_zero"


def test_table_marker_alone_never_attests_completeness(tmp_path):
    save_batch(tmp_path, [report("2026-10", [row()])])
    health = assess(tmp_path)
    assert health["latest_table_month"] == "2026-10"
    assert health["latest_complete_table_month"] is None
    assert health["months"][0]["table_completeness_status"] == "table_coverage_unverified"


def test_death_only_report_does_not_advance_case_freshness_or_fill_case_gap(tmp_path):
    death_only = row("aids__deaths", cases=6)
    death_only["Measure"] = "deaths"
    death_only["Raw"]["explicit_narrative_value"] = "艾滋病死亡6例"
    save_batch(tmp_path, [report("2026-10", [death_only], evidence="narrative_sparse")])
    health = assess(tmp_path)
    assert health["latest_parsed_month"] == "2026-10"
    assert health["latest_cases_month"] is None
    assert health["latest_deaths_month"] == "2026-10"
    assert health["freshness"]["closed_month_lag_cases"] is None
    assert "2026-10" in health["month_gaps"]["no_case_values"]
    assert "2026-10" not in health["month_gaps"]["no_death_values"]
    assert health["latest_complete_table_month"] is None
    assert any(a["action"] == "investigate_no_case_detail" for a in health["next_action"])


def test_newer_death_only_month_flags_case_detail_lag(tmp_path):
    old = row(month="2026-07")
    death_only = row("aids__deaths", cases=2)
    death_only["Measure"] = "deaths"
    save_batch(tmp_path, [report("2026-07", [old]), report("2026-10", [death_only], evidence="narrative_sparse")])
    health = assess(tmp_path)
    assert health["latest_cases_month"] == "2026-07"
    assert health["latest_deaths_month"] == "2026-10"
    assert health["freshness"]["cases_stale"] is True
    assert health["freshness"]["parsed_stale"] is False
    assert any(a["action"] == "investigate_case_detail_lag" for a in health["next_action"])


def test_review_supplements_only_approved_month_and_exact_image(tmp_path):
    diagnostic = lambda url: {"status": "report_image_unparsed", "url": url}
    save_batch(tmp_path, [
        report("2026-09", [row(month="2026-09", narrative=True)], evidence="narrative_sparse", diagnostics=[diagnostic("https://official.test/september.png")]),
        report("2026-10", [row(narrative=True)], evidence="narrative_sparse", diagnostics=[diagnostic("https://official.test/october.png")]),
    ])
    save_review(tmp_path)
    health = assess(tmp_path)
    assert health["latest_complete_table_month"] == "2026-10"
    assert health["approved_image_review_months"] == ["2026-10"]
    assert health["unresolved_image_months"] == ["2026-09"]
    assert health["source_profile"]["automatic_image_parsing"] is True
    assert health["months"][-1]["cases_disease_count"] == 2
    assert health["months"][-1]["deaths_disease_count"] == 2
    assert health["months"][-1]["unparsed_images"][0]["automatic_parser_supported"] is True


@pytest.mark.parametrize("approved,validated", [(False, True), (True, False)])
def test_pending_or_invalid_review_cannot_improve_coverage(tmp_path, approved, validated):
    save_batch(tmp_path, [report("2026-10", status="failed")])
    save_review(tmp_path, approved=approved, validated=validated)
    health = assess(tmp_path)
    assert health["latest_parsed_month"] is None
    assert health["latest_complete_table_month"] is None
    assert health["approved_image_review_months"] == []


def test_conflicting_counts_are_flagged_without_selecting_a_value(tmp_path):
    save_batch(tmp_path, [report("2026-10", [row(cases=10)], complete=True), report("2026-10", [row(cases=20)])])
    health = assess(tmp_path)
    assert health["months"][0]["conflicting_values"] == [{"metric": "cases", "disease_code": "influenza", "values": [10, 20]}]
    assert health["months"][0]["cases_disease_count"] == 0
    assert health["latest_complete_table_month"] is None
    assert any(a["action"] == "resolve_conflicting_observations" for a in health["next_action"])


def test_foreign_province_row_does_not_count_as_parsed(tmp_path):
    foreign = {**row(), "JurisdictionCode": "CN-SN", "GeographyKey": "country:CN-SN:national"}
    save_batch(tmp_path, [report("2026-10", [foreign], complete=True)])
    health = assess(tmp_path)
    assert health["latest_parsed_month"] is None
    assert health["months"][0]["failed_reports"][0]["row_errors"] == ["row_scope_mismatch"]


def test_summary_only_report_is_source_detail_gap_not_generic_parse_retry(tmp_path):
    diagnostic = {"status": "summary_only", "url": "https://official.test/2026-10", "report_month": "2026-10"}
    save_batch(tmp_path, [report("2026-10", status="failed", diagnostics=[diagnostic])])
    health = assess(tmp_path)
    assert health["summary_only_months"] == ["2026-10"]
    assert health["no_disease_value_months"] == ["2026-10"]
    assert health["unresolved_failed_months"] == []
    assert health["months"][0]["reports"][0]["source_value_diagnostic"] == diagnostic
    assert any(a["action"] == "find_detail_for_summary_reports" for a in health["next_action"])
    assert not any(a["action"] == "retry_or_adapt_failed_reports" for a in health["next_action"])


def test_old_discovery_without_any_values_still_prompts_column_check(tmp_path):
    save_batch(tmp_path, [report("2025-05", status="failed", diagnostics=[
        {"status": "summary_only", "url": "https://official.test/2025-05", "report_month": "2025-05"},
    ])])
    health = assess(tmp_path)
    assert health["latest_discovered_month"] == "2025-05"
    assert health["latest_parsed_month"] is None
    action = next(a for a in health["next_action"]
                  if a["action"] == "refresh_discovery_and_compare_official_release")
    assert action["latest_discovered_month"] == "2025-05"
    assert action["parsed_month_lag"] is None


def test_missing_fingerprint_is_unknown_and_changed_capture_is_visible(tmp_path):
    save_batch(tmp_path, [report("2026-10", [row()])])
    assert assess(tmp_path)["fingerprints"]["source_configuration"]["status"] == "unknown"
    save_batch(tmp_path, [report("2026-10", [row()])], source_profile_sha256=_digest(asdict(CONFIG)))
    assert assess(tmp_path)["fingerprints"]["source_configuration"]["status"] == "unchanged"
    save_batch(tmp_path, [report("2026-10", [row()])], source_profile_sha256="old-configuration")
    health = assess(tmp_path)
    assert health["fingerprints"]["source_configuration"]["status"] == "changed"
    assert health["fingerprints"]["parser"]["status"] == "unknown"


def test_shared_disease_alias_change_invalidates_parser_fingerprint(tmp_path):
    from src.services.cn_province_health import parser_fingerprint
    config = tmp_path / "configs/cn_province_sources.json"
    config.parent.mkdir()
    config.write_text('{"aliases":["埃博拉出血热"]}')
    before = parser_fingerprint(tmp_path)
    config.write_text('{"aliases":["埃博拉病毒病"]}')
    assert parser_fingerprint(tmp_path) != before


def test_freshness_budget_counts_closed_months_and_is_configurable(tmp_path):
    save_batch(tmp_path, [report("2026-07", [row(month="2026-07")])])
    stale = assess(tmp_path)
    assert stale["freshness"]["closed_month_lag_parsed"] == 3
    assert stale["freshness"]["parsed_stale"] is True
    assert assess(tmp_path, stale_months=4)["freshness"]["parsed_stale"] is False
    assert stale["freshness"]["budget_is_official_commitment"] is False


def test_month_gaps_cross_years_and_ignore_open_month_values(tmp_path):
    save_batch(tmp_path, [report("2025-12", [row(month="2025-12")]), report("2026-02", [row(month="2026-02")]), report("2026-03", [row(month="2026-03")])])
    health = build_health_report(tmp_path, as_of=date(2026, 3, 16), window_months=3, registry=REGISTRY)["provinces"][0]
    assert health["month_gaps"]["not_seen_in_selected_links"] == ["2026-01"]
    assert health["open_or_future_report_months"] == ["2026-03"]
    assert health["latest_parsed_month"] == "2026-02"


def test_fixed_date_report_is_reproducible_and_does_not_change_inputs(tmp_path):
    save_batch(tmp_path, [report("2026-10", [row(deaths=0)])])
    before = (tmp_path / "provinces/CN-GS.json").read_bytes()
    first = build_health_report(tmp_path, as_of=date(2026, 11, 16), registry=REGISTRY)
    second = build_health_report(tmp_path, as_of=date(2026, 11, 16), registry=REGISTRY)
    assert first == second
    assert (tmp_path / "provinces/CN-GS.json").read_bytes() == before
    text = render_health_markdown(first)
    assert "不是官方发布承诺" in text
    assert "table_coverage_unverified" in text


def test_corrupt_province_artifact_is_reported_without_losing_other_scopes(tmp_path):
    (tmp_path / "provinces").mkdir()
    (tmp_path / "provinces/CN-GS.json").write_text("not json")
    health = assess(tmp_path)
    assert health["latest_parsed_month"] is None
    assert any(a["action"] == "review_artifact_integrity" for a in health["next_action"])


def test_cli_outputs_31_provinces_offline(tmp_path, monkeypatch):
    from scripts.check_cn_province_health import main
    save_batch(tmp_path, [report("2026-10", [row()])])
    monkeypatch.setenv("DATABASE_URL", "invalid://must-not-be-used")
    output = tmp_path / "review-output"
    assert main(["--input", str(tmp_path), "--output-dir", str(output), "--as-of", "2026-11-16"]) == 0
    result = json.loads((output / "cn_province_health.json").read_text())
    assert result["summary"]["province_count"] == 31
    assert (output / "cn_province_health.md").is_file()
    assert result["public_release"] is False


@pytest.mark.parametrize("option", ["stale_months", "window_months", "reference_months"])
def test_invalid_month_budgets_fail_explicitly(tmp_path, option):
    save_batch(tmp_path, [])
    with pytest.raises(ValueError, match="positive"):
        assess(tmp_path, **{option: 0})
