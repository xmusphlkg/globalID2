from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.literature.health import (
    HealthThresholds,
    ResearchRadarSnapshot,
    collect_health_snapshot,
    evaluate_health,
    exit_code_for,
)


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
ALL_SOURCES = (
    "crossref",
    "europe-pmc",
    "openalex",
    "unpaywall",
    "who-iris-oai",
    "controlled-query",
)


def _healthy_source_counts() -> dict[str, int]:
    # Zero matches are a valid successful provider result. Presence of the
    # complete counter contract, rather than a positive record count, is the
    # execution evidence used by the health check.
    return {
        "crossref_fetched": 0,
        "source_records_seen": 0,
        "source_records_returned": 0,
        "europe_pmc_enriched": 0,
        "europe_pmc_errors": 0,
        "openalex_enriched": 0,
        "openalex_errors": 0,
        "unpaywall_enriched": 0,
        "unpaywall_errors": 0,
        "official_guidance_fetched": 0,
        "official_guidance_records_seen": 0,
        "official_guidance_errors": 0,
        "controlled_discovery_fetched": 0,
        "controlled_discovery_queries": 0,
        "controlled_discovery_query_errors": 0,
    }


def _article(article_id: str = "public-1") -> dict:
    return {
        "article_id": article_id,
        "slug": article_id,
        "title": "Public evidence",
        "doi": f"10.1000/{article_id}",
        "published_at": "2026-08-16T00:00:00+00:00",
        "peer_review_status": "peer_reviewed",
        "editorial_status": "published",
        "integrity_status": "current",
        "indexable": True,
        "classification_version": 5,
        "research_domain": "human_health",
        "summary": {
            "en": {"main_findings": "Evidence"},
            "zh": {"main_findings": "证据"},
        },
        "related_articles": [],
    }


def _release_payload() -> dict:
    article = _article()
    return {
        "last_updated": (NOW - timedelta(hours=1)).isoformat(),
        "articles": [article],
        "preprints": [],
        "integrity_alerts": [],
        "weekly_briefs": [{
            "week": "2026-W33",
            "end_date": "2026-08-16",
            "article_count": 1,
            "cited_findings": [{"provenance": "published_bilingual_structured_summary"}],
            "brief_status": "automatically_compiled_not_editorially_reviewed",
        }],
        "surveillance_evidence": {"signals": [], "evidence_gaps": []},
    }


def _healthy_snapshot() -> ResearchRadarSnapshot:
    source = "+".join(ALL_SOURCES)
    return ResearchRadarSnapshot(
        collected_at=NOW,
        ingest_runs=(
            {
                "source": source,
                "status": "completed",
                "started_at": NOW - timedelta(hours=1),
                "completed_at": NOW - timedelta(minutes=50),
                "through_indexed_at": NOW - timedelta(hours=1),
                "checkpoint": {
                    "strategy": "index-date",
                    "controlled_discovery": {"schema_version": 1},
                    "official_guidance": {"schema_version": 1},
                },
                "counts": _healthy_source_counts(),
            },
            {
                "source": "research-radar-autopilot",
                "status": "completed",
                "started_at": NOW - timedelta(minutes=45),
                "completed_at": NOW - timedelta(minutes=44),
                "through_indexed_at": NOW - timedelta(minutes=45),
                "checkpoint": {"policy_version": 1},
                "counts": {
                    "article_exceptions": 1,
                    "link_exceptions": 0,
                    "summary_exceptions": 2,
                },
            },
        ),
        tasks=tuple({
            "type": task_type,
            "status": "completed",
            "created_at": NOW - timedelta(hours=1),
            "started_at": NOW - timedelta(hours=1),
            "completed_at": NOW - timedelta(minutes=50),
            "updated_at": NOW - timedelta(minutes=50),
            "retry_count": 0,
        } for task_type in (
            "sync_literature", "enrich_literature", "discover_literature_gaps"
        )),
        articles=(
            {
                "doi": "10.1000/one",
                "openalex_id": "W1",
                "source_payload": {"unpaywall": {"is_oa": True}},
                "metadata_": {"classification_version": 5},
                "publication_status": "published",
                "integrity_status": "current",
                "peer_review_status": "peer_reviewed",
            },
        ),
        summaries=(),
        current_review_link_count=0,
        evidence_gaps=(),
        release_payload=_release_payload(),
        release_read_status="ok",
        backfill_checkpoint={
            "status": "completed",
            "updated_at": (NOW - timedelta(days=3)).isoformat(),
            "run_stats": {
                "failure_count": 0,
                "provider_stats": {
                    "openalex": {"failed": 0},
                    "unpaywall": {"failed": 0},
                },
            },
        },
        backfill_read_status="ok",
        expected_sources=ALL_SOURCES,
    )


