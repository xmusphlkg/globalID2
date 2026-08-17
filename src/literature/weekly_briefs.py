"""Source-grounded weekly Research Radar brief projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
WEEKLY_REVIEW_REGISTRY_PATH = ROOT / "configs" / "literature" / "weekly_reviews.json"
_WEEK = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")
_UNSAFE_PUBLIC_TEXT = re.compile(r"(?:[<>]|https?://|mailto:|\bwww\.|[^\s@]+@[^\s@]+)", re.IGNORECASE)
_NON_HUMAN_REVIEWER = re.compile(
    r"\b(?:anonymous|automated|automation|compiler|chatgpt|openai|system|unknown|reviewer|tbd|test)\b",
    re.IGNORECASE,
)


def _public_text(value: Any, *, minimum: int, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not minimum <= len(text) <= maximum:
        return None
    if _UNSAFE_PUBLIC_TEXT.search(text) or any(ord(character) < 32 for character in text):
        return None
    return text


def _reviewed_at(value: Any, *, now: datetime) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    normalized = parsed.astimezone(timezone.utc)
    if normalized > now.astimezone(timezone.utc) + timedelta(minutes=5):
        return None
    return normalized.isoformat()


def project_weekly_editorial_review(
    value: Any,
    *,
    now: datetime | None = None,
) -> dict[str, str] | None:
    """Return the deliberately public subset of a complete human review.

    Partial, synthetic-looking, unsafe, naive-datetime, and future-dated
    records fail closed. Unknown keys (operator ids, email addresses, private
    notes, and similar internal metadata) are never copied to the projection.
    """
    if not isinstance(value, dict):
        return None
    current = now or datetime.now(timezone.utc)
    name = _public_text(value.get("name"), minimum=2, maximum=160)
    role = _public_text(value.get("role"), minimum=2, maximum=160)
    reviewed_at = _reviewed_at(value.get("reviewed_at"), now=current)
    if (
        not name
        or not role
        or not reviewed_at
        or sum(character.isalpha() for character in name) < 2
        or sum(character.isalpha() for character in role) < 2
        or _NON_HUMAN_REVIEWER.search(name)
    ):
        return None
    projected = {"name": name, "role": role, "reviewed_at": reviewed_at}
    institution = _public_text(value.get("institution"), minimum=2, maximum=240)
    if "institution" in value and institution is None:
        return None
    if institution:
        projected["institution"] = institution
    note_en = _public_text(value.get("note_en"), minimum=2, maximum=1000)
    note_zh = _public_text(value.get("note_zh"), minimum=1, maximum=1000)
    # A public note is optional, but an explicitly supplied note must be a
    # complete safe bilingual pair. Partial/unsafe optional metadata makes the
    # review record ambiguous, so the entire review fails closed.
    if "note_en" in value or "note_zh" in value:
        if not note_en or not note_zh:
            return None
        projected.update({"note_en": note_en, "note_zh": note_zh})
    return projected


def load_weekly_review_registry(
    path: Path = WEEKLY_REVIEW_REGISTRY_PATH,
) -> dict[str, dict[str, Any]]:
    """Load explicit weekly review evidence from an auditable registry.

    Malformed files, malformed rows, and duplicate weeks return no review for
    the affected scope. Validation of the human review fields happens at the
    final public projection boundary.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return {}
    rows = payload.get("reviews")
    if not isinstance(rows, list):
        return {}
    reviews: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        week = row.get("week")
        review = row.get("review")
        if not isinstance(week, str) or not _WEEK.fullmatch(week) or not isinstance(review, dict):
            continue
        if week in reviews:
            duplicates.add(week)
        else:
            reviews[week] = review
    for week in duplicates:
        reviews.pop(week, None)
    return reviews


