"""Stage a personalized digest only after all published sources are closed.

CompletionAuthority must consult trustworthy source-controller run state; a
public snapshot proves publication but cannot by itself prove completeness.
The existing producers do not yet issue the required attestations. This module
does not send mail and is not connected to an automatic scheduler. Before
activating it, disable only the legacy report dispatch to weekly_digest while
preserving the reports stream, and reconcile its pending, sending and unknown
deliveries. The old and new campaign keys do not deduplicate one another; the
cutover must prevent overlapping workers and set an explicit activation floor.
Research issues require the current public catalogue, integrity events and
Papers publication verifier to prove each selected article remains current.
The immutable provenance may also block a retry after an unrelated release;
current-publication equivalence must be proven before that guard is relaxed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from urllib.parse import quote
from urllib.request import urlopen

from scripts.automation import dispatch_research_digest as transport
from . import notice_subscription as notices
from . import report_subscription as reports
from . import research_subscription as research
from .weekly_research_observations import ResearchObservationError, scan_public_research


class WeeklyDigestError(RuntimeError):
    release_stage = "weekly_digest_preparation"


CompletionAuthority = Callable[[Mapping[str, Any], Any], bool]
SOURCES = ("reports", "notices", "research")
MARKER = "{{GIDS_PERSONALIZED_ITEMS}}"


def _bounds(week: str, now: datetime) -> tuple[datetime, datetime]:
    if not research._valid_week(week):
        raise WeeklyDigestError("weekly_digest_invalid_week")
    if now.tzinfo is None or now.utcoffset() is None:
        raise WeeklyDigestError("weekly_digest_timezone_required")
    start = datetime.combine(date.fromisocalendar(int(week[:4]), int(week[6:]), 1),
                             datetime.min.time(), timezone.utc)
    end = start + timedelta(days=7)
    if now.astimezone(timezone.utc) < end:
        raise WeeklyDigestError("weekly_digest_week_not_complete")
    return start, end


def _plain(value: Any, limit: int = 1200) -> str:
    raw = str(value or "").replace(MARKER, "")
    for char in "[]*_#\\" + chr(96):
        raw = raw.replace(char, "")
    return re.sub(r"\s+", " ", raw).strip()[:limit]


def _url(value: str) -> str:
    return quote(value, safe=":/?#[]@!$&'*+,;=%~._-")


def _report_items(rows: list[dict[str, Any]], state: Any, manifest: Mapping[str, Any],
                  reports_dir: Path, origin: str, week: str, start: datetime,
                  opener: Callable[..., Any]) -> list[dict[str, Any]]:
    if not isinstance(state, dict) or state.get("schema_version") != 1 or not isinstance(state.get("observations"), dict):
        raise WeeklyDigestError("report_observations_invalid")
    try:
        baseline = datetime.fromisoformat(str(state["baseline_at"]).replace("Z", "+00:00"))
    except (ValueError, KeyError) as exc:
        raise WeeklyDigestError("report_baseline_invalid") from exc
    if baseline.tzinfo is None or baseline.astimezone(timezone.utc) > start:
        raise WeeklyDigestError("report_week_not_fully_observed")
    catalogue: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = reports._report(row, origin)
        if item:
            if item["id"] in catalogue:
                raise WeeklyDigestError("report_id_duplicate")
            catalogue[item["id"]] = item
    selected = []
    for key, observation in state["observations"].items():
        if not isinstance(observation, dict):
            raise WeeklyDigestError("report_observations_invalid")
        observed = observation.get("week")
        if observed == "baseline":
            continue
        if not isinstance(observed, str) or not research._valid_week(observed):
            raise WeeklyDigestError("report_observations_invalid")
        if observed == week:
            item = catalogue.get(str(key))
            if not item or observation.get("fingerprint") != research._fingerprint(item):
                raise WeeklyDigestError("report_changed_or_removed_requires_review")
            selected.append(item)
    reports._verify_pages(selected, reports_dir, manifest, opener)
    result = []
    for item in sorted(selected, key=lambda value: value["id"]):
        label = f"{item['country']} · {item['period_start']} – {item['period_end']}"
        result.append({"id": "report:" + item["id"], "countries": [item["country"]],
                       "diseases": item["diseases"], "localized_markdown": {
                           "en": f"**Surveillance report**\n\n[{label}]({_url(item['url'])})",
                           "zh": f"**监测报告**\n\n[{label}]({_url(item['url'])})"}})
    return result


def _notice_items(outbreaks: list[dict[str, Any]], week: str, origin: str,
                  release_id: str, mode: str, expected_ids: set[str]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for obj in outbreaks:
        if not isinstance(obj, dict) or obj.get("schema_version") != "gids.situation_alerts.v1" or not isinstance(obj.get("alerts"), list):
            raise WeeklyDigestError("situation_export_invalid")
        for alert in obj["alerts"]:
            if not isinstance(alert, dict):
                raise WeeklyDigestError("situation_export_invalid")
            if "notice:" + str(alert.get("alert_id") or "") not in expected_ids:
                continue
            try:
                day = date.fromisoformat(str(alert.get("published_date") or ""))
            except ValueError as exc:
                raise WeeklyDigestError("notice_published_date_invalid") from exc
            target_end = date.fromisocalendar(int(week[:4]), int(week[6:]), 1) + timedelta(days=7)
            if day >= target_end:
                raise WeeklyDigestError("notice_published_after_week")
            review = alert.get("ai")
            if not isinstance(review, dict) or review.get("verdict") != "accept" or review.get("review_mode") != "cross_model":
                continue
            if alert.get("status") not in {"new", "imported", "ongoing", "ended"}:
                continue
            event = notices._event(alert, release_id=release_id, origin=origin, mode=mode)
            identifier = event["alert_id"]
            if identifier in found and found[identifier] != event:
                raise WeeklyDigestError("duplicate_official_alert_conflict")
            found[identifier] = event
    result = []
    for identifier, event in sorted(found.items()):
        title, source_url = _plain(event["title"], 200), _url(event["source_url"])
        status = {"new": "新发", "imported": "输入", "ongoing": "持续", "ended": "结束"}[event["notice_status"]]
        result.append({"id": "notice:" + identifier, "countries": event["countries"],
                       "diseases": event["diseases"], "localized_markdown": {
                           "en": f"**Official notice: {title}**\n\n{_plain(event['summary_en'])}\n\nNotice date: {event['published_date']} · Status: {event['notice_status']}\n\n[Official source]({source_url})",
                           "zh": f"**官方通报：{title}**\n\n{_plain(event['summary_zh'])}\n\n通报日期：{event['published_date']} · 状态：{status}\n\n[官方来源]({source_url})"}})
    return result


def _research_items(astro_dir: Path, origin: str, week: str, expected_ids: set[str]) -> list[dict[str, Any]]:
    # The trusted observation set can contain a late item from an older brief.
    # Search newest first and accept only briefs whose exact data bytes are
    # bound to both language pages in the verified deployed artifact.
    directory = astro_dir / "src" / "data" / "research" / "weekly"
    found: dict[str, dict[str, Any]] = {}
    paths = sorted(directory.glob("*.json"), reverse=True) if directory.is_dir() else []
    for path in paths:
        source_week = path.stem
        if not research._valid_week(source_week) or source_week > week:
            continue
        brief = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(brief, dict) or not isinstance(brief.get("cited_findings"), list):
            raise WeeklyDigestError("research_digest_brief_invalid")
        candidate_ids = {"research:" + str(finding.get("article_id") or "")
                         for finding in brief["cited_findings"] if isinstance(finding, dict)}
        wanted = (expected_ids - set(found)) & candidate_ids
        if not wanted:
            continue
        if brief.get("brief_status") not in {"ai_reviewed", "editorially_reviewed"}:
            raise WeeklyDigestError("research_brief_review_required")
        payload = research._payload_for_week(astro_dir, source_week, origin)
        for item in payload["content_items"]:
            identifier = "research:" + str(item["id"])
            if identifier in wanted:
                found[identifier] = {**item, "id": identifier, "localized_markdown": {
                    "en": "**Research**\n\n" + item["localized_markdown"]["en"],
                    "zh": "**研究进展**\n\n" + item["localized_markdown"]["zh"],
                }}
    return [found[key] for key in sorted(found)]


def _verify_current_research_integrity(selected: list[dict[str, Any]], scanned: list[dict[str, Any]]) -> None:
    """A historical brief must equal the independently verified current item."""
    current = {item.get("id"): item for item in scanned if isinstance(item, dict)}
    if len(current) != len(scanned) or any(
        item.get("id") not in current or research._fingerprint(item) != research._fingerprint(current[item["id"]])
        for item in selected
    ):
        raise WeeklyDigestError("research_current_publication_verification_required")


def _payload(week: str, items: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    if not items or len(items) > 100:
        raise WeeklyDigestError("weekly_digest_item_count_invalid")
    items = sorted(items, key=lambda item: item["id"])
    if len({item["id"] for item in items}) != len(items):
        raise WeeklyDigestError("weekly_digest_duplicate_item")
    dimensions = ("countries", "diseases", "research_topics", "study_types", "peer_review_statuses")
    for item in items:
        if (len(item["id"]) > 200 or sum(len(item.get(name, [])) for name in dimensions) > 200 or
                any(not isinstance(item["localized_markdown"].get(loc), str) or
                    not item["localized_markdown"][loc] or len(item["localized_markdown"][loc]) > 20000 or
                    MARKER in item["localized_markdown"][loc] for loc in ("en", "zh"))):
            raise WeeklyDigestError("weekly_digest_item_invalid")
    subjects = {"en": f"GIDS Weekly Digest — {week}", "zh": f"GIDS 每周摘要 — {week}"}
    intro = {"en": "Published surveillance reports, official notices, and research findings for this week.",
             "zh": "本周已发布的监测报告、官方通报与研究进展。"}
    contents = {locale: {"subject": subjects[locale], "markdown":
                intro[locale] + "\n\n" + "\n\n".join(item["localized_markdown"][locale] for item in items)}
                for locale in ("en", "zh")}
    if any(len(content["markdown"]) > 50000 for content in contents.values()):
        raise WeeklyDigestError("weekly_digest_content_too_long")
    payload = {"idempotency_key": ("pilot:" if mode == "pilot" else "") + f"weekly-digest:{week}:r1",
            "list_codes": ["weekly_digest"], "frequency": "weekly", "source_locale": "en",
            "default_locale": "en", "target_locales": ["en", "zh"], "contents": contents,
            "personalized_templates": {loc: intro[loc] + "\n\n" + MARKER for loc in ("en", "zh")},
            "content_items": items, "created_by": "weekly-digest-dispatcher", "max_recipients": 10000}
    if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 1024 * 1024:
        raise WeeklyDigestError("weekly_digest_payload_too_large")
    return payload


def prepare_weekly_digest(*, root: Path, astro_dir: Path, reports_dir: Path, identity: Mapping[str, Any],
                          public_base_url: str, reports_base_url: str, week: str,
                          completion_proofs: Mapping[str, Any], completion_authority: CompletionAuthority,
                          now: datetime | None = None, opener: Callable[..., Any] = urlopen,
                          mode: str = "pilot", papers_base_url: str = "") -> dict[str, Any]:
    """Verify sources and stage one immutable issue without contacting Worker.

    Each proof contains expected_item_ids: the complete set of content IDs
    first publicly observed in this week, including late publications. The
    authority must validate that set against durable source-controller records,
    a successful run, closed-through watermark >= end, and every field of the
    source context, including publication identity, domains and mode. Empty
    sources require the same proof. If an older source brief or
    notice should be included but cannot be assembled, the mismatch blocks the
    issue for review. Late changes to a staged issue also fail closed.
    """
    start, end = _bounds(week, now or datetime.now(timezone.utc))
    if mode not in {"pilot", "full"}:
        raise WeeklyDigestError("subscription_delivery_mode_invalid")
    if not callable(completion_authority) or set(completion_proofs) != set(SOURCES):
        raise WeeklyDigestError("weekly_digest_completion_authority_required")
    expected_by_source: dict[str, set[str]] = {}
    prefixes = {"reports": "report:", "notices": "notice:", "research": "research:"}
    for source in SOURCES:
        proof = completion_proofs[source]
        if not isinstance(proof, dict) or not isinstance(proof.get("expected_item_ids"), list):
            raise WeeklyDigestError(f"{source}_first_publication_evidence_required")
        expected = proof["expected_item_ids"]
        if (len(expected) > 100 or not all(isinstance(item, str) and item.startswith(prefixes[source]) and
                                          len(item) > len(prefixes[source]) for item in expected) or
                len(expected) != len(set(expected))):
            raise WeeklyDigestError(f"{source}_first_publication_evidence_invalid")
        expected_by_source[source] = set(expected)
    try:
        origin = transport._https_origin(public_base_url, "public_base_url")
        report_origin = transport._https_origin(reports_base_url, "reports_base_url")
        research._verify_built_artifact(astro_dir, identity)
        research._verify_live_release(origin, identity, opener=opener)
        rows, manifest = reports._verified_catalogue(reports_dir, report_origin, opener)
        state = reports._load_json(research.outbox_directory(root, "reports", mode) / "observations.json",
                                   "report_observations_invalid")
        report_items = _report_items(rows, state, manifest, reports_dir, report_origin, week, start, opener)
        if not (astro_dir / "dist" / "situation" / "index.html").is_file():
            raise WeeklyDigestError("situation_public_page_not_built")
        directory = astro_dir / "dist" / "situation" / "outbreaks"
        pointer = None
        if directory.is_dir():
            outbreaks = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]
        else:
            outbreaks, pointer = notices._published_outbreaks(root, origin, opener)
        notice_items = _notice_items(outbreaks, week, origin, str(identity["release_id"]), mode, expected_by_source["notices"])
        research_items = _research_items(astro_dir, origin, week, expected_by_source["research"])
        scanned_research, research_evidence = scan_public_research(
            astro_dir=astro_dir, identity=identity, public_base_url=origin,
            papers_base_url=papers_base_url, opener=opener,
        )
        _verify_current_research_integrity(research_items, scanned_research)
        if pointer is not None and notices._situation_pointer(origin, opener) != pointer:
            raise WeeklyDigestError("situation_publication_changed")
        research._verify_built_artifact(astro_dir, identity)
        research._verify_live_release(origin, identity, opener=opener)
        rows_after, manifest_after = reports._verified_catalogue(reports_dir, report_origin, opener)
        if (research._fingerprint(rows) != research._fingerprint(rows_after) or
                research._fingerprint(manifest) != research._fingerprint(manifest_after)):
            raise WeeklyDigestError("report_publication_changed")
        if pointer is not None and notices._situation_pointer(origin, opener) != pointer:
            raise WeeklyDigestError("situation_publication_changed")
    except WeeklyDigestError:
        raise
    except (notices.NoticeSubscriptionError, reports.ReportSubscriptionError, research.ResearchSubscriptionError,
            transport.DispatchError, ResearchObservationError, OSError, ValueError, KeyError, TypeError) as exc:
        raise WeeklyDigestError(str(exc)) from exc
    collections = {"reports": report_items, "notices": notice_items, "research": research_items}
    digests = {source: research._fingerprint(collections[source]) for source in SOURCES}
    provenance = {
        "mode": mode,
        "public_base_url": origin,
        "reports_base_url": report_origin,
        "pages_release_id": str(identity["release_id"]),
        "pages_build_digest": str(identity["build_digest"]),
        "reports_manifest_sha256": research._fingerprint(manifest),
        "situation_snapshot": ({"kind": "public_pointer", "sha256": hashlib.sha256(pointer).hexdigest()}
                               if pointer is not None else
                               {"kind": "pages_build", "sha256": str(identity["build_digest"])}),
        "papers_origin": research_evidence["papers_origin"],
        "papers_manifest_sha256": research_evidence["papers_manifest_sha256"],
        "papers_generation_sha256": research_evidence["papers_generation_sha256"],
        "papers_manifest_url": research_evidence["papers_manifest_url"],
        "research_evidence_manifest_sha256": research_evidence["evidence_manifest_sha256"],
    }
    for source in SOURCES:
        proof = completion_proofs[source]
        if expected_by_source[source] != {item["id"] for item in collections[source]}:
            raise WeeklyDigestError(f"{source}_first_publication_items_mismatch")
        context = {**provenance, "source": source, "week": week,
                   "week_end_exclusive": end.isoformat(), "source_items_sha256": digests[source]}
        try:
            valid = completion_authority(context, proof)
        except Exception as exc:
            raise WeeklyDigestError(f"{source}_completion_verification_failed") from exc
        if valid is not True:
            raise WeeklyDigestError(f"{source}_week_not_complete")
    items = [item for source in SOURCES for item in collections[source]]
    payload = _payload(week, items, mode) if items else None
    snapshot = {"source_digests": digests, "week_end_exclusive": end.isoformat(),
                "source_provenance": provenance, "payload": payload}
    fingerprint = research._fingerprint(snapshot)
    directory = research.outbox_directory(root, "weekly", mode)
    with research._outbox_lock(directory):
        path = directory / f"{week}.json"
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise WeeklyDigestError("weekly_digest_outbox_invalid") from exc
            if (not isinstance(previous, dict) or previous.get("week") != week or
                previous.get("mode") != mode or previous.get("status") not in {"staged", "empty", "completed"} or
                not isinstance(previous.get("snapshot"), dict) or
                research._fingerprint(previous["snapshot"]) != previous.get("snapshot_sha256")):
                raise WeeklyDigestError("weekly_digest_outbox_invalid")
            if previous["snapshot_sha256"] != fingerprint:
                raise WeeklyDigestError("weekly_digest_changed_requires_review")
            return previous
        record = {"week": week, "mode": mode, "status": "staged" if payload else "empty",
                  "snapshot": snapshot, "snapshot_sha256": fingerprint}
        research._save(path, record)
        return record