def _checks(report: dict) -> dict[str, dict]:
    return {check["code"]: check for check in report["checks"]}


def test_healthy_snapshot_passes_all_slos() -> None:
    report = evaluate_health(_healthy_snapshot())

    assert report["status"] == "healthy"
    assert report["summary"] == {
        "check_count": 14,
        "passed": 14,
        "warnings": 0,
        "critical": 0,
    }
    assert exit_code_for(report) == 0


def test_enabled_sources_accept_explicit_zero_record_success_for_every_contract() -> None:
    expected_sources = (
        *ALL_SOURCES,
        "publisher-rss",
        "springer-nature",
        "elsevier",
        "biorxiv-api",
    )
    counts = {
        **_healthy_source_counts(),
        "publisher_rss_fetched": 0,
        "publisher_rss_records_seen": 0,
        "publisher_rss_feeds_modified": 0,
        "publisher_rss_feeds_not_modified": 1,
        "publisher_rss_feed_errors": 0,
        "springer_nature_fetched": 0,
        "springer_nature_errors": 0,
        "springer_nature_skipped_credentials": 0,
        "elsevier_fetched": 0,
        "elsevier_errors": 0,
        "elsevier_skipped_credentials": 0,
        "preprint_fetched": 0,
        "preprint_source_errors": 0,
    }
    core = {
        **_healthy_snapshot().ingest_runs[0],
        "source": "+".join(expected_sources),
        "counts": counts,
    }
    snapshot = replace(
        _healthy_snapshot(),
        ingest_runs=(core, _healthy_snapshot().ingest_runs[1]),
        expected_sources=expected_sources,
    )

    source_check = _checks(evaluate_health(snapshot))["enabled_source_success"]

    assert source_check["status"] == "pass"
    assert source_check["observed"]["successful_source_count"] == len(expected_sources)
    assert {
        result["reason"] for result in source_check["observed"]["source_results"]
    } == {"success"}


@pytest.mark.parametrize(
    ("source", "counter"),
    [
        ("europe-pmc", "europe_pmc_errors"),
        ("openalex", "openalex_errors"),
        ("unpaywall", "unpaywall_errors"),
        ("who-iris-oai", "official_guidance_errors"),
        ("controlled-query", "controlled_discovery_query_errors"),
    ],
)
def test_enabled_source_error_counter_cannot_be_masked_by_completed_run(
    source: str,
    counter: str,
) -> None:
    snapshot = _healthy_snapshot()
    core = dict(snapshot.ingest_runs[0])
    core["counts"] = {**core["counts"], counter: 1}

    source_check = _checks(evaluate_health(replace(
        snapshot,
        ingest_runs=(core, snapshot.ingest_runs[1]),
    )))["enabled_source_success"]
    result = next(
        item for item in source_check["observed"]["source_results"]
        if item["source"] == source
    )

    assert source_check["status"] == "warning"
    assert source_check["observed"]["provider_error_source_count"] == 1
    assert result == {
        "source": source,
        "status": "failed",
        "reason": "provider_errors",
        "error_count": 1,
        "skipped_credentials_count": 0,
    }


