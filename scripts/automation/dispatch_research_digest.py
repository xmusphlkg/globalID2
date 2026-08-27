#!/usr/bin/env python3
"""Build or queue a replay-safe email campaign from the latest Research Radar brief.

Dry-run is the default. Network activity requires ``--apply``; sending queued
deliveries additionally requires ``--process``. Secrets are read only from the
environment and neither responses nor logs contain recipient records.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.literature.weekly_briefs import project_weekly_editorial_review
from src.literature.weekly_ai_review import project_weekly_ai_review


DEFAULT_WEEKLY_DIR = ROOT / "astro-site" / "src" / "data" / "research" / "weekly"
DEFAULT_PUBLIC_BASE_URL = "https://globalinfectiousdisease.com"
CREATE_ROUTE = "/api/admin/notifications"
WEEK_FILE = re.compile(r"^\d{4}-W\d{2}\.json$")
WEEK = re.compile(r"^\d{4}-W\d{2}$")
REVISION = re.compile(r"^r[1-9][0-9]{0,5}$")
TRUE_VALUES = {"1", "true", "yes", "on", "required", "strict"}


class DispatchError(RuntimeError):
    """A safe-to-log contract or transport error."""


def _record(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DispatchError(f"{field}_object_required")
    return value


def _text(value: Any, field: str, maximum: int = 50000) -> str:
    result = value.strip() if isinstance(value, str) else ""
    if not result:
        raise DispatchError(f"{field}_required")
    if len(result) > maximum:
        raise DispatchError(f"{field}_too_long")
    return result


def _https_origin(value: Any, field: str) -> str:
    text = _text(value, field, 2048)
    parsed = urlsplit(text)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise DispatchError(f"{field}_https_origin_required")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DispatchError(f"invalid_{field}") from exc
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None:
        host += f":{port}"
    return urlunsplit(("https", host, "", "", ""))


def latest_brief_path(directory: Path = DEFAULT_WEEKLY_DIR) -> Path:
    try:
        candidates = sorted(
            (path for path in directory.iterdir() if path.is_file() and WEEK_FILE.fullmatch(path.name)),
            reverse=True,
        )
    except OSError as exc:
        raise DispatchError("weekly_brief_directory_unreadable") from exc
    if not candidates:
        raise DispatchError("weekly_brief_not_found")
    return candidates[0]


def load_brief(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DispatchError("weekly_brief_not_found") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchError("weekly_brief_unreadable_or_invalid_json") from exc
    return _record(value, "weekly_brief")


def _slug(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:120]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _markdown_text(value: Any, field: str, maximum: int) -> str:
    # Prevent source text from creating links or headings in the generated email.
    return _text(value, field, maximum).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _reviewer_markdown_text(value: Any, field: str, maximum: int) -> str:
    result = _markdown_text(value, field, maximum)
    for character in ("`", "*", "_"):
        result = result.replace(character, f"\\{character}")
    return result


def _validated_public_review(brief: Mapping[str, Any]) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    status = brief.get("brief_status")
    byline_value = brief.get("byline")
    if byline_value is not None and not isinstance(byline_value, dict):
        raise DispatchError("invalid_brief_byline")
    byline = byline_value if isinstance(byline_value, dict) else {}
    if set(byline) - {"name_en", "name_zh", "reviewer", "ai_review"}:
        raise DispatchError("brief_byline_contains_non_public_fields")
    reviewer = byline.get("reviewer")
    ai_review = byline.get("ai_review")
    if status == "automatically_compiled_not_editorially_reviewed":
        if reviewer is not None or ai_review is not None:
            raise DispatchError("unreviewed_brief_exposes_reviewer")
        return None, None
    if status == "ai_reviewed":
        if reviewer is not None:
            raise DispatchError("ai_reviewed_brief_exposes_human_reviewer")
        projected_ai = project_weekly_ai_review(ai_review)
        if projected_ai is None:
            raise DispatchError("invalid_ai_reviewed_brief_evidence")
        return None, projected_ai
    if status != "editorially_reviewed":
        raise DispatchError("unsupported_brief_status")
    if ai_review is not None:
        raise DispatchError("editorially_reviewed_brief_exposes_ai_review")
    if not isinstance(reviewer, dict):
        raise DispatchError("reviewed_brief_reviewer_required")
    if set(reviewer) - {"name", "role", "reviewed_at", "institution", "note_en", "note_zh"}:
        raise DispatchError("brief_reviewer_contains_non_public_fields")
    projected = project_weekly_editorial_review(reviewer)
    if projected is None:
        raise DispatchError("invalid_reviewed_brief_reviewer")
    return {
        key: _reviewer_markdown_text(value, f"reviewer_{key}", 1000)
        for key, value in projected.items()
    }, None


def _source_url(value: Any, public_origin: str) -> str:
    source = _text(value, "finding_source_url", 2048)
    if not source.startswith("/research/articles/") or not source.endswith("/"):
        raise DispatchError("finding_public_article_source_required")
    url = urljoin(public_origin + "/", source.lstrip("/"))
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != urlsplit(public_origin).netloc:
        raise DispatchError("finding_public_article_source_required")
    return url


def _doi_url(value: Any) -> str:
    doi = str(value or "").strip()
    if not doi:
        return ""
    if len(doi) > 300 or not doi.lower().startswith("10.") or any(character.isspace() for character in doi):
        raise DispatchError("invalid_finding_doi")
    return "https://doi.org/" + quote(doi, safe="/:().-_;+")


def _validated_findings(brief: Mapping[str, Any], public_origin: str) -> list[dict[str, str]]:
    findings = brief.get("cited_findings")
    if not isinstance(findings, list) or not findings:
        raise DispatchError("cited_findings_required")
    if len(findings) > 20:
        raise DispatchError("too_many_cited_findings")
    articles = brief.get("articles")
    if not isinstance(articles, list):
        raise DispatchError("brief_articles_array_required")
    article_by_id = {
        str(article.get("article_id")): article
        for article in articles
        if isinstance(article, dict) and article.get("article_id")
    }
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in findings:
        finding = _record(raw, "cited_finding")
        article_id = _text(finding.get("article_id"), "finding_article_id", 160)
        if article_id in seen:
            raise DispatchError("duplicate_cited_finding")
        article = article_by_id.get(article_id)
        if not isinstance(article, dict):
            raise DispatchError("cited_finding_article_missing")
        if article.get("editorial_status") != "published" or article.get("content_tier") != "quality_gated_bilingual_evidence":
            raise DispatchError("cited_finding_not_public_bilingual_evidence")
        if finding.get("provenance") != "published_bilingual_structured_summary":
            raise DispatchError("cited_finding_provenance_required")
        result.append({
            "article_id": article_id,
            "title": _markdown_text(finding.get("title"), "finding_title", 500),
            "finding_en": _markdown_text(finding.get("finding_en"), "finding_en", 8000),
            "finding_zh": _markdown_text(finding.get("finding_zh"), "finding_zh", 8000),
            "source_url": _source_url(finding.get("source_url"), public_origin),
            "doi_url": _doi_url(finding.get("doi")),
        })
        seen.add(article_id)
    return result


def _markdown(
    *,
    week: str,
    locale: str,
    findings: Sequence[Mapping[str, str]],
    brief_url: str,
    methodology: str,
    reviewer: Mapping[str, str] | None,
    ai_review: Mapping[str, Any] | None,
) -> str:
    zh = locale == "zh"
    if reviewer:
        institution = f" · {reviewer['institution']}" if reviewer.get("institution") else ""
        reviewed_date = reviewer["reviewed_at"][:10]
        disclosure = (
            f"> 本邮件由已发布的双语结构化摘要自动编译，并由 {reviewer['name']}（{reviewer['role']}{institution}）于 {reviewed_date} 完成编辑审核；仅供研究导航，不构成公共卫生风险评估。"
            if zh
            else f"> Automatically compiled from published bilingual structured summaries and editorially reviewed by {reviewer['name']} ({reviewer['role']}{institution}) on {reviewed_date}; for research navigation, not a public-health risk assessment."
        )
    elif ai_review:
        reviewed_date = str(ai_review["reviewed_at"])[:10]
        disclosure = (
            f"> 本邮件由已发布的双语结构化摘要自动编译，并于 {reviewed_date} 通过仅限公开证据的 AI 质量审核；这不是编辑签审，也不构成公共卫生风险评估。"
            if zh
            else f"> Automatically compiled from published bilingual structured summaries and passed an AI review limited to the public evidence packet on {reviewed_date}; this is not editorial review or a public-health risk assessment."
        )
    else:
        disclosure = (
            "> 本邮件由已发布的双语结构化摘要自动编译，尚未经过编辑审阅；仅供研究导航，不构成公共卫生风险评估。"
            if zh
            else "> Automatically compiled from published bilingual structured summaries and not editorially reviewed; for research navigation, not a public-health risk assessment."
        )
    lines = [
        f"# {'Research Radar 每周研究简报' if zh else 'Research Radar weekly brief'} — {week}",
        "",
        disclosure,
    ]
    if reviewer and reviewer.get("note_zh" if zh else "note_en"):
        lines.extend([
            "",
            f"> {'审核备注' if zh else 'Review note'}: {reviewer['note_zh' if zh else 'note_en']}",
        ])
    for index, finding in enumerate(findings, 1):
        lines.extend([
            "",
            f"## {index}. {finding['title']}",
            "",
            finding["finding_zh" if zh else "finding_en"],
            "",
        ])
        source_label = "Research Radar 文献页" if zh else "Research Radar article"
        links = [f"[{source_label}]({finding['source_url']})"]
        if finding.get("doi_url"):
            links.append(f"[DOI]({finding['doi_url']})")
        lines.append(("来源：" if zh else "Sources: ") + " · ".join(links))
    lines.extend([
        "",
        f"[{'查看完整每周简报' if zh else 'Read the full weekly brief'}]({brief_url})",
        "",
        f"**{'方法说明' if zh else 'Methodology'}:** {methodology}",
    ])
    return "\n".join(lines)


def build_campaign_payload(
    brief: Mapping[str, Any],
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    *,
    revision: str = "r1",
    max_recipients: int = 10000,
) -> dict[str, Any]:
    public_origin = _https_origin(public_base_url, "public_base_url")
    week = _text(brief.get("week"), "brief_week", 8)
    if not WEEK.fullmatch(week):
        raise DispatchError("invalid_brief_week")
    if not REVISION.fullmatch(revision):
        raise DispatchError("invalid_revision")
    reviewer, ai_review = _validated_public_review(brief)
    if max_recipients < 1 or max_recipients > 50000:
        raise DispatchError("max_recipients_out_of_range")
    brief_path = _text(brief.get("url"), "brief_url", 500)
    if brief_path != f"/research/weekly/{week}/":
        raise DispatchError("brief_url_week_mismatch")
    brief_url = urljoin(public_origin + "/", brief_path.lstrip("/"))
    findings = _validated_findings(brief, public_origin)
    methodology = _record(brief.get("methodology"), "brief_methodology")
    method_en = _markdown_text(methodology.get("en"), "methodology_en", 4000)
    method_zh = _markdown_text(methodology.get("zh"), "methodology_zh", 4000)

    cited_ids = {finding["article_id"] for finding in findings}
    cited_articles = [
        article for article in brief.get("articles", [])
        if isinstance(article, dict) and str(article.get("article_id")) in cited_ids
    ]
    diseases = _unique([
        _slug(disease.get("slug"))
        for article in cited_articles
        for disease in (article.get("diseases") if isinstance(article.get("diseases"), list) else [])
        if isinstance(disease, dict)
    ])
    countries = _unique([
        str(country.get("code") or country.get("country_code") or "").strip().upper()
        for article in cited_articles
        for country in (article.get("countries") if isinstance(article.get("countries"), list) else [])
        if isinstance(country, dict)
    ])
    topics = _unique([
        _slug(topic.get("name"))
        for article in cited_articles
        for topic in (article.get("topics") if isinstance(article.get("topics"), list) else [])
        if isinstance(topic, dict)
    ])
    study_types = _unique([_slug(article.get("study_type")) for article in cited_articles])
    peer_statuses = _unique([_slug(article.get("peer_review_status")) for article in cited_articles])

    return {
        "idempotency_key": f"research-digest:{week}:{revision}",
        "source_ref": brief_url,
        "list_codes": ["research_digest"],
        "frequency": "weekly",
        "source_locale": "en",
        "default_locale": "en",
        "target_locales": ["en", "zh"],
        "contents": {
            "en": {
                "subject": f"Research Radar weekly brief — {week}",
                "markdown": _markdown(
                    week=week, locale="en", findings=findings, brief_url=brief_url,
                    methodology=method_en, reviewer=reviewer, ai_review=ai_review,
                ),
            },
            "zh": {
                "subject": f"Research Radar 每周研究简报 — {week}",
                "markdown": _markdown(
                    week=week, locale="zh", findings=findings, brief_url=brief_url,
                    methodology=method_zh, reviewer=reviewer, ai_review=ai_review,
                ),
            },
        },
        "countries": countries,
        "diseases": diseases,
        "research_topics": topics,
        "study_types": study_types,
        "peer_review_statuses": peer_statuses,
        "created_by": "research-brief-dispatcher",
        "max_recipients": max_recipients,
    }


def _endpoint(worker_base_url: str, route: str) -> str:
    return _https_origin(worker_base_url, "worker_base_url") + route


def _post_json(
    url: str,
    token: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float,
    opener: Callable[..., Any],
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "globalid-research-digest-dispatch/1",
    })
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 0) or response.getcode())
            raw = response.read(64 * 1024 + 1)
    except HTTPError as exc:
        raise DispatchError(f"worker_http_error:{exc.code}") from exc
    except (URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise DispatchError(f"worker_transport_error:{type(exc).__name__}") from exc
    if len(raw) > 64 * 1024:
        raise DispatchError("worker_response_too_large")
    if status < 200 or status >= 300:
        raise DispatchError(f"worker_http_error:{status}")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchError("worker_invalid_json_response") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise DispatchError("worker_rejected_response")
    return result


def queue_campaign(
    worker_base_url: str,
    token: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    if not token:
        raise DispatchError("admin_token_required")
    return _post_json(
        _endpoint(worker_base_url, CREATE_ROUTE), token, payload,
        timeout_seconds=timeout_seconds, opener=opener,
    )


def _safe_campaign_result(result: Mapping[str, Any], *, source_count: int) -> dict[str, Any]:
    campaign = result.get("campaign") if isinstance(result.get("campaign"), dict) else {}
    progress = campaign.get("progress") if isinstance(campaign.get("progress"), dict) else {}
    return {
        "campaign_id": str(campaign.get("id") or ""),
        "status": str(campaign.get("status") or "unknown"),
        "duplicate": result.get("duplicate") is True,
        "audience_count": int(campaign.get("audience_count") or progress.get("total") or 0),
        "source_count": source_count,
        "progress": {
            key: int(progress.get(key) or 0)
            for key in ("total", "queued", "sent", "failed", "skipped")
        },
    }


def process_campaign(
    worker_base_url: str,
    token: str,
    campaign_id: str,
    *,
    batch_size: int,
    max_batches: int,
    timeout_seconds: float,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    if not campaign_id or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", campaign_id):
        raise DispatchError("invalid_campaign_id")
    latest: dict[str, Any] = {}
    for _ in range(max_batches):
        latest = _post_json(
            _endpoint(worker_base_url, f"{CREATE_ROUTE}/{quote(campaign_id, safe='')}/process"),
            token,
            {"batch_size": batch_size},
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        progress = latest.get("progress") if isinstance(latest.get("progress"), dict) else {}
        if int(progress.get("queued") or 0) == 0:
            return latest
        if int(latest.get("processed") or 0) == 0:
            break
    raise DispatchError("campaign_processing_incomplete")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Validate and print the campaign without network activity (default)")
    mode.add_argument("--apply", action="store_true", help="Create or replay the queued campaign")
    parser.add_argument("--process", action="store_true", help="Process queued deliveries after --apply")
    parser.add_argument("--strict-config", action="store_true", help="Require valid Worker URL and admin token even in dry-run")
    parser.add_argument("--worker-base-url")
    parser.add_argument("--public-base-url")
    parser.add_argument("--revision", default="r1")
    parser.add_argument("--max-recipients", type=int, default=10000)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--max-batches", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    opener: Callable[..., Any] = urlopen,
) -> int:
    args = parse_args(argv)
    env = os.environ if environment is None else environment
    strict = args.strict_config or str(env.get("RESEARCH_DIGEST_DISPATCH_STRICT") or "").strip().lower() in TRUE_VALUES
    worker = str(args.worker_base_url or env.get("RESEARCH_DIGEST_WORKER_URL") or "").strip()
    public_base = str(args.public_base_url or env.get("GIDS_PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL).strip()
    token = str(env.get("SUBSCRIPTIONS__ADMIN_API_TOKEN") or "").strip()
    if args.process and not args.apply:
        print(json.dumps({"status": "failed", "error": "process_requires_apply"}), file=sys.stderr)
        return 2
    if args.batch_size < 1 or args.batch_size > 100 or args.max_batches < 1 or args.max_batches > 10000:
        print(json.dumps({"status": "failed", "error": "invalid_processing_bounds"}), file=sys.stderr)
        return 2
    if args.timeout_seconds <= 0 or args.timeout_seconds > 120:
        print(json.dumps({"status": "failed", "error": "timeout_seconds_out_of_range"}), file=sys.stderr)
        return 2
    missing = [name for name, value in (
        ("RESEARCH_DIGEST_WORKER_URL", worker),
        ("SUBSCRIPTIONS__ADMIN_API_TOKEN", token),
    ) if not value]
    if (strict or args.apply) and missing:
        print(json.dumps({"status": "failed", "reason": "configuration_missing", "missing": missing}, sort_keys=True), file=sys.stderr)
        return 2
    try:
        if strict or args.apply:
            _https_origin(worker, "worker_base_url")
        brief_path = args.brief or latest_brief_path()
        brief = load_brief(brief_path)
        payload = build_campaign_payload(
            brief, public_base, revision=args.revision, max_recipients=args.max_recipients
        )
        source_count = len(brief["cited_findings"])
        if not args.apply:
            print(json.dumps({"status": "dry_run", "brief": str(brief_path), "source_count": source_count, "payload": payload}, ensure_ascii=False, sort_keys=True))
            return 0
        result = queue_campaign(worker, token, payload, timeout_seconds=args.timeout_seconds, opener=opener)
        safe = _safe_campaign_result(result, source_count=source_count)
        if args.process and safe["progress"]["queued"] > 0:
            processed = process_campaign(
                worker, token, safe["campaign_id"], batch_size=args.batch_size,
                max_batches=args.max_batches, timeout_seconds=args.timeout_seconds, opener=opener,
            )
            progress = processed.get("progress") if isinstance(processed.get("progress"), dict) else {}
            safe["status"] = str(processed.get("status") or safe["status"])
            safe["progress"] = {key: int(progress.get(key) or 0) for key in ("total", "queued", "sent", "failed", "skipped")}
        safe["status_message"] = "processed" if args.process else "queued"
        print(json.dumps(safe, ensure_ascii=False, sort_keys=True))
        return 1 if safe["progress"]["failed"] else 0
    except DispatchError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
