"""Literature collection sources, one controllable unit per provider.

Ingestion runs one country source through collect, organize and store. Literature
uses the same idea: a sync names the providers it will fetch, and every other
provider keeps the cursor it already has. Enrichment of the records this run
collected stays in the organize step; it is not a separate collection source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CollectionSource:
    source_id: str
    label: str
    #: ``None`` means the provider is always part of the catalogue.
    flag: str | None
    fetched_key: str
    error_key: str
    skipped_key: str | None = None


COLLECTION_SOURCES: tuple[CollectionSource, ...] = (
    CollectionSource("crossref", "Crossref", None, "crossref_fetched", "crossref_source_errors"),
    CollectionSource("pubmed", "PubMed", "pubmed_enabled", "pubmed_core_fetched", "pubmed_source_errors"),
    CollectionSource("publisher-rss", "Publisher RSS", "publisher_rss_enabled", "publisher_rss_fetched", "publisher_rss_feed_errors"),
    CollectionSource("springer-nature", "Springer Nature", "springer_nature_enabled", "springer_nature_fetched", "springer_nature_errors", "springer_nature_skipped_credentials"),
    CollectionSource("elsevier", "Elsevier", "elsevier_enabled", "elsevier_fetched", "elsevier_errors", "elsevier_skipped_credentials"),
    CollectionSource("biorxiv-api", "bioRxiv", "preprint_discovery_enabled", "preprint_fetched", "preprint_source_errors"),
    CollectionSource("who-iris-oai", "WHO IRIS", "official_guidance_enabled", "official_guidance_fetched", "official_guidance_errors"),
    CollectionSource("controlled-query", "Controlled discovery", "controlled_discovery_enabled", "controlled_discovery_fetched", "controlled_discovery_source_errors"),
)

_BY_ID = {item.source_id: item for item in COLLECTION_SOURCES}
_ORDER = [item.source_id for item in COLLECTION_SOURCES]


def is_enabled(config: Any, source: CollectionSource) -> bool:
    if source.flag is None:
        return True
    return bool(getattr(config, source.flag, False))


def normalize_literature_sources(requested: Iterable[str] | None, config: Any) -> list[str]:
    """Return the providers a run will fetch. An empty request means every enabled provider."""

    raw = [str(item or "").strip() for item in (requested or [])]
    raw = [item for item in raw if item]
    if not raw:
        return []
    unknown: list[str] = []
    disabled: list[str] = []
    chosen: list[str] = []
    seen: set[str] = set()
    for source_id in raw:
        if source_id in seen:
            continue
        seen.add(source_id)
        spec = _BY_ID.get(source_id)
        if spec is None:
            unknown.append(source_id)
            continue
        if not is_enabled(config, spec):
            disabled.append(source_id)
            continue
        chosen.append(source_id)
    if unknown:
        raise ValueError("Unknown literature sources: " + ", ".join(unknown))
    if disabled:
        raise ValueError("Literature sources are not enabled: " + ", ".join(disabled))
    return chosen


def selected_sources(task: Any) -> list[str] | None:
    """``None`` means the run fetches every enabled provider."""

    raw = (getattr(task, "input_data", None) or {}).get("sources") if task is not None else None
    if not isinstance(raw, list) or not raw:
        return None
    chosen = [str(item).strip() for item in raw if str(item).strip()]
    return chosen or None


def source_plan(selected: list[str] | None, config: Any) -> dict[str, bool]:
    """Which collection sources this run will call."""

    return {
        spec.source_id: (selected is None or spec.source_id in selected) and is_enabled(config, spec)
        for spec in COLLECTION_SOURCES
    }


def run_label(default_label: str, selected: list[str] | None) -> str:
    """Keep a ``crossref`` prefix so a partial run can carry the shared cursor forward."""

    if not selected:
        return default_label
    parts = ["crossref"]
    parts.extend(source_id for source_id in _ORDER if source_id != "crossref" and source_id in selected)
    return "+".join(parts)


def run_covers(run: Any, source_id: str) -> bool:
    checkpoint = run.checkpoint if isinstance(getattr(run, "checkpoint", None), dict) else {}
    selected = checkpoint.get("selected_sources")
    if isinstance(selected, list) and selected:
        return source_id in {str(item) for item in selected}
    tokens = {part for part in str(getattr(run, "source", "") or "").split("+") if part}
    return source_id in tokens


def merge_carried_checkpoint(
    previous: dict[str, Any] | None,
    nested: dict[str, Any],
    selected: list[str],
) -> dict[str, Any]:
    """Keep cursors for providers this run did not touch."""

    merged = dict(previous or {})
    for key, value in nested.items():
        if value is not None:
            merged[key] = value
    merged["selected_sources"] = list(selected)
    return merged


_NESTED_CHECKPOINT_KEYS = (
    "pubmed_core",
    "rss",
    "controlled_discovery",
    "official_guidance",
    "springer_nature",
    "elsevier",
    "preprints",
)


def assemble_checkpoint(
    *,
    selected: list[str],
    crossref_planned: bool,
    crossref_ran: bool,
    source_checkpoint: dict[str, Any],
    previous: dict[str, Any] | None,
    nested: dict[str, Any],
) -> dict[str, Any]:
    """Write a subset run without rewinding cursors it did not collect.

    Crossref fields advance only when this run fetched Crossref. Nested cursors
    advance only when this run produced them. Every other cursor stays as it
    was on the latest completed Crossref checkpoint.
    """

    base = dict(previous or {})
    if crossref_planned and crossref_ran:
        saved = {key: base[key] for key in _NESTED_CHECKPOINT_KEYS if key in base}
        base.update(source_checkpoint)
        base.update(saved)
    produced = {
        key: value
        for key, value in nested.items()
        if key in _NESTED_CHECKPOINT_KEYS and value is not None
    }
    return merge_carried_checkpoint(base, produced, selected)


def catalogue_rows(config: Any) -> list[dict[str, Any]]:
    return [
        {
            "source_id": spec.source_id,
            "label": spec.label,
            "enabled": is_enabled(config, spec),
            "status": "disabled" if not is_enabled(config, spec) else "not_checked",
            "fetched": None,
            "error_count": None,
            "last_error": None,
            "last_run_uuid": None,
            "last_task_uuid": None,
            "last_started_at": None,
            "stages": [],
        }
        for spec in COLLECTION_SOURCES
    ]


def apply_run(row: dict[str, Any], run: Any) -> None:
    spec = _BY_ID[row["source_id"]]
    counts = run.counts if isinstance(getattr(run, "counts", None), dict) else {}
    fetched = counts.get(spec.fetched_key)
    errors = int(counts.get(spec.error_key) or 0)
    skipped = int(counts.get(spec.skipped_key) or 0) if spec.skipped_key else 0
    status = str(getattr(run, "status", "") or "")
    if skipped:
        row["status"] = "skipped"
    elif status == "running":
        row["status"] = "running"
    elif status == "failed" or errors:
        row["status"] = "failed"
    elif status == "completed":
        row["status"] = "ok"
    else:
        row["status"] = status or "not_checked"
    row["fetched"] = int(fetched) if isinstance(fetched, int) else None
    row["error_count"] = errors
    row["last_error"] = getattr(run, "error", None) if row["status"] == "failed" else None
    row["last_run_uuid"] = getattr(run, "run_uuid", None)
    row["last_task_uuid"] = getattr(run, "task_uuid", None)
    row["last_started_at"] = getattr(run, "started_at", None)


async def load_provider_catalogue(db: Any, config: Any) -> list[dict[str, Any]]:
    """Enabled providers plus the latest run and collect/organize/store stages for each."""

    from sqlalchemy import select

    from src.domain import LiteratureIngestRun, RunStage, Task, TaskType

    rows = catalogue_rows(config)
    runs = (
        await db.execute(
            select(LiteratureIngestRun)
            .where(LiteratureIngestRun.source.notin_(("research-radar-autopilot", "evidence-gap-discovery")))
            .order_by(LiteratureIngestRun.started_at.desc())
            .limit(40)
        )
    ).scalars().all()
    tasks = (
        await db.execute(
            select(Task)
            .where(Task.task_type == TaskType.SYNC_LITERATURE)
            .order_by(Task.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    task_ids = [task.task_uuid for task in tasks]
    grouped: dict[str, list[Any]] = {}
    if task_ids:
        stage_rows = (
            await db.execute(
                select(RunStage)
                .where(RunStage.task_uuid.in_(task_ids))
                .order_by(RunStage.attempt, RunStage.id)
            )
        ).scalars().all()
        for stage in stage_rows:
            grouped.setdefault(stage.task_uuid, []).append(stage)
    for row in rows:
        if not row["enabled"]:
            continue
        match = next((run for run in runs if run_covers(run, row["source_id"])), None)
        if match is not None:
            apply_run(row, match)
        task = next(
            (
                item
                for item in tasks
                if not selected_sources(item) or row["source_id"] in (selected_sources(item) or [])
            ),
            None,
        )
        if task is not None:
            if not row.get("last_task_uuid"):
                row["last_task_uuid"] = task.task_uuid
            apply_stages(row, grouped.get(task.task_uuid, []))
            if str(getattr(task.status, "value", task.status)) in {"pending", "queued", "running", "retrying"}:
                row["status"] = "running"
                row["last_task_uuid"] = task.task_uuid
    return rows


def apply_stages(row: dict[str, Any], stages: list[Any]) -> None:
    if not stages:
        return
    attempt = max(int(stage.attempt or 0) for stage in stages)
    current = [stage for stage in stages if int(stage.attempt or 0) == attempt]
    by_name = {stage.stage: stage for stage in current}
    task_uuid = current[0].task_uuid if current else row.get("last_task_uuid")
    row["stages"] = [
        {
            "stage": name,
            "status": by_name[name].status if name in by_name else None,
            "attempt": attempt,
            "error": by_name[name].error if name in by_name else None,
            "task_uuid": task_uuid,
        }
        for name in ("collect", "organize", "store")
    ]
    if task_uuid and not row.get("last_task_uuid"):
        row["last_task_uuid"] = task_uuid


__all__ = [
    "COLLECTION_SOURCES",
    "apply_run",
    "apply_stages",
    "assemble_checkpoint",
    "catalogue_rows",
    "load_provider_catalogue",
    "merge_carried_checkpoint",
    "normalize_literature_sources",
    "run_covers",
    "run_label",
    "selected_sources",
    "source_plan",
]