@pytest.mark.parametrize(
    ("source", "prefix"),
    [("springer-nature", "springer_nature"), ("elsevier", "elsevier")],
)
def test_enabled_credential_source_skip_cannot_report_success(
    source: str,
    prefix: str,
) -> None:
    snapshot = _healthy_snapshot()
    counts = {
        **snapshot.ingest_runs[0]["counts"],
        f"{prefix}_fetched": 0,
        f"{prefix}_errors": 0,
        f"{prefix}_skipped_credentials": 1,
    }
    core = {
        **snapshot.ingest_runs[0],
        "source": f"{snapshot.ingest_runs[0]['source']}+{source}",
        "counts": counts,
    }

    source_check = _checks(evaluate_health(replace(
        snapshot,
        ingest_runs=(core, snapshot.ingest_runs[1]),
        expected_sources=(*snapshot.expected_sources, source),
    )))["enabled_source_success"]
    result = source_check["observed"]["source_results"][-1]

    assert source_check["status"] == "warning"
    assert source_check["observed"]["credential_skipped_source_count"] == 1
    assert result["reason"] == "skipped_credentials"
    assert result["status"] == "failed"


def test_enabled_source_fails_closed_when_latest_run_lacks_count_contract() -> None:
    snapshot = _healthy_snapshot()
    core = dict(snapshot.ingest_runs[0])
    core["counts"] = {**core["counts"]}
    core["counts"].pop("official_guidance_errors")

    source_check = _checks(evaluate_health(replace(
        snapshot,
        ingest_runs=(core, snapshot.ingest_runs[1]),
    )))["enabled_source_success"]
    who = next(
        item for item in source_check["observed"]["source_results"]
        if item["source"] == "who-iris-oai"
    )

    assert source_check["status"] == "warning"
    assert source_check["observed"]["count_contract_failure_source_count"] == 1
    assert who["reason"] == "missing_count_contract"


def test_failed_sync_is_not_masked_by_newer_autopilot_and_output_is_redacted() -> None:
    snapshot = _healthy_snapshot()
    failed = {
        "source": "+".join(ALL_SOURCES),
        "status": "failed",
        "started_at": NOW - timedelta(minutes=20),
        "completed_at": NOW - timedelta(minutes=19),
        "through_indexed_at": NOW - timedelta(minutes=20),
        "checkpoint": {"strategy": "index-date"},
        "counts": {},
        "error": "postgres://user:secret@example.invalid/article/private-id",
    }
    snapshot = replace(
        snapshot,
        ingest_runs=(snapshot.ingest_runs[1], failed, snapshot.ingest_runs[0]),
        articles=({
            **snapshot.articles[0],
            "source_payload": {
                "unpaywall": {"is_oa": True},
                "private_token": "do-not-serialize",
            },
        },),
    )

    report = evaluate_health(snapshot)
    serialized = json.dumps(report)
    failure = _checks(report)["sync_failures_and_recovery"]

    assert report["status"] == "unhealthy"
    assert failure["status"] == "critical"
    assert failure["observed"]["latest_terminal_status"] == "failed"
    assert exit_code_for(report) == 2
    assert "secret" not in serialized
    assert "private-id" not in serialized
    assert "do-not-serialize" not in serialized


def test_recovered_failure_is_visible_as_warning_with_configurable_ci_exit() -> None:
    snapshot = _healthy_snapshot()
    failed = {
        "source": "+".join(ALL_SOURCES),
        "status": "failed",
        "started_at": NOW - timedelta(hours=2),
        "completed_at": NOW - timedelta(hours=2),
        "through_indexed_at": NOW - timedelta(hours=2),
        "checkpoint": {"strategy": "index-date"},
        "counts": {},
    }
    snapshot = replace(snapshot, ingest_runs=(snapshot.ingest_runs[0], failed, snapshot.ingest_runs[1]))

    report = evaluate_health(snapshot)

    assert report["status"] == "degraded"
    assert _checks(report)["sync_failures_and_recovery"]["observed"]["recovered_failures"] == 1
    assert exit_code_for(report, fail_on="warning") == 1
    assert exit_code_for(report, fail_on="critical") == 0


