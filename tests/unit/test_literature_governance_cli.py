from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import pytest

from scripts import backfill_literature_metadata
from scripts import govern_literature_backlog
from scripts import run_literature_autopilot
from src.literature.governance import governance_plan
from src.services.literature_automation_service import _effective_config


def test_metadata_backfill_cli_is_bounded_and_read_only_by_default() -> None:
    args = backfill_literature_metadata._parser().parse_args([])

    assert args.apply is False
    assert args.limit == 500
    assert args.openalex_target is None
    assert args.unpaywall_target is None


def test_metadata_backfill_apply_lock_rejects_concurrent_writer(tmp_path) -> None:
    lock_path = tmp_path / "backfill.lock"

    with backfill_literature_metadata._exclusive_apply_lock(lock_path):
        with pytest.raises(backfill_literature_metadata.ConcurrentApplyError, match="already running"):
            with backfill_literature_metadata._exclusive_apply_lock(lock_path):
                pass


def test_autopilot_cli_requires_explicit_apply(monkeypatch, capsys) -> None:
    calls: list[dict[str, bool]] = []

    async def fake_run(*, dry_run: bool, export: bool) -> dict:
        calls.append({"dry_run": dry_run, "export": export})
        return {"dry_run": dry_run}

    monkeypatch.setattr(run_literature_autopilot, "run", fake_run)

    assert run_literature_autopilot.main([]) == 0
    assert calls == [{"dry_run": True, "export": True}]
    assert json.loads(capsys.readouterr().out) == {"dry_run": True}

    assert run_literature_autopilot.main(["--apply", "--no-export"]) == 0
    assert calls[-1] == {"dry_run": False, "export": False}


def _governance_preview(**updates):
    value = {
        "enabled": True,
        "policy_version": "research-radar-autopilot.v1",
        "effective_article_min_score": 0.60,
        "articles_published": 1769,
        "articles_excluded": 0,
        "articles_deferred": 76,
        "article_exceptions": 179,
        "links_confirmed": 0,
        "links_rejected": 0,
        "link_exceptions": 0,
        "summaries_published": 66,
        "summaries_archived": 0,
        "summaries_deferred": 62,
        "summary_exceptions": 260,
        "changed": 1835,
        "published_article_ids": ["must-not-leak"],
    }
    value.update(updates)
    return value


def test_governance_plan_is_stable_and_thresholded() -> None:
    first = governance_plan(_governance_preview(), max_projected_backlog=500)
    second = governance_plan(_governance_preview(), max_projected_backlog=500)

    assert first == second
    assert first["projected_actionable_backlog"] == 439
    assert first["within_backlog_guard"] is True
    assert len(first["plan_sha256"]) == 64

    over = governance_plan(
        _governance_preview(summary_exceptions=400), max_projected_backlog=500
    )
    assert over["within_backlog_guard"] is False
    assert over["plan_sha256"] != first["plan_sha256"]


def test_policy_override_cannot_weaken_the_exclusion_boundary() -> None:
    config = SimpleNamespace(
        autopilot_article_exclude_below_score=0.60,
        copy=lambda **kwargs: None,
    )

    with pytest.raises(ValueError, match="cannot be below"):
        _effective_config(config, {"autopilot_article_min_score": 0.59})
    with pytest.raises(ValueError, match="unsupported"):
        _effective_config(config, {"autopilot_summary_min_quality": 0.70})


def test_governance_cli_is_read_only_by_default_and_requires_exact_plan_hash(
    monkeypatch, capsys
) -> None:
    calls = []

    async def fake_audit():
        return {"actionable": {"total": 2152}}

    async def fake_reconcile(**kwargs):
        calls.append(kwargs)
        return _governance_preview()

    monkeypatch.setattr(govern_literature_backlog, "audit_current_backlog", fake_audit)
    monkeypatch.setattr(
        govern_literature_backlog.literature_automation_service,
        "reconcile",
        fake_reconcile,
    )

    assert govern_literature_backlog.main(["--article-min-score", "0.60"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["read_only"] is True
    assert report["plan"]["projected_actionable_backlog"] == 439
    assert calls == [{
        "dry_run": True,
        "export": False,
        "policy_overrides": {"autopilot_article_min_score": 0.6},
        "diagnostics": True,
    }]

    assert govern_literature_backlog.main([
        "--article-min-score", "0.60", "--apply", "--confirm-plan-sha256", "wrong"
    ]) == 2
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["applied"] is False
    assert "does not match" in blocked["error"]
    assert len(calls) == 2


def test_governance_cli_applies_only_confirmed_bounded_plan(monkeypatch, capsys) -> None:
    preview = _governance_preview()
    digest = governance_plan(preview, max_projected_backlog=500)["plan_sha256"]
    calls = []

    async def fake_audit():
        return {"actionable": {"total": 2152}}

    async def fake_reconcile(**kwargs):
        calls.append(kwargs)
        return dict(preview)

    monkeypatch.setattr(govern_literature_backlog, "audit_current_backlog", fake_audit)
    monkeypatch.setattr(
        govern_literature_backlog.literature_automation_service,
        "reconcile",
        fake_reconcile,
    )

    assert govern_literature_backlog.main([
        "--article-min-score", "0.60",
        "--apply",
        "--confirm-plan-sha256", digest,
        "--no-export",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is True
    assert "published_article_ids" not in report["result"]
    assert calls[-1] == {
        "dry_run": False,
        "export": False,
        "policy_overrides": {"autopilot_article_min_score": 0.6},
        "diagnostics": True,
    }
