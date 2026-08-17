"""Build daily and true period-aggregate Situation Room v3 reports."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import defaultdict
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .contracts import (
    ContextMetric,
    ContextPanel,
    Coverage,
    CurrencySlice,
    DataCurrency,
    EventUpdate,
    LocalizedText,
    MethodMetadata,
    PublicHealthRisk,
    QualityCheck,
    QualityGate,
    ReportMetadata,
    ReportSummary,
    SituationEventClusterV3,
    SituationReportV3,
    SituationSignalV3,
    SourceStatus,
)


METHOD_VERSION = "situation_room_v3.1"


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()
    ).hexdigest()


@lru_cache(maxsize=1)
def _code_version() -> str:
    explicit = os.getenv("GIDS_CODE_VERSION") or os.getenv("GIT_COMMIT")
    if explicit:
        return explicit[:80]
    root = Path(__file__).resolve().parents[3]
    head = root / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            reference = root / ".git" / value[5:]
            value = reference.read_text(encoding="utf-8").strip()
        value = value[:40] or "unknown"
        try:
            tracked_diff = subprocess.run(
                ["git", "diff", "--binary", "HEAD", "--"],
                cwd=root,
                check=True,
                capture_output=True,
                text=False,
                timeout=5,
            ).stdout
            untracked_output = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                cwd=root,
                check=True,
                capture_output=True,
                text=False,
                timeout=5,
            ).stdout
            dirty_digest = hashlib.sha256(tracked_diff)
            for raw_path in sorted(filter(None, untracked_output.split(b"\0"))):
                relative = raw_path.decode("utf-8", errors="surrogateescape")
                file_path = root / relative
                if not file_path.is_file():
                    continue
                dirty_digest.update(raw_path)
                dirty_digest.update(b"\0")
                dirty_digest.update(file_path.read_bytes())
            dirty = bool(tracked_diff or untracked_output)
        except (OSError, subprocess.SubprocessError):
            dirty = False
            dirty_digest = None
        return (
            f"{value}-dirty.{dirty_digest.hexdigest()[:12]}"
            if dirty and dirty_digest is not None
            else value
        )
    except OSError:
        return "unknown"


def _as_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _valid_http_url(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if candidate.lower() in {"", "nan", "none", "null", "nat"}:
        return None
    parsed = urlparse(candidate)
    return candidate if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else None


def _geography_codes(event: dict[str, Any]) -> set[str]:
    return {
        str(place.get("code"))
        for place in (event.get("geographies") or [])
        if isinstance(place, dict) and place.get("code")
    }


def cluster_official_events(events: Iterable[dict[str, Any]]) -> list[SituationEventClusterV3]:
    """Cluster updates with the same disease and overlapping geography.

    A stored cluster UUID can replace the deterministic provisional identifier
    when persistence is available; the grouping rule remains identical.
    """
    ordered = sorted(
        [event for event in events if event.get("disease_id") and _as_date(event.get("published_at"))],
        key=lambda event: (_as_date(event.get("published_at")) or date.min, str(event.get("id") or "")),
    )
    groups: list[list[dict[str, Any]]] = []
    for event in ordered:
        published = _as_date(event.get("published_at"))
        codes = _geography_codes(event)
        match: list[dict[str, Any]] | None = None
        for group in groups:
            group_dates = [_as_date(item.get("published_at")) for item in group]
            group_codes = set().union(*(_geography_codes(item) for item in group))
            if (
                group[0].get("disease_id") == event.get("disease_id")
                and codes
                and group_codes.intersection(codes)
                and published is not None
                and any(prior is not None and abs((published - prior).days) <= 45 for prior in group_dates)
            ):
                match = group
                break
        if match is None:
            groups.append([event])
        else:
            match.append(event)
    output: list[SituationEventClusterV3] = []
    for group in groups:
        dates = [_as_date(event.get("published_at")) for event in group]
        valid_dates = [value for value in dates if value is not None]
        disease_id = str(group[0].get("disease_id"))
        seed = disease_id + "|" + min(str(event.get("source_url") or event.get("id") or "") for event in group)
        geographies: dict[str, dict[str, str]] = {}
        updates: list[EventUpdate] = []
        for event in group:
            for place in event.get("geographies") or []:
                if isinstance(place, dict) and place.get("code"):
                    geographies[str(place["code"])] = {
                        "code": str(place["code"]),
                        "name": str(place.get("name") or place["code"]),
                    }
            evidence = event.get("evidence_links") or [
                {
                    "source": event.get("source"),
                    "url": event.get("source_url"),
                    "title": event.get("title"),
                }
            ]
            for index, link in enumerate(evidence):
                published = _as_date(event.get("published_at"))
                url = _valid_http_url(link.get("url"))
                if not published or url is None:
                    continue
                update_seed = f"{event.get('id')}|{url}|{index}"
                updates.append(
                    EventUpdate(
                        update_id="event-update:" + hashlib.sha256(update_seed.encode()).hexdigest()[:18],
                        source=str(link.get("source") or event.get("source") or "official"),
                        title=str(link.get("title") or event.get("title") or "Official update"),
                        url=url,
                        published_at=published,
                    )
                )
        updates = list({item.update_id: item for item in updates}.values())
        updates.sort(key=lambda item: (item.published_at, item.update_id))
        if not updates:
            continue
        output.append(
            SituationEventClusterV3(
                cluster_id="event-cluster:" + hashlib.sha256(seed.encode()).hexdigest()[:18],
                disease_id=disease_id,
                disease_name=str(group[0].get("disease_name") or disease_id),
                geographies=[geographies[key] for key in sorted(geographies)],
                first_published_at=min(valid_dates),
                last_published_at=max(valid_dates),
                updates=updates,
            )
        )
    output.sort(key=lambda item: (item.last_published_at, item.cluster_id), reverse=True)
    return output


def _official_risk(event: dict[str, Any]) -> tuple[str, str] | None:
    raw = str(event.get("agency_risk") or "").strip().lower().replace(" ", "_")
    if raw in {"low", "moderate", "high", "very_high"}:
        return raw, _valid_http_url(event.get("source_url")) or ""
    return None


def _apply_event_evidence(
    signals: list[SituationSignalV3],
    events: list[dict[str, Any]],
    clusters: list[SituationEventClusterV3],
) -> None:
    cluster_by_disease: dict[str, list[SituationEventClusterV3]] = defaultdict(list)
    for cluster in clusters:
        cluster_by_disease[cluster.disease_id].append(cluster)
    for signal in signals:
        eligible_clusters = [
            cluster
            for cluster in cluster_by_disease.get(signal.identity.disease_id, [])
            if signal.identity.country_code
            in {place.get("code") for place in cluster.geographies}
        ]
        candidates = [
            event
            for event in events
            if event.get("disease_id") == signal.identity.disease_id
            and signal.identity.country_code in _geography_codes(event)
        ]
        if not candidates or not eligible_clusters:
            continue
        if "official_match" not in signal.tags:
            signal.tags.append("official_match")
        signal.assessment.signal_type = "officially_correlated_signal"
        if signal.assessment.verification_status == "unreviewed":
            signal.assessment.verification_status = "under_review"
        signal.assessment.review_priority = "high"
        signal.assessment.evidence_gaps = [
            gap for gap in signal.assessment.evidence_gaps if gap != "official_event_not_matched"
        ]
        for cluster in eligible_clusters:
            cluster.matched_signal_ids.append(signal.identity.signal_id)
        rated = next(((_official_risk(event), event) for event in candidates if _official_risk(event)), None)
        if rated:
            (level, url), event = rated
            signal.assessment.public_health_risk = PublicHealthRisk(
                status="assessed",
                level=level,
                source="official_agency",
                rationale=f"Attributable {event.get('source')} agency rating",
                evidence_url=url or None,
            )
            signal.assessment.evidence_gaps = [
                gap for gap in signal.assessment.evidence_gaps if gap != "public_health_risk_not_assessed"
            ]


def _apply_signal_reviews(
    signals: list[SituationSignalV3],
    reviews: dict[str, dict[str, Any]],
) -> None:
    for signal in signals:
        review = reviews.get(signal.identity.signal_id)
        if not review:
            continue
        action = str(review.get("action") or "").lower()
        if action in {"verify", "publish"}:
            signal.assessment.verification_status = "verified"
        elif action in {"suppress", "reject"}:
            signal.assessment.verification_status = "rejected"
        else:
            signal.assessment.verification_status = "under_review"
        signal.assessment.verification_basis = "analyst_review"
        signal.assessment.verification_policy_version = None
        signal.assessment.verification_note = str(review.get("note") or "") or None
        actor = str(review.get("actor") or "").strip()
        # The immutable review ledger retains the real actor. Public report
        # payloads use a stable opaque identifier so reviewer email addresses
        # are not leaked to static JSON or subscriber mail metadata.
        signal.assessment.verified_by = (
            "reviewer:" + hashlib.sha256(actor.encode("utf-8")).hexdigest()[:16]
            if actor
            else None
        )
        signal.assessment.verified_at = _as_datetime(review.get("created_at"))
        payload = review.get("payload") or {}
        if (
            signal.assessment.verification_status == "verified"
            and isinstance(payload, dict)
            and payload.get("risk_level")
            in {"low", "moderate", "high", "very_high"}
            and payload.get("risk_rationale")
        ):
            signal.assessment.public_health_risk = PublicHealthRisk(
                status="assessed",
                level=str(payload["risk_level"]),
                source="audited_expert",
                rationale=str(payload["risk_rationale"]),
                evidence_url=_valid_http_url(payload.get("evidence_url")),
            )
            signal.assessment.evidence_gaps = [
                gap
                for gap in signal.assessment.evidence_gaps
                if gap != "public_health_risk_not_assessed"
            ]


def _apply_automatic_signal_verification(
    signals: list[SituationSignalV3],
    config: dict[str, Any],
    checked_at: datetime,
) -> None:
    policy = config.get("publication", {}).get("auto_verification", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return
    # Statistical auto-publication stays fail-closed until the checked
    # calibration protocol explicitly supports it. Merely toggling `enabled`
    # must not bypass a negative calibration decision.
    if str(policy.get("calibration_decision") or "") != "supported":
        return
    policy_version = str(policy.get("policy_version") or "guarded_auto_v1")
    maximum_q = float(policy.get("maximum_q", 0.01))
    minimum_completeness = float(policy.get("minimum_completeness", 0.95))
    allowed_fit_statuses = {
        str(value) for value in policy.get("allowed_fit_statuses", ["completed"])
    }
    allowed_tiers = {
        str(value)
        for value in policy.get(
            "allowed_detector_tiers",
            ["common_count", "rare_count"],
        )
    }
    allowed_sources = {
        str(value) for value in policy.get("allowed_source_systems", [])
    }
    for signal in signals:
        q_value = signal.anomaly.q_value
        eligible = (
            signal.anomaly.state in {"alert", "strong"}
            and signal.anomaly.effect_threshold_passed
            and q_value is not None
            and q_value <= maximum_q
            and signal.anomaly.fit_status in allowed_fit_statuses
            and signal.anomaly.detector_tier in allowed_tiers
            and signal.observation.completeness >= minimum_completeness
            and (
                not bool(policy.get("require_current", True))
                or signal.observation.data_status == "current"
            )
            and (
                not bool(policy.get("require_evidence_link", True))
                or bool(signal.evidence_links)
            )
            and (
                not allowed_sources
                or signal.identity.source_system in allowed_sources
            )
        )
        if not eligible:
            continue
        signal.assessment.verification_status = "verified"
        signal.assessment.verification_basis = "automated_policy"
        signal.assessment.verification_policy_version = policy_version
        signal.assessment.verification_note = (
            f"Automatically verified by {policy_version}: current, stable primary "
            f"fit, complete evidence, effect gate, q≤{maximum_q:g}."
        )
        signal.assessment.verified_by = f"policy:{policy_version}"
        signal.assessment.verified_at = checked_at


def _apply_automatic_signal_triage(
    signals: list[SituationSignalV3],
    config: dict[str, Any],
) -> None:
    """Queue statistical candidates without treating triage as verification."""

    policy = config.get("publication", {}).get("automatic_triage", {})
    if not isinstance(policy, dict) or not bool(policy.get("enabled", False)):
        return
    policy_version = str(policy.get("policy_version") or "exception_review_v1")
    queue_states = {
        str(value) for value in policy.get("queue_states", ["alert", "strong"])
    }
    for signal in signals:
        if (
            signal.anomaly.state not in queue_states
            or signal.assessment.verification_status != "unreviewed"
        ):
            continue
        hold_reasons: list[str] = []
        if (
            signal.anomaly.detector_tier == "rare_count"
            and bool(
                policy.get("rare_count_requires_official_match_for_queue", False)
            )
            and "official_match" not in signal.tags
        ):
            hold_reasons.append("rare_count_requires_official_match")
        if (
            signal.anomaly.fit_status == "fallback_completed"
            and bool(policy.get("fallback_requires_official_match_for_queue", False))
            and "official_match" not in signal.tags
        ):
            hold_reasons.append("fallback_fit_requires_official_match")
        if hold_reasons:
            signal.assessment.verification_note = (
                f"Automatically held by {policy_version}: "
                f"{', '.join(hold_reasons)}. The candidate remains private and "
                "is not added to the analyst queue without independent official "
                "evidence."
            )
            continue
        reasons = ["calibration_requires_independent_verification"]
        if signal.anomaly.detector_tier == "rare_count":
            reasons.append("rare_count_requires_analyst")
        if signal.anomaly.fit_status == "fallback_completed":
            reasons.append("fallback_fit_requires_analyst")
        if signal.observation.data_status != "current":
            reasons.append("non_current_data")
        if not signal.evidence_links:
            reasons.append("source_evidence_missing")
        signal.assessment.verification_status = "under_review"
        signal.assessment.verification_note = (
            f"Automatically queued by {policy_version}: {', '.join(reasons)}. "
            "Automatic public verification is disabled by calibration."
        )


def _context_panels(cards: Iterable[dict[str, Any]]) -> list[ContextPanel]:
    label_zh = {
        "test_positivity": "检测阳性率",
        "hospitalized_case_notifications": "住院病例",
        "acute_respiratory_illness_activity": "急性呼吸道疾病活动",
    }
    panels: list[ContextPanel] = []
    for card in cards:
        metrics = []
        for metric in card.get("metrics") or []:
            metric_type = str(metric.get("metric_type") or "context")
            metrics.append(
                ContextMetric(
                    metric_type=metric_type,
                    label=LocalizedText(
                        en=str(metric.get("label") or metric_type.replace("_", " ")),
                        zh=label_zh.get(metric_type, str(metric.get("label") or metric_type)),
                    ),
                    value=metric.get("value"),
                    unit=str(metric.get("unit") or "unknown"),
                    data_through=_as_date(metric.get("data_through")),
                    source_url=_valid_http_url(metric.get("source_url")),
                )
            )
        if metrics:
            panels.append(
                ContextPanel(
                    panel_id=str(card.get("id") or "context:" + _hash(card)[:16]),
                    topic="respiratory",
                    disease_id=card.get("disease_id"),
                    disease_name=card.get("disease_name"),
                    geography=card.get("country_name"),
                    metrics=metrics,
                    note=LocalizedText(
                        en="Metrics remain separate and are not combined into a synthetic risk score.",
                        zh="各项指标保持独立，不合成为综合风险分数。",
                    ),
                )
            )
    return panels


def _source_statuses(freshness: dict[str, Any]) -> list[SourceStatus]:
    output = []
    for source_id, details in sorted(freshness.items()):
        raw_status = str((details or {}).get("status") or "not_checked")
        status = raw_status if raw_status in {"fresh", "partial", "stale", "failed", "not_checked"} else "partial"
        output.append(
            SourceStatus(
                source_id=str(source_id),
                label=str((details or {}).get("label") or source_id).replace("_", " "),
                status=status,
                checked_at=_as_datetime((details or {}).get("checked_at")),
                last_success_at=_as_datetime((details or {}).get("last_success_at")),
                item_count=(details or {}).get("item_count"),
                error=(details or {}).get("error"),
            )
        )
    return output


def _currency(
    signals: Iterable[SituationSignalV3],
    ledger: Iterable[dict[str, Any]],
    freshness: dict[str, Any],
) -> DataCurrency:
    grouped: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
    for row in ledger:
        grouped[(str(row.get("source_system") or "unknown"), row.get("cadence"))].append(row)
    # A directly built test/report may not provide a full ledger. Preserve all
    # modeled signal currency without collapsing cadence or source.
    for signal in signals:
        key = (signal.identity.source_system, signal.identity.cadence)
        if not any(str(row.get("signal_id") or "") == signal.identity.signal_id for row in grouped[key]):
            grouped[key].append(
                {
                    "signal_id": signal.identity.signal_id,
                    "status": "modeled",
                    "data_through": signal.observation.data_through.isoformat(),
                    "latest_available_period": (
                        signal.observation.latest_available_period.isoformat()
                        if signal.observation.latest_available_period
                        else signal.observation.data_through.isoformat()
                    ),
                    "data_status": signal.observation.data_status,
                }
            )
    for source, details in freshness.items():
        if not any(key[0] == str(source) for key in grouped):
            grouped[(str(source), None)].append(
                {
                    "status": str((details or {}).get("status") or "not_checked"),
                    "data_through": (details or {}).get("data_through"),
                }
            )
    slices = []
    all_dates: list[date] = []
    for (source, cadence), rows in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        dates = [parsed for row in rows if (parsed := _as_date(row.get("data_through")))]
        available_dates = [
            parsed
            for row in rows
            if (parsed := _as_date(row.get("latest_available_period")))
        ]
        cutoff_dates = [
            parsed for row in rows if (parsed := _as_date(row.get("analysis_cutoff")))
        ]
        readiness = [
            float(value)
            for row in rows
            if (value := row.get("source_period_coverage")) is not None
        ]
        delayed_count = sum(row.get("data_status") == "delayed" for row in rows)
        held_back_count = sum(row.get("data_status") == "held_back" for row in rows)
        all_dates.extend(dates)
        statuses = {str(row.get("status") or "not_checked") for row in rows}
        if statuses <= {"stale"} or all(row.get("rejection_reason") == "stale" for row in rows):
            status = "stale"
        elif "failed" in statuses:
            status = "failed" if len(statuses) == 1 else "partial"
        elif any(row.get("rejection_reason") == "stale" for row in rows):
            status = "partial"
        elif delayed_count or held_back_count:
            status = "partial"
        elif dates:
            status = "fresh"
        else:
            status = "not_checked"
        slices.append(
            CurrencySlice(
                source_system=source,
                cadence=cadence,
                earliest_data_through=min(dates) if dates else None,
                latest_data_through=max(dates) if dates else None,
                comparable_through=max(cutoff_dates) if cutoff_dates else None,
                latest_available_through=max(available_dates) if available_dates else (max(dates) if dates else None),
                analyzed_series_count=sum(
                    row.get("status") in {"modeled", "context_only"} for row in rows
                ),
                delayed_series_count=delayed_count,
                held_back_series_count=held_back_count,
                readiness_ratio=min(readiness) if readiness else None,
                status=status,
            )
        )
    return DataCurrency(
        earliest_data_through=min(all_dates) if all_dates else None,
        latest_data_through=max(all_dates) if all_dates else None,
        by_source=slices,
    )


def _daily_gate(
    *,
    all_signals: list[SituationSignalV3],
    public_signals: list[SituationSignalV3],
    ledger: list[dict[str, Any]],
    required_sources: Iterable[str],
    required_context_sources: Iterable[str],
    source_health: dict[str, Any],
) -> QualityGate:
    modeled_rows = [row for row in ledger if row.get("status") == "modeled"]
    unstable_rows = [
        row
        for row in ledger
        if row.get("status") == "modeled"
        and row.get("fit_status") not in {"completed", "fallback_completed"}
    ]
    public_without_evidence = [
        signal.identity.signal_id for signal in public_signals if not signal.evidence_links
    ]
    public_delayed = [
        signal.identity.signal_id
        for signal in public_signals
        if signal.observation.data_status == "delayed"
    ]
    checks = [
        QualityCheck(
            id="modeled_series_nonzero",
            passed=bool(modeled_rows),
            details={"modeled": len(modeled_rows)},
        ),
        QualityCheck(
            id="unique_signal_identity",
            passed=len({signal.identity.signal_id for signal in all_signals}) == len(all_signals),
            details={"signals": len(all_signals)},
        ),
        QualityCheck(
            id="valid_fdr_output",
            passed=all(
                signal.anomaly.raw_p_value is None
                or (signal.anomaly.q_value is not None and signal.anomaly.q_value >= signal.anomaly.raw_p_value - 1e-9)
                for signal in all_signals
            ),
            details={"published": len(public_signals)},
        ),
        QualityCheck(
            id="stable_model_inference_only",
            passed=not unstable_rows,
            details={
                "unstable_count": len(unstable_rows),
                "signal_ids": [str(row.get("signal_id") or "") for row in unstable_rows[:20]],
            },
        ),
        QualityCheck(
            id="public_signal_evidence_links",
            passed=not public_without_evidence,
            details={"missing_signal_ids": public_without_evidence},
        ),
        QualityCheck(
            id="public_signal_temporal_relevance",
            passed=not public_delayed,
            details={"delayed_signal_ids": public_delayed},
        ),
    ]
    analyzed_sources = {
        str(row.get("source_system"))
        for row in ledger
        if row.get("status") == "modeled"
        and row.get("fit_status") in {"completed", "fallback_completed"}
    }
    for source in required_sources:
        checks.append(
            QualityCheck(
                id=f"analyzed_source_{source}",
                passed=str(source) in analyzed_sources,
                details={"source": str(source), "requires_stable_model": True},
            )
        )
    available_sources = {
        str(row.get("source_system"))
        for row in ledger
        if row.get("status") in {"modeled", "context_only"}
    }
    for source in required_context_sources:
        checks.append(
            QualityCheck(
                id=f"available_context_source_{source}",
                passed=str(source) in available_sources,
                details={"source": str(source), "model_inference_required": False},
            )
        )
    for source_id, details in sorted(source_health.items()):
        status = str((details or {}).get("status") or "not_checked")
        if status in {"failed", "stale", "not_checked"}:
            checks.append(
                QualityCheck(
                    id=f"source_acquisition_{source_id}",
                    passed=False,
                    severity="warning",
                    details={
                        "source": str(source_id),
                        "status": status,
                        "error": str((details or {}).get("error") or "")[:500],
                    },
                )
            )
    failed = [
        check.id
        for check in checks
        if not check.passed and check.severity == "blocking"
    ]
    warnings = [
        check.id
        for check in checks
        if not check.passed and check.severity == "warning"
    ]
    return QualityGate(
        status="failed" if failed else "degraded" if warnings else "passed",
        passed=not failed,
        failed_checks=failed,
        warning_checks=warnings,
        checks=checks,
    )


def build_daily_report_v3(
    *,
    signals: list[SituationSignalV3],
    ledger: list[dict[str, Any]],
    rejected_reasons: dict[str, int],
    events: list[dict[str, Any]],
    respiratory_cards: list[dict[str, Any]],
    freshness: dict[str, Any],
    config: dict[str, Any],
    checked_at: datetime,
    registered_series_count: int,
    event_reviews: dict[str, dict[str, Any]] | None = None,
    signal_reviews: dict[str, dict[str, Any]] | None = None,
    event_cluster_ids: dict[str, str] | None = None,
    revision: int = 1,
    supersedes_report_id: str | None = None,
) -> SituationReportV3:
    checked_at = checked_at if checked_at.tzinfo else checked_at.replace(tzinfo=timezone.utc)
    clusters = cluster_official_events(events)
    stable_cluster_ids = event_cluster_ids or {}
    for cluster in clusters:
        cluster.cluster_id = stable_cluster_ids.get(cluster.cluster_id, cluster.cluster_id)
    reviews = event_reviews or {}
    reviewed_clusters: list[SituationEventClusterV3] = []
    for cluster in clusters:
        review = reviews.get(cluster.cluster_id) or {}
        action = str(review.get("review_state") or "unreviewed")
        if action in {"suppress", "suppressed", "merge", "merged"}:
            continue
        if action in {"correct", "corrected"}:
            correction = review.get("corrected_payload") or {}
            if correction.get("disease_name"):
                cluster.disease_name = str(correction["disease_name"])
            if isinstance(correction.get("geographies"), list):
                corrected_geographies = [
                    {
                        "code": str(place["code"]),
                        "name": str(place.get("name") or place["code"]),
                    }
                    for place in correction["geographies"]
                    if isinstance(place, dict) and place.get("code")
                ]
                if corrected_geographies:
                    cluster.geographies = corrected_geographies
            cluster.status = "corrected"
        reviewed_clusters.append(cluster)
    clusters = reviewed_clusters
    _apply_event_evidence(signals, events, clusters)
    _apply_automatic_signal_triage(signals, config)
    _apply_automatic_signal_verification(signals, config, checked_at)
    # Operator review is the final state transition. It may verify, reject, or
    # add an attributable expert assessment after the official context has
    # been attached, and must not be overwritten by acquisition order.
    _apply_signal_reviews(signals, signal_reviews or {})
    require_verified = bool(
        config.get("publication", {}).get("require_verified_signals", False)
    )
    public_signals = [
        signal
        for signal in signals
        if signal.observation.data_status != "delayed"
        and signal.assessment.verification_status != "rejected"
        and (
            not require_verified
            or signal.assessment.verification_status == "verified"
        )
        and (signal.anomaly.state in {"alert", "strong"} or "official_match" in signal.tags)
    ]
    public_signals.sort(
        key=lambda signal: (
            {"high": 2, "standard": 1, "routine": 0}[signal.assessment.review_priority],
            -(signal.anomaly.q_value if signal.anomaly.q_value is not None else 1.0),
            signal.identity.signal_id,
        ),
        reverse=True,
    )
    gate = _daily_gate(
        all_signals=signals,
        public_signals=public_signals,
        ledger=ledger,
        required_sources=config.get("quality", {}).get("required_analyzed_source_systems", []),
        required_context_sources=config.get("quality", {}).get(
            "required_context_source_systems", []
        ),
        source_health=freshness,
    )
    config_hash = _hash(config)
    thresholds = config.get("thresholds", {})
    day = checked_at.date()
    report_id = f"situation-v3-daily-{day.isoformat()}-r{revision}"
    evaluated = len(ledger)
    modeled = sum(row.get("status") == "modeled" for row in ledger)
    disease_ids = {signal.identity.disease_id for signal in signals}
    jurisdictions = {signal.identity.canonical_geography_key for signal in signals}
    summary = ReportSummary(
        unique_signal_count=len(public_signals),
        alert_count=sum(signal.anomaly.state in {"alert", "strong"} for signal in public_signals),
        strong_count=sum(signal.anomaly.state == "strong" for signal in public_signals),
        official_event_count=len(clusters),
        active_at_period_end_count=sum(signal.anomaly.state in {"alert", "strong"} for signal in public_signals),
    )
    return SituationReportV3(
        public_enabled=bool(config.get("public_enabled", False)),
        report=ReportMetadata(
            report_id=report_id,
            kind="daily",
            period_key=day.isoformat(),
            period_start=day,
            period_end=day,
            as_of=checked_at,
            revision=revision,
            status="published" if gate.passed else "gate_failed",
            supersedes_report_id=supersedes_report_id,
        ),
        method=MethodMetadata(
            version=METHOD_VERSION,
            model="robust_quasi_poisson_v1",
            config_hash=config_hash,
            code_version=_code_version(),
            fdr_family="detector_tier_metric_type_cadence",
            alert_q=float(config.get("v3", {}).get("alert_q", 0.05)),
            strong_q=float(config.get("v3", {}).get("strong_q", 0.01)),
            parameters={
                "predictive_variance_inflation": float(
                    config.get("v3", {}).get("predictive_variance_inflation", 1.0)
                ),
                "maximum_analysis_workers": min(
                    4, int(config.get("v3", {}).get("maximum_analysis_workers", 1))
                ),
                "detector_tiers": dict(
                    config.get("v3", {}).get("detector_tiers", {})
                ),
                "publication": dict(config.get("publication", {})),
                "effect_thresholds": {
                    "minimum_current_cases": thresholds.get("minimum_current_cases", 20),
                    "minimum_absolute_increase": thresholds.get(
                        "minimum_absolute_increase", 10
                    ),
                    "minimum_relative_increase_pct": thresholds.get(
                        "minimum_relative_increase_pct", 25
                    ),
                    "metric_effects": dict(thresholds.get("metric_effects") or {}),
                    "special_thresholds": list(
                        thresholds.get("special_thresholds") or []
                    ),
                },
                "cadences": dict(config.get("cadences", {})),
                "data_latency": dict(config.get("data_latency", {})),
            },
        ),
        data_currency=_currency(signals, ledger, freshness),
        coverage=Coverage(
            registered_series_count=registered_series_count,
            evaluated_series_count=evaluated,
            modeled_series_count=modeled,
            rejected_series_count=evaluated - len(signals),
            published_signal_count=len(public_signals),
            jurisdiction_count=len(jurisdictions),
            disease_count=len(disease_ids),
            rejection_reasons=rejected_reasons,
            note=LocalizedText(
                en="Signals cover configured source-native series and are not exhaustive global surveillance.",
                zh="信号仅覆盖已配置的来源原生序列，并不代表穷尽全球监测。",
            ),
        ),
        summary=summary,
        signals=public_signals,
        events=clusters,
        context_panels=_context_panels(respiratory_cards),
        sources=_source_statuses(freshness),
        narrative=LocalizedText(
            en=f"{len(public_signals)} independently verified signals were published from {modeled} modeled source-native series; statistical candidates are triaged automatically but remain private until verified.",
            zh=f"从 {modeled} 条已建模的来源原生序列中发布了 {len(public_signals)} 个独立验证信号；统计候选会自动分流，但在完成验证前保持非公开。",
        ),
        limitations=LocalizedText(
            en="Anomaly results prioritize human review. Public-health risk is shown only when attributable official or audited expert evidence exists.",
            zh="异常结果仅用于安排人工复核；只有存在可归因的官方证据或经审计的专家判断时才展示公共卫生风险。",
        ),
        quality_gate=gate,
    )


def build_period_report_v3(
    daily_reports: Iterable[SituationReportV3 | dict[str, Any]],
    *,
    kind: str,
    period_key: str,
    period_start: date,
    period_end: date,
    as_of: datetime,
    revision: int = 1,
    previous_active_signal_ids: set[str] | None = None,
    supersedes_report_id: str | None = None,
) -> SituationReportV3:
    if kind not in {"weekly", "monthly"}:
        raise ValueError("period reports must be weekly or monthly")
    reports = [
        report if isinstance(report, SituationReportV3) else SituationReportV3.model_validate(report)
        for report in daily_reports
    ]
    reports = [
        report
        for report in reports
        if report.report.kind == "daily"
        and period_start <= report.report.period_start <= period_end
    ]
    latest_by_day: dict[date, SituationReportV3] = {}
    for report in sorted(reports, key=lambda item: (item.report.as_of, item.report.revision)):
        latest_by_day[report.report.period_start] = report
    reports = [latest_by_day[day] for day in sorted(latest_by_day)]
    if not reports:
        raise ValueError("a period report requires at least one daily report")
    expected_days = {
        date.fromordinal(ordinal)
        for ordinal in range(period_start.toordinal(), period_end.toordinal() + 1)
    }
    missing_days = sorted(expected_days - set(latest_by_day))
    failed_member_days = sorted(
        report.report.period_start for report in reports if not report.quality_gate.passed
    )
    passed_reports = [report for report in reports if report.quality_gate.passed]
    if not passed_reports:
        raise ValueError("a period report requires at least one gate-passed daily report")
    reports = passed_reports
    occurrences: dict[str, list[tuple[SituationReportV3, SituationSignalV3]]] = defaultdict(list)
    for report in reports:
        for signal in report.signals:
            occurrences[signal.identity.signal_id].append((report, signal))
    final_active = {signal.identity.signal_id for signal in reports[-1].signals}
    previous_active = previous_active_signal_ids or set()
    period_signals: list[SituationSignalV3] = []
    for signal_id, rows in occurrences.items():
        latest_report, latest_signal = rows[-1]
        signal = latest_signal.model_copy(deep=True)
        active_count = len(rows)
        if signal_id not in final_active:
            lifecycle = "resolved"
        elif signal_id not in previous_active and rows[0][0].report.period_start >= period_start:
            lifecycle = "new"
        elif active_count >= 2:
            lifecycle = "persistent"
        else:
            lifecycle = "active"
        signal.lifecycle.status = lifecycle
        signal.lifecycle.first_seen_at = rows[0][0].report.as_of
        signal.lifecycle.last_seen_at = latest_report.report.as_of
        signal.lifecycle.active_run_count = active_count
        q_values = [row.anomaly.q_value for _, row in rows if row.anomaly.q_value is not None]
        signal.lifecycle.peak_q_value = min(q_values) if q_values else None
        period_signals.append(signal)
    period_signals.sort(
        key=lambda signal: (
            signal.lifecycle.status != "resolved",
            {"high": 2, "standard": 1, "routine": 0}[signal.assessment.review_priority],
            -(signal.anomaly.q_value if signal.anomaly.q_value is not None else 1.0),
        ),
        reverse=True,
    )
    latest = reports[-1]
    events: dict[str, SituationEventClusterV3] = {}
    for report in reports:
        for event in report.events:
            existing = events.get(event.cluster_id)
            if existing is None:
                events[event.cluster_id] = event.model_copy(deep=True)
                continue
            by_update = {update.update_id: update for update in [*existing.updates, *event.updates]}
            existing.updates = sorted(by_update.values(), key=lambda update: (update.published_at, update.update_id))
            existing.first_published_at = min(existing.first_published_at, event.first_published_at)
            existing.last_published_at = max(existing.last_published_at, event.last_published_at)
            existing.matched_signal_ids = sorted(set(existing.matched_signal_ids) | set(event.matched_signal_ids))
    failed_checks = []
    if failed_member_days:
        failed_checks.append("daily_members_gate_passed")
    if missing_days:
        failed_checks.append("daily_members_complete")
    warning_checks = sorted(
        {
            f"daily_{report.report.period_key}:{warning}"
            for report in reports
            for warning in report.quality_gate.warning_checks
        }
    )
    gate = QualityGate(
        status=(
            "failed"
            if failed_checks
            else "degraded"
            if warning_checks
            else "passed"
        ),
        passed=not failed_checks,
        failed_checks=failed_checks,
        warning_checks=warning_checks,
        checks=[
            QualityCheck(
                id="daily_members_gate_passed",
                passed=not failed_member_days,
                details={
                    "gate_passed_member_count": len(reports),
                    "failed_member_days": [day.isoformat() for day in failed_member_days],
                },
            ),
            QualityCheck(
                id="daily_members_complete",
                passed=not missing_days,
                details={
                    "expected_member_count": len(expected_days),
                    "observed_member_count": len(latest_by_day),
                    "missing_days": [day.isoformat() for day in missing_days],
                },
            ),
        ],
    )
    report_id = f"situation-v3-{kind}-{period_key}-r{revision}"
    summary = ReportSummary(
        unique_signal_count=len(period_signals),
        alert_count=sum(signal.anomaly.state in {"alert", "strong"} for signal in period_signals),
        strong_count=sum(signal.anomaly.state == "strong" for signal in period_signals),
        official_event_count=len(events),
        new_count=sum(signal.lifecycle.status == "new" for signal in period_signals),
        persistent_count=sum(signal.lifecycle.status == "persistent" for signal in period_signals),
        resolved_count=sum(signal.lifecycle.status == "resolved" for signal in period_signals),
        active_at_period_end_count=len(final_active),
    )
    coverage = latest.coverage.model_copy(
        update={"published_signal_count": len(period_signals)}
    )
    return SituationReportV3(
        public_enabled=latest.public_enabled,
        report=ReportMetadata(
            report_id=report_id,
            kind=kind,
            period_key=period_key,
            period_start=period_start,
            period_end=period_end,
            as_of=as_of,
            revision=revision,
            status="published" if gate.passed else "gate_failed",
            supersedes_report_id=supersedes_report_id,
        ),
        method=latest.method,
        data_currency=latest.data_currency,
        coverage=coverage,
        summary=summary,
        signals=period_signals,
        events=list(events.values()),
        context_panels=latest.context_panels,
        sources=latest.sources,
        narrative=LocalizedText(
            en=f"{period_key}: {summary.new_count} new, {summary.persistent_count} persistent, and {summary.resolved_count} resolved review signals.",
            zh=f"{period_key}：新增 {summary.new_count} 个、持续 {summary.persistent_count} 个、消退 {summary.resolved_count} 个复核信号。",
        ),
        limitations=latest.limitations,
        quality_gate=gate,
    )


__all__ = [
    "METHOD_VERSION",
    "build_daily_report_v3",
    "build_period_report_v3",
    "cluster_official_events",
]