def test_stale_running_ingest_exposes_safe_reconciliation_dry_run_action() -> None:
    snapshot = _healthy_snapshot()
    stale = {
        "source": "+".join(ALL_SOURCES),
        "status": "running",
        "started_at": NOW - timedelta(hours=3),
        "completed_at": None,
        "through_indexed_at": NOW - timedelta(hours=3),
        "checkpoint": {"strategy": "index-date"},
        "counts": {},
    }

    failure = _checks(evaluate_health(replace(
        snapshot, ingest_runs=(stale, *snapshot.ingest_runs)
    )))["sync_failures_and_recovery"]

    assert failure["status"] == "critical"
    assert failure["observed"]["stale_running_runs"] == 1
    assert failure["next_action_code"] == "reconcile_stale_ingest_runs_dry_run"


def test_truncated_checkpoint_exposes_catch_up_capacity_without_weakening_freshness_slo() -> None:
    snapshot = _healthy_snapshot()
    core = dict(snapshot.ingest_runs[0])
    core["checkpoint"] = {
        **core["checkpoint"],
        "truncated": True,
        "next_from_indexed_at": (NOW - timedelta(hours=1)).isoformat(),
        "catch_up_required": True,
        "remaining_index_span_seconds": 3_600,
        "records_prefetched": 330,
        "records_returned": 300,
        "lookahead_records": 30,
        "pages_fetched": 51,
        "fetch_efficiency_ratio": round(300 / 330, 6),
    }
    snapshot = replace(snapshot, ingest_runs=(core, snapshot.ingest_runs[1]))
    snapshot = replace(
        snapshot,
        tasks=tuple(
            {
                **task,
                **({
                    "catch_up": {
                        "catch_up_required": 1,
                        "catch_up_status": "scheduled",
                        "catch_up_next_run_at": (NOW + timedelta(minutes=5)).isoformat(),
                        "catch_up_backlog_observed_count": 100,
                        "catch_up_backlog_projected_upper_bound": 400,
                        "catch_up_backlog_limit": 500,
                        "catch_up_resume_below_backlog": 200,
                    },
                } if task["type"] == "sync_literature" else {}),
            }
            for task in snapshot.tasks
        ),
    )

    report = evaluate_health(snapshot)
    checkpoint = _checks(report)["sync_checkpoint"]

    assert report["status"] == "healthy"
    assert checkpoint["status"] == "pass"
    assert checkpoint["observed"] == {
        "present": True,
        "strategy_present": True,
        "truncated": True,
        "truncated_checkpoint_resumable": True,
        "catch_up_required": True,
        "remaining_index_span_seconds": 3_600,
        "records_prefetched": 330,
        "records_returned": 300,
        "lookahead_records": 30,
        "pages_fetched": 51,
        "fetch_efficiency_ratio": round(300 / 330, 6),
        "nested_checkpoint_count": 2,
    }
    catch_up = _checks(report)["catch_up_orchestration"]
    assert catch_up["status"] == "pass"
    assert catch_up["next_action_code"] == "await_accelerated_catch_up"
    assert catch_up["observed"]["resume_below_backlog"] == 200


def test_catch_up_backpressure_reports_exact_resume_action() -> None:
    snapshot = _healthy_snapshot()
    core = dict(snapshot.ingest_runs[0])
    core["checkpoint"] = {
        **core["checkpoint"],
        "truncated": True,
        "next_from_indexed_at": (NOW - timedelta(hours=1)).isoformat(),
        "catch_up_required": True,
    }
    tasks = tuple(
        {
            **task,
            **({
                "catch_up": {
                    "catch_up_required": 1,
                    "catch_up_status": "paused_backpressure",
                    "catch_up_backlog_observed_count": 2404,
                    "catch_up_backlog_projected_upper_bound": 2704,
                    "catch_up_backlog_limit": 2500,
                    "catch_up_resume_below_backlog": 2200,
                    "catch_up_required_backlog_reduction": 205,
                    "catch_up_backpressure_reason": (
                        "exception_backlog_headroom_exhausted"
                    ),
                },
            } if task["type"] == "sync_literature" else {}),
        }
        for task in snapshot.tasks
    )
    report = evaluate_health(
        replace(snapshot, ingest_runs=(core, snapshot.ingest_runs[1]), tasks=tasks)
    )

    catch_up = _checks(report)["catch_up_orchestration"]
    assert catch_up["status"] == "warning"
    assert catch_up["next_action_code"] == (
        "reduce_exception_backlog_below_resume_threshold"
    )
    assert catch_up["observed"]["resume_below_backlog"] == 2200
    assert catch_up["observed"]["required_backlog_reduction"] == 205


