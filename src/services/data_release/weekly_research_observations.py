"""Verify a complete published research evidence inventory before observation.

This is a read-only scanner. The current publication does not yet have the
required Pages-to-Papers manifest binding and therefore fails closed. It does
not create a source receipt, enqueue a campaign, or send mail.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping
from urllib.request import urlopen

from scripts.automation import dispatch_research_digest as transport
from . import report_subscription as reports, research_subscription as research
from .research_publication_verifier import (
    ResearchPublicationError, recheck_research_publication, verify_research_publication,
)


class ResearchObservationError(RuntimeError):
    pass


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def scan_public_research(*, astro_dir: Path, identity: Mapping[str, Any], public_base_url: str,
                         papers_base_url: str, opener: Callable[..., Any] = urlopen) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Verify the entire public reviewed-brief inventory and current evidence.

    Empty means no eligible reviewed findings in this verified public export;
    it does not claim that all external literature has been collected.
    """
    try:
        origin = transport._https_origin(public_base_url, "public_base_url")
        research._verify_built_artifact(astro_dir, identity)
        research._verify_live_release(origin, identity, opener=opener)
        path = astro_dir / "dist" / "research" / "evidence-manifest.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000_000:
            raise ResearchObservationError("research_evidence_manifest_unavailable")
        raw = path.read_bytes()
        if reports._get(origin + "/research/evidence-manifest.json", opener, limit=20_000_000) != raw:
            raise ResearchObservationError("research_evidence_manifest_mismatch")
        manifest = json.loads(raw)
        if isinstance(manifest, dict) and manifest.get("schema_version") == "gids.research-evidence.v2":
            shards = manifest.get("article_shards")
            if not isinstance(shards, list) or len(shards) > 100:
                raise ResearchObservationError("research_evidence_shards_invalid")
            articles = []
            seen = set()
            for shard in shards:
                if not isinstance(shard, dict):
                    raise ResearchObservationError("research_evidence_shard_invalid")
                digest, name, count = shard.get("sha256"), shard.get("path"), shard.get("count")
                if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                        or name != f"evidence-shards/{digest}.json" or name in seen
                        or type(count) is not int or not 0 < count <= 5000):
                    raise ResearchObservationError("research_evidence_shard_invalid")
                seen.add(name)
                shard_path = path.parent / name
                if shard_path.is_symlink() or not shard_path.is_file() or shard_path.stat().st_size > 20_000_000:
                    raise ResearchObservationError("research_evidence_shard_unavailable")
                shard_raw = shard_path.read_bytes()
                if (_sha(shard_raw) != digest
                        or reports._get(origin + "/research/" + name, opener, limit=20_000_000) != shard_raw):
                    raise ResearchObservationError("research_evidence_shard_mismatch")
                rows = json.loads(shard_raw)
                if not isinstance(rows, list) or len(rows) != count:
                    raise ResearchObservationError("research_evidence_shard_invalid")
                articles.extend(rows)
            manifest["articles"] = articles
            manifest["schema_version"] = "gids.research-evidence.v1"
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != "gids.research-evidence.v1"
                or manifest.get("weekly_inventory_complete") is not True
                or not isinstance(manifest.get("weekly_briefs"), list)
                or not isinstance(manifest.get("articles"), list)
                or not isinstance(manifest.get("integrity_events"), list)):
            raise ResearchObservationError("research_evidence_inventory_incomplete")
        if len(manifest["weekly_briefs"]) > 5000 or len(manifest["articles"]) > 100_000:
            raise ResearchObservationError("research_evidence_inventory_too_large")
        article_by_id: dict[str, dict[str, Any]] = {}
        for article in manifest["articles"]:
            if not isinstance(article, dict) or not isinstance(article.get("article_id"), str) or not article["article_id"] or article["article_id"] in article_by_id:
                raise ResearchObservationError("research_evidence_article_invalid")
            article_by_id[article["article_id"]] = article
        flagged: set[str] = set()
        for event in manifest["integrity_events"]:
            if not isinstance(event, dict) or not isinstance(event.get("article_id"), str) or event.get("event_type") not in {"retraction", "expression_of_concern", "correction"}:
                raise ResearchObservationError("research_integrity_events_invalid")
            flagged.add(event["article_id"])
        entries: dict[str, dict[str, Any]] = {}
        statuses = {"ai_reviewed", "editorially_reviewed", "automatically_compiled_not_editorially_reviewed"}
        for entry in manifest["weekly_briefs"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("week"), str) or not research._valid_week(entry["week"]) or entry["week"] in entries:
                raise ResearchObservationError("research_evidence_week_invalid")
            if (entry.get("brief_status") not in statuses or not isinstance(entry.get("brief_sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", entry["brief_sha256"])
                    or not isinstance(entry.get("cited_article_ids"), list)
                    or any(not isinstance(value, str) or not value for value in entry["cited_article_ids"])
                    or len(set(entry["cited_article_ids"])) != len(entry["cited_article_ids"])):
                raise ResearchObservationError("research_evidence_week_invalid")
            entries[entry["week"]] = entry
        source_dir = astro_dir / "src" / "data" / "research" / "weekly"
        if source_dir.is_symlink() or not source_dir.is_dir():
            raise ResearchObservationError("research_week_inventory_unavailable")
        paths = list(source_dir.glob("*.json"))
        if len(paths) != len(entries) or {path.stem for path in paths} != set(entries):
            raise ResearchObservationError("research_week_inventory_mismatch")
        # Verify every inventory file, including unreviewed ones. An omitted
        # or stale file cannot turn an incomplete source into an empty proof.
        for brief_path in paths:
            if brief_path.is_symlink() or not brief_path.is_file() or brief_path.stat().st_size > 20_000_000:
                raise ResearchObservationError("research_brief_unavailable")
            if _sha(brief_path.read_bytes()) != entries[brief_path.stem]["brief_sha256"]:
                raise ResearchObservationError("research_brief_digest_mismatch")
        reviewed = [entry for entry in entries.values() if entry["brief_status"] in {"ai_reviewed", "editorially_reviewed"}]
        if len(reviewed) > 100:
            raise ResearchObservationError("research_reviewed_briefs_limit_exceeded")
        found: dict[str, dict[str, Any]] = {}
        findings: dict[str, dict[str, Any]] = {}
        for entry in sorted(reviewed, key=lambda item: item["week"], reverse=True):
            week = entry["week"]
            brief_path = astro_dir / "src" / "data" / "research" / "weekly" / f"{week}.json"
            if brief_path.is_symlink() or not brief_path.is_file() or brief_path.stat().st_size > 20_000_000:
                raise ResearchObservationError("research_brief_unavailable")
            brief_raw = brief_path.read_bytes()
            if _sha(brief_raw) != entry["brief_sha256"]:
                raise ResearchObservationError("research_brief_digest_mismatch")
            brief = json.loads(brief_raw)
            if not isinstance(brief, dict) or brief.get("brief_status") != entry["brief_status"] or not isinstance(brief.get("cited_findings"), list):
                raise ResearchObservationError("research_brief_invalid")
            brief_findings = {item.get("article_id"): item for item in brief["cited_findings"] if isinstance(item, dict)}
            if len(brief_findings) != len(brief["cited_findings"]) or set(brief_findings) != set(entry["cited_article_ids"]):
                raise ResearchObservationError("research_brief_inventory_mismatch")
            payload = research._payload_for_week(astro_dir, week, origin)
            for item in payload["content_items"]:
                article_id = str(item.get("id") or "")
                if article_id in found:
                    continue
                finding, current = brief_findings.get(article_id), article_by_id.get(article_id)
                if (not isinstance(finding, dict) or not isinstance(current, dict) or article_id in flagged
                        or current.get("editorial_status") != "published" or current.get("integrity_status") != "current"
                        or current.get("peer_review_status") != "peer_reviewed" or current.get("indexable") is not True
                        or current.get("detail_available") is not True
                        or current.get("content_tier") not in {"quality_gated_bilingual_evidence", "quality_gated_multilingual_evidence"}
                        or current.get("slug") != finding.get("slug")):
                    raise ResearchObservationError("research_article_not_current")
                summaries = current.get("summaries")
                for locale in ("en", "zh"):
                    result = finding.get("finding_" + locale)
                    descriptor = summaries.get(locale) if isinstance(summaries, dict) else None
                    if (not isinstance(result, str) or not result.strip() or not isinstance(descriptor, dict)
                            or descriptor.get("main_findings_sha256") != _sha(result.strip().encode("utf-8"))):
                        raise ResearchObservationError("research_summary_version_mismatch")
                findings[article_id] = {key: finding[key] for key in ("article_id", "slug", "finding_en", "finding_zh")}
                found[article_id] = {**item, "id": "research:" + article_id, "localized_markdown": {
                    "en": "**Research**\n\n" + item["localized_markdown"]["en"],
                    "zh": "**研究进展**\n\n" + item["localized_markdown"]["zh"],
                }}
        if len(found) > 100:
            raise ResearchObservationError("research_findings_limit_exceeded")
        kwargs = dict(astro_dir=astro_dir, identity=identity, public_base_url=origin,
                      papers_base_url=papers_base_url, findings=[findings[key] for key in sorted(findings)], opener=opener)
        verified = verify_research_publication(**kwargs)
        if {item[0] for item in verified.articles} != set(findings) or len(verified.articles) != len(findings):
            raise ResearchObservationError("research_verified_article_inventory_mismatch")
        if reports._get(origin + "/research/evidence-manifest.json", opener, limit=20_000_000) != raw:
            raise ResearchObservationError("research_evidence_manifest_changed")
        recheck_research_publication(previous=verified, **kwargs)
        items = [found[key] for key in sorted(found)]
        return items, {"evidence_manifest_sha256": _sha(raw), "papers_origin": verified.papers_origin,
                       "papers_manifest_sha256": verified.papers_manifest_sha256,
                       "papers_generation_sha256": verified.papers_generation_sha256,
                       "papers_manifest_url": verified.papers_manifest_url,
                       "verified_articles_sha256": research._fingerprint(list(verified.articles)),
                       "weekly_inventory_count": len(entries), "reviewed_brief_count": len(reviewed)}
    except ResearchObservationError:
        raise
    except (OSError, ValueError, KeyError, TypeError, reports.ReportSubscriptionError,
            research.ResearchSubscriptionError, transport.DispatchError, ResearchPublicationError) as exc:
        raise ResearchObservationError(str(exc)) from exc