def _finding(article: dict[str, Any]) -> dict[str, Any] | None:
    summary_en = (article.get("summary") or {}).get("en") or {}
    summary_zh = (article.get("summary") or {}).get("zh") or {}
    finding_en = str(summary_en.get("main_findings") or "").strip()
    finding_zh = str(summary_zh.get("main_findings") or "").strip()
    if not finding_en or not finding_zh:
        return None
    return {
        "article_id": article.get("article_id"),
        "slug": article.get("slug"),
        "title": article.get("title"),
        "finding_en": finding_en,
        "finding_zh": finding_zh,
        "source_url": f"/research/articles/{article.get('slug')}/" if article.get("slug") else None,
        "doi": article.get("doi"),
        "provenance": "published_bilingual_structured_summary",
    }


def enrich_weekly_briefs(
    briefs: Iterable[dict[str, Any]],
    *,
    surveillance_evidence: dict[str, Any] | None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Add cited findings, monitoring context, gaps, and review state.

    The result deliberately reports relationships rather than synthesizing a
    causal interpretation or a public-health risk judgment.
    """
    projection = surveillance_evidence or {}
    gaps = projection.get("evidence_gaps") or []
    output: list[dict[str, Any]] = []
    for raw in briefs:
        review = project_weekly_editorial_review(raw.get("_editorial_review"), now=now)
        # Underscore-prefixed generation metadata is private by contract. The
        # public status and byline below are always derived, never trusted from
        # the caller or registry.
        brief = {
            key: value
            for key, value in raw.items()
            if not str(key).startswith("_") and key not in {"brief_status", "byline"}
        }
        articles = [dict(article) for article in brief.get("articles") or []]
        disease_ids = {
            str(disease.get("disease_id") or "")
            for article in articles
            for disease in article.get("diseases") or []
            if disease.get("disease_id")
        }
        signals: dict[str, dict[str, Any]] = {}
        for article in articles:
            for signal in article.get("related_signals") or []:
                if signal.get("visibility") != "public":
                    continue
                signal_id = str(signal.get("signal_id") or "")
                if signal_id:
                    signals[signal_id] = {
                        key: signal.get(key)
                        for key in (
                            "signal_id", "section", "kind", "title", "disease_id",
                            "disease_name_en", "disease_name_zh", "geographies",
                            "data_through", "window", "risk", "relation_level", "situation_url",
                        )
                    }
        related_gaps = [
            {
                key: gap.get(key)
                for key in (
                    "gap_id", "signal_id", "disease_id", "disease_name_en", "disease_name_zh",
                    "geographies", "gap_type", "section", "kind", "data_through", "risk",
                    "context_article_count", "note_en", "note_zh",
                )
            }
            for gap in gaps
            if str(gap.get("disease_id") or "") in disease_ids
        ]
        findings = [finding for article in articles if (finding := _finding(article))]
        findings.sort(key=lambda item: (str(item.get("title") or ""), str(item.get("article_id") or "")))
        output.append({
            **brief,
            "articles": articles,
            "cited_findings": findings[:5],
            "monitoring_context": sorted(signals.values(), key=lambda item: str(item.get("signal_id") or "")),
            "evidence_gaps": related_gaps,
            "brief_status": (
                "editorially_reviewed"
                if review is not None
                else "automatically_compiled_not_editorially_reviewed"
            ),
            "byline": {
                "name_en": "GIDS Research Radar automated compiler",
                "name_zh": "GIDS Research Radar 自动编译器",
                "reviewer": review,
            },
            "methodology": {
                "en": "Source-level findings come only from published bilingual structured summaries. Monitoring links are deterministic Research Radar relationships; they do not establish cause, validate a signal, or constitute a risk assessment.",
                "zh": "文献发现仅来自已发布的双语结构化摘要。监测关联由 Research Radar 确定性关系生成，不建立因果关系、不验证信号，也不构成风险评估。",
            },
        })
    return output


__all__ = [
    "WEEKLY_REVIEW_REGISTRY_PATH",
    "enrich_weekly_briefs",
    "load_weekly_review_registry",
    "project_weekly_editorial_review",
]