def test_release_classification_and_provider_regressions_fail_closed() -> None:
    snapshot = _healthy_snapshot()
    payload = _release_payload()
    payload["articles"][0]["summary"] = {"en": {"main_findings": "Only English"}}
    snapshot = replace(
        snapshot,
        release_payload=payload,
        articles=({
            **snapshot.articles[0],
            "openalex_id": None,
            "source_payload": {},
            "metadata_": {"classification_version": 4},
        },),
    )

    report = evaluate_health(snapshot)
    checks = _checks(report)

    assert checks["classification_version"]["status"] == "critical"
    assert checks["metadata_provider_coverage"]["status"] == "critical"
    assert checks["public_bilingual_gate"]["status"] == "critical"
    assert checks["release_validator"]["observed"]["blocker_categories"] == {
        "bilingual_gate": 1,
    }


def test_weekly_digest_accepts_only_complete_public_human_review_evidence() -> None:
    payload = _release_payload()
    payload["weekly_briefs"][0].update({
        "brief_status": "editorially_reviewed",
        "byline": {"reviewer": {
            "name": "Dr Jane Q. Public",
            "role": "Infectious disease editor",
            "reviewed_at": "2026-08-16T10:00:00+00:00",
            "institution": "Example School of Public Health",
        }},
    })
    report = evaluate_health(replace(_healthy_snapshot(), release_payload=payload))
    digest = _checks(report)["weekly_digest"]

    assert digest["status"] == "pass"
    assert digest["observed"]["human_reviewed"] is True
    assert digest["observed"]["review_evidence_valid"] is True

    payload["weekly_briefs"][0]["byline"]["reviewer"].pop("reviewed_at")
    invalid = evaluate_health(replace(_healthy_snapshot(), release_payload=payload))
    assert _checks(invalid)["weekly_digest"]["status"] == "critical"


def test_stalled_backfill_and_exception_backlog_use_overridable_thresholds() -> None:
    snapshot = _healthy_snapshot()
    snapshot = replace(
        snapshot,
        backfill_checkpoint={
            "status": "running",
            "updated_at": (NOW - timedelta(hours=25)).isoformat(),
            "run_stats": {"failure_count": 0, "provider_stats": {}},
        },
        articles=({**snapshot.articles[0], "publication_status": "review"},),
        summaries=({"status": "review"},),
        current_review_link_count=1,
    )
    thresholds = HealthThresholds(max_exception_backlog=2)

    report = evaluate_health(snapshot, thresholds)
    checks = _checks(report)

    assert checks["metadata_backfill_checkpoint"]["status"] == "critical"
    assert checks["metadata_backfill_checkpoint"]["next_action_code"] == "resume_stalled_metadata_backfill"
    assert checks["exception_backlog"]["status"] == "critical"
    assert checks["exception_backlog"]["next_action_code"] == "run_literature_autopilot_dry_run"
    assert checks["exception_backlog"]["observed"]["combined_exception_backlog"] == 3


