from datetime import datetime, timedelta, timezone

from src.services.data_release.process_runner import ReleaseCommandError
from src.services.data_release.publication_wait import (
    claim_allowed,
    failure_kind,
    wait_reason,
)


def test_claim_skips_a_baseline_or_contract_wait():
    now = datetime(2026, 11, 18, 10, 30, tzinfo=timezone.utc)
    assert claim_allowed({}, now) is True
    assert claim_allowed({"publication_wait": {"kind": "baseline_invalid"}}, now) is False
    assert claim_allowed({"publication_wait": {"kind": "contract_blocked"}}, now) is False
    assert claim_allowed({"publication_wait": {"kind": "blocked_by_static"}}, now) is False
    assert claim_allowed({
        "publication_wait": {
            "kind": "transient_backoff",
            "not_before": (now + timedelta(seconds=30)).isoformat(),
        },
    }, now) is False
    assert claim_allowed({
        "publication_wait": {
            "kind": "transient_backoff",
            "not_before": (now - timedelta(seconds=1)).isoformat(),
        },
    }, now) is True


def test_baseline_value_error_is_a_wait_and_transient_command_is_not():
    assert failure_kind(ValueError(
        "Surveillance incremental baseline is missing or invalid; "
        "restore the published baseline before retrying"
    )) == "baseline_invalid"
    transient = ReleaseCommandError(
        "fetch failed: connection reset by peer",
        release_stage="cloudflare_pages_deploy",
        title="deploy",
        returncode=1,
    )
    assert failure_kind(transient) is None
    astro = ReleaseCommandError(
        "Error: research_evidence_week_data_mismatch\nBuild Astro Site failed with exit code 1.",
        release_stage="astro_build",
        title="Build Astro Site",
        returncode=1,
    )
    assert failure_kind(astro) == "contract_blocked"
    assert "unchanged contract" in wait_reason(
        "contract_blocked", "static", blocked_by=None, detail=str(astro),
    )
