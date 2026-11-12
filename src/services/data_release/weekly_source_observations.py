"""Verified first-public-observation ledger for weekly subscription sources.

Only built-in public scanners can add records. Research requires a verified
complete weekly inventory and the Pages-to-Papers publication binding; a
missing brief is not proof of a completed empty research week.
This module does not enable a digest, send mail, or assert upstream collection
completeness; it observes the published catalogue verified at scan time.
The ledger requires persistent storage shared by all release processes.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import urlopen

from scripts.automation import dispatch_research_digest as transport
from . import notice_subscription as notices, report_subscription as reports, research_subscription as research
from .weekly_research_observations import ResearchObservationError, scan_public_research


class ObservationError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ObservationError("observation_ledger_unreadable") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("items"), dict):
        raise ObservationError("observation_ledger_invalid")
    return value


def _timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ObservationError("observation_time_invalid") from exc


def _scan_reports(reports_dir: Path, origin: str, opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    from .weekly_digest import _url

    rows, manifest = reports._verified_catalogue(reports_dir, origin, opener)
    catalogue: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = reports._report(row, origin)
        if item:
            if item["id"] in catalogue:
                raise ObservationError("report_id_duplicate")
            catalogue[item["id"]] = item
    # Bound public requests and fail closed rather than emit a partial scan.
    if len(catalogue) > 1000:
        raise ObservationError("report_scan_limit_exceeded")
    ordered = sorted(catalogue.values(), key=lambda item: item["id"])
    reports._verify_pages(ordered, reports_dir, manifest, opener)
    rows_after, manifest_after = reports._verified_catalogue(reports_dir, origin, opener)
    if research._fingerprint(rows) != research._fingerprint(rows_after) or research._fingerprint(manifest) != research._fingerprint(manifest_after):
        raise ObservationError("report_publication_changed")
    items = []
    for item in ordered:
        label = f"{item['country']} · {item['period_start']} – {item['period_end']}"
        items.append({"id": "report:" + item["id"], "countries": [item["country"]], "diseases": item["diseases"],
                      "localized_markdown": {"en": f"**Surveillance report**\n\n[{label}]({_url(item['url'])})",
                                             "zh": f"**监测报告**\n\n[{label}]({_url(item['url'])})"}})
    return items, {"source_origin": origin, "index_sha256": research._fingerprint(rows),
                   "manifest_sha256": research._fingerprint(manifest)}


def _scan_notices(root: Path, astro_dir: Path, identity: Mapping[str, Any], origin: str,
                  mode: str, opener: Callable[..., Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    from .weekly_digest import _notice_items

    research._verify_built_artifact(astro_dir, identity)
    research._verify_live_release(origin, identity, opener=opener)
    if not (astro_dir / "dist" / "situation" / "index.html").is_file():
        raise ObservationError("situation_public_page_not_built")
    directory = astro_dir / "dist" / "situation" / "outbreaks"
    if directory.is_dir():
        # A directory, particularly an empty one, is not a complete inventory.
        # The independently published manifest names every outbreak object.
        raise ObservationError("bundled_notice_inventory_unverified")
    outbreaks, pointer = notices._published_outbreaks(root, origin, opener)
    weeks: dict[str, set[str]] = {}
    for obj in outbreaks:
        if not isinstance(obj, dict) or obj.get("schema_version") != "gids.situation_alerts.v1" or not isinstance(obj.get("alerts"), list):
            raise ObservationError("situation_export_invalid")
        for alert in obj["alerts"]:
            if not isinstance(alert, dict):
                raise ObservationError("situation_export_invalid")
            try:
                day = date.fromisoformat(str(alert.get("published_date") or ""))
            except ValueError as exc:
                raise ObservationError("notice_published_date_invalid") from exc
            year, week, _ = day.isocalendar()
            review = alert.get("ai")
            if (isinstance(review, dict) and review.get("verdict") == "accept"
                    and review.get("review_mode") == "cross_model"
                    and alert.get("status") in {"new", "imported", "ongoing", "ended"}):
                weeks.setdefault(f"{year:04d}-W{week:02d}", set()).add("notice:" + str(alert.get("alert_id") or ""))
    if len(weeks) > 1000:
        raise ObservationError("notice_scan_limit_exceeded")
    items = [item for week in sorted(weeks) for item in _notice_items(outbreaks, week, origin, str(identity["release_id"]), mode, weeks[week])]
    if len(items) > 10000 or len({item["id"] for item in items}) != len(items):
        raise ObservationError("notice_scan_invalid")
    if notices._situation_pointer(origin, opener) != pointer:
        raise ObservationError("situation_publication_changed")
    research._verify_live_release(origin, identity, opener=opener)
    return sorted(items, key=lambda item: item["id"]), {
        "source_origin": origin, "release_id": str(identity["release_id"]), "build_digest": str(identity["build_digest"]),
        "situation_pointer_sha256": hashlib.sha256(pointer).hexdigest(),
        "source_sha256": research._fingerprint(outbreaks),
    }


def record_public_scan(*, root: Path, source: str, mode: str, reports_dir: Path | None = None,
                       astro_dir: Path | None = None, identity: Mapping[str, Any] | None = None,
                       public_base_url: str = "", reports_base_url: str = "", papers_base_url: str = "",
                       opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    """Verify a complete public catalogue and persist its first observations.

    The first successful scan is a baseline and does not backfill past items.
    This function never accepts caller-supplied item lists or success proofs.
    """
    if mode not in {"pilot", "full"} or source not in {"reports", "notices", "research"}:
        raise ObservationError("observation_source_unsupported")
    if astro_dir is None or identity is None or reports_dir is None:
        raise ObservationError("observation_publication_context_required")
    directory = root / "exports" / "subscription-observations" / mode
    with research._outbox_lock(directory):
        try:
            origin = transport._https_origin(public_base_url, "public_base_url")
            report_origin = transport._https_origin(reports_base_url, "reports_base_url")
            research._verify_built_artifact(astro_dir, identity)
            research._verify_live_release(origin, identity, opener=opener)
            if not (astro_dir / "dist" / "situation" / "index.html").is_file():
                raise ObservationError("situation_public_page_not_built")
            _, manifest = reports._verified_catalogue(reports_dir, report_origin, opener)
            outbreak_dir = astro_dir / "dist" / "situation" / "outbreaks"
            pointer = None if outbreak_dir.is_dir() else notices._situation_pointer(origin, opener)
            if pointer is not None:
                notices.validate_manifest(pointer)
            if source == "reports":
                items, evidence = _scan_reports(reports_dir, report_origin, opener)
                if evidence["manifest_sha256"] != research._fingerprint(manifest):
                    raise ObservationError("report_publication_changed")
            elif source == "notices":
                items, evidence = _scan_notices(root, astro_dir, identity, origin, mode, opener)
                expected_pointer = hashlib.sha256(pointer).hexdigest() if pointer is not None else "bundled"
                if evidence["situation_pointer_sha256"] != expected_pointer:
                    raise ObservationError("situation_publication_changed")
            else:
                items, evidence = scan_public_research(astro_dir=astro_dir, identity=identity, public_base_url=origin,
                                                       papers_base_url=papers_base_url, opener=opener)
            _, latest_manifest = reports._verified_catalogue(reports_dir, report_origin, opener)
            if research._fingerprint(manifest) != research._fingerprint(latest_manifest):
                raise ObservationError("report_publication_changed")
            if pointer is not None and notices._situation_pointer(origin, opener) != pointer:
                raise ObservationError("situation_publication_changed")
            research._verify_live_release(origin, identity, opener=opener)
            provenance = {
                "mode": mode, "public_base_url": origin, "reports_base_url": report_origin,
                "pages_release_id": str(identity["release_id"]), "pages_build_digest": str(identity["build_digest"]),
                "reports_manifest_sha256": research._fingerprint(manifest),
                "situation_snapshot": ({"kind": "public_pointer", "sha256": hashlib.sha256(pointer).hexdigest()}
                                       if pointer is not None else {"kind": "pages_build", "sha256": str(identity["build_digest"])}),
            }
            if source == "research":
                provenance.update({"papers_origin": evidence["papers_origin"],
                                   "papers_manifest_sha256": evidence["papers_manifest_sha256"],
                                   "papers_generation_sha256": evidence["papers_generation_sha256"],
                                   "papers_manifest_url": evidence["papers_manifest_url"],
                                   "research_evidence_manifest_sha256": evidence["evidence_manifest_sha256"]})
        except ObservationError:
            raise
        except (reports.ReportSubscriptionError, notices.NoticeSubscriptionError, research.ResearchSubscriptionError,
                transport.DispatchError, ResearchObservationError, OSError, ValueError, TypeError, KeyError) as exc:
            raise ObservationError(str(exc)) from exc
        now = _now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ObservationError("observation_time_invalid")
        timestamp = now.astimezone(timezone.utc).isoformat()
        path = directory / f"{source}.json"
        state = _read(path) if path.exists() else {
            "schema_version": 1, "source": source, "mode": mode, "baseline_at": timestamp, "items": {},
        }
        if state.get("source") != source or state.get("mode") != mode or (state.get("scanned_at") and _timestamp(state["scanned_at"]) > now):
            raise ObservationError("observation_ledger_invalid")
        seen = state["items"]
        for entry in seen.values():
            if not isinstance(entry, dict):
                raise ObservationError("observation_ledger_invalid")
            entry["present"] = False
        baseline = "scanned_at" not in state
        for item in items:
            identifier = str(item.get("id") or "")
            prefix = {"reports": "report:", "notices": "notice:", "research": "research:"}[source]
            if not identifier or not identifier.startswith(prefix):
                raise ObservationError("observation_item_invalid")
            fingerprint = research._fingerprint(item)
            if identifier not in seen:
                seen[identifier] = {"first_seen": None if baseline else timestamp, "initial_sha256": fingerprint}
            seen[identifier].update({"present": True, "item": item, "current_sha256": fingerprint})
        state.update({"scanned_at": timestamp, "evidence": evidence, "provenance": provenance,
                      "snapshot_sha256": research._fingerprint(items)})
        research._save(path, state)
        return {"source": source, "mode": mode, "scanned_at": timestamp, "item_count": len(items),
                "snapshot_sha256": state["snapshot_sha256"]}


def completion_proof(*, root: Path, source: str, mode: str, week: str) -> dict[str, Any]:
    """Create a proof only from the durable ledger; never from caller content."""
    if source not in {"reports", "notices", "research"} or mode not in {"pilot", "full"} or not research._valid_week(week):
        raise ObservationError("observation_source_unsupported")
    directory = root / "exports" / "subscription-observations" / mode
    with research._outbox_lock(directory):
        state = _read(directory / f"{source}.json")
        start = datetime.combine(date.fromisocalendar(int(week[:4]), int(week[6:]), 1), datetime.min.time(), timezone.utc)
        end = start + timedelta(days=7)
        if state.get("source") != source or state.get("mode") != mode or _timestamp(state["baseline_at"]) > start or _timestamp(state["scanned_at"]) < end:
            raise ObservationError("observation_week_not_closed")
        selected = []
        for identifier, entry in state["items"].items():
            seen = entry.get("first_seen") if isinstance(entry, dict) else None
            if seen and start <= _timestamp(seen) < end:
                item = entry.get("item")
                if not entry.get("present") or not isinstance(item, dict) or item.get("id") != identifier or entry.get("initial_sha256") != research._fingerprint(item):
                    raise ObservationError("observation_item_changed_or_removed")
                selected.append(item)
        selected.sort(key=lambda item: item["id"])
        return {"source": source, "mode": mode, "week": week, "scanned_at": state["scanned_at"],
                "scan_snapshot_sha256": state["snapshot_sha256"], "expected_item_ids": [item["id"] for item in selected],
                "selected_sha256": research._fingerprint(selected), "evidence": state["evidence"],
                "provenance": state["provenance"]}


def completion_authority(root: Path, mode: str) -> Callable[[Mapping[str, Any], Any], bool]:
    """Bind the coordinator's proposed items to a fresh ledger-derived proof."""
    def verify(context: Mapping[str, Any], proof: Any) -> bool:
        try:
            source, week = context["source"], context["week"]
            actual = completion_proof(root=root, source=source, mode=mode, week=week)
            expected_end = datetime.combine(date.fromisocalendar(int(week[:4]), int(week[6:]), 1), datetime.min.time(), timezone.utc) + timedelta(days=7)
            return (context.get("week_end_exclusive") == expected_end.isoformat() and proof == actual
                    and context.get("source_items_sha256") == actual["selected_sha256"]
                    and all(context.get(key) == value for key, value in actual["provenance"].items()))
        except (ObservationError, OSError, ValueError, KeyError, TypeError):
            return False
    return verify