def test_exception_backlog_thresholds_current_objects_without_snapshot_double_count() -> None:
    snapshot = _healthy_snapshot()
    autopilot = {
        **snapshot.ingest_runs[1],
        "counts": {
            "article_exceptions": 2,
            "link_exceptions": 1,
            "summary_exceptions": 1,
        },
    }
    snapshot = replace(
        snapshot,
        ingest_runs=(snapshot.ingest_runs[0], autopilot),
        articles=(
            {**snapshot.articles[0], "publication_status": "review"},
            {**snapshot.articles[0], "publication_status": "review"},
        ),
        summaries=({"status": "review"},),
        current_review_link_count=1,
    )

    report = evaluate_health(snapshot, HealthThresholds(max_exception_backlog=4))
    backlog = _checks(report)["exception_backlog"]

    assert backlog["status"] == "pass"
    assert backlog["observed"] == {
        "autopilot_snapshot_present": True,
        "automation_exception_count": 4,
        "review_article_count": 2,
        "raw_latest_autopilot_article_exception_count": 2,
        "raw_latest_autopilot_link_exception_count": 1,
        "raw_latest_autopilot_summary_exception_count": 1,
        "raw_latest_autopilot_article_deferred_count": 0,
        "raw_latest_autopilot_summary_deferred_count": 0,
        "raw_latest_autopilot_summary_archived_count": 0,
        "raw_legacy_combined_exception_backlog": 6,
        "raw_review_article_count": 2,
        "raw_review_summary_count": 1,
        "deferred_review_article_count": 0,
        "deferred_review_summary_count": 0,
        "deferred_review_object_count": 0,
        "archived_decision_review_article_count": 0,
        "archived_decision_review_summary_count": 0,
        "archived_decision_review_object_count": 0,
        "archived_summary_count": 0,
        "current_review_article_count": 2,
        "current_review_link_count": 1,
        "current_review_summary_count": 1,
        "backlog_counting_basis": "current_actionable_review_objects",
        "uniqueish_exception_backlog": 4,
        "combined_exception_backlog": 4,
        "open_evidence_gap_count": 0,
        "active_evidence_gap_error_count": 0,
        "retained_evidence_gap_error_count": 0,
    }


def test_exception_backlog_excludes_only_explicit_deferred_and_archived_state() -> None:
    snapshot = _healthy_snapshot()
    autopilot = {
        **snapshot.ingest_runs[1],
        "counts": {
            "article_exceptions": 2,
            "link_exceptions": 1,
            "summary_exceptions": 1,
            "articles_deferred": 1,
            "summaries_deferred": 1,
            "summaries_archived": 1,
        },
    }
    review_article = {**snapshot.articles[0], "publication_status": "review"}
    snapshot = replace(
        snapshot,
        ingest_runs=(snapshot.ingest_runs[0], autopilot),
        articles=(
            {
                **review_article,
                "metadata_": {"autopilot": {"decision": "defer"}},
            },
            {
                **review_article,
                "metadata_": {"autopilot": {"decision": "DEFER"}},
            },
            {
                **review_article,
                "metadata_": {"autopilot": {"decision": "archive"}},
            },
            {**review_article, "metadata_": {"autopilot": "malformed"}},
        ),
        summaries=(
            {
                "status": "review",
                "generation_metadata": {"autopilot": {"decision": "defer"}},
            },
            {
                "status": "review",
                "generation_metadata": {"autopilot": {"decision": "archive"}},
            },
            {"status": "review", "generation_metadata": {}},
            {
                "status": "archived",
                "generation_metadata": {"autopilot": {"decision": "archive"}},
            },
        ),
        current_review_link_count=1,
    )

    report = evaluate_health(snapshot, HealthThresholds(max_exception_backlog=4))
    backlog = _checks(report)["exception_backlog"]
    observed = backlog["observed"]

    assert backlog["status"] == "pass"
    assert observed["raw_review_article_count"] == 4
    assert observed["raw_review_summary_count"] == 3
    assert observed["deferred_review_article_count"] == 1
    assert observed["deferred_review_summary_count"] == 1
    assert observed["deferred_review_object_count"] == 2
    assert observed["archived_decision_review_article_count"] == 1
    assert observed["archived_decision_review_summary_count"] == 1
    assert observed["archived_decision_review_object_count"] == 2
    assert observed["archived_summary_count"] == 1
    assert observed["current_review_article_count"] == 2
    assert observed["current_review_link_count"] == 1
    assert observed["current_review_summary_count"] == 1
    assert observed["uniqueish_exception_backlog"] == 4
    assert observed["raw_legacy_combined_exception_backlog"] == 8
    assert observed["raw_latest_autopilot_article_deferred_count"] == 1
    assert observed["raw_latest_autopilot_summary_deferred_count"] == 1
    assert observed["raw_latest_autopilot_summary_archived_count"] == 1


def test_threshold_mapping_rejects_typos_and_invalid_ratios() -> None:
    with pytest.raises(ValueError, match="unknown health threshold"):
        HealthThresholds.from_mapping({"max_syn_age_hours": 1})
    with pytest.raises(ValueError, match="between zero and one"):
        HealthThresholds(min_openalex_coverage=1.1)


def test_every_health_check_exposes_a_stable_next_action_code() -> None:
    report = evaluate_health(_healthy_snapshot())

    assert all(isinstance(check["next_action_code"], str) for check in report["checks"])
    assert {check["next_action_code"] for check in report["checks"]} == {"none"}


def test_completed_enrichment_with_generation_failures_is_visible_warning() -> None:
    snapshot = _healthy_snapshot()
    tasks = tuple(
        {
            **task,
            **(
                {"enrichment": {"articles": 8, "generated": 0, "skipped": 8, "failed": 8}}
                if task["type"] == "enrich_literature"
                else {}
            ),
        }
        for task in snapshot.tasks
    )

    report = evaluate_health(replace(snapshot, tasks=tasks))
    background = _checks(report)["background_tasks"]

    assert background["status"] == "warning"
    assert background["next_action_code"] == "inspect_enrichment_generation_failures"
    assert background["observed"]["latest_enrichment_failed_summaries"] == 8
    assert background["observed"]["completed_with_enrichment_failures"] == 1


def test_cli_stdout_is_exactly_one_json_document_without_log_noise(tmp_path) -> None:
    invalid_thresholds = tmp_path / "invalid-thresholds.json"
    invalid_thresholds.write_text('{"unknown_slo": 1}', encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_research_radar_health.py"),
            "--thresholds",
            str(invalid_thresholds),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 3
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "schema_version": 1,
        "service": "research-radar",
        "status": "check_error",
        "error_code": "health_check_failed",
    }


@pytest.mark.asyncio
async def test_collector_issues_selects_only(tmp_path) -> None:
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    from src.domain import (
        Base,
        LiteratureArticle,
        LiteratureEvidenceGap,
        LiteratureIngestRun,
        LiteratureSignalArticleLink,
        LiteratureSummary,
        StandardDisease,
        Task,
    )

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        StandardDisease.__table__,
        Task.__table__,
        LiteratureArticle.__table__,
        LiteratureSummary.__table__,
        LiteratureIngestRun.__table__,
        LiteratureEvidenceGap.__table__,
        LiteratureSignalArticleLink.__table__,
    ])
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.strip().upper())

    class AsyncSessionAdapter:
        def __init__(self, session):
            self.session = session

        async def execute(self, statement):
            return self.session.execute(statement)

    release_path = tmp_path / "release.json"
    checkpoint_path = tmp_path / "checkpoint.json"
    release_path.write_text(json.dumps(_release_payload()), encoding="utf-8")
    checkpoint_path.write_text(json.dumps({
        "status": "completed",
        "updated_at": NOW.isoformat(),
        "run_stats": {},
    }), encoding="utf-8")
    with Session(engine) as session:
        snapshot = await collect_health_snapshot(
            AsyncSessionAdapter(session),
            release_path=release_path,
            backfill_checkpoint_path=checkpoint_path,
            now=NOW,
        )

    assert snapshot.release_read_status == "ok"
    assert snapshot.backfill_read_status == "ok"
    assert snapshot.current_review_link_count == 0
    assert len(statements) == 7
    assert all(statement.startswith("SELECT") for statement in statements)
    assert snapshot.article_metrics == {
        "article_count": 0,
        "classification_current_count": 0,
        "doi_article_count": 0,
        "openalex_count": 0,
        "unpaywall_count": 0,
    }
