"""Commit public curves and the selected publication acknowledgements together.

The worker excludes every COLLECTION_STORE_TASK_TYPE for the whole EXPORT_DATA
execution. File generation and this final transaction therefore read identical
formal facts, even though production verification separates the two reads.
The worker singleton lease also prevents independent claim loops from racing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, or_, select, union, update

from src.core.country_library import expand_country_scopes, get_country_bootstrap_config
from src.domain import Country, CountryPublishState, PublicDiseaseSeries
from src.domain.collection import PublishDirty
from src.generation.public_series_store import _load_publish_context, _publish_country


async def _retire_disabled_publications(db) -> set[str]:
    """Read only stored country identifiers; no observation or curve projection."""
    published = union(
        select(PublicDiseaseSeries.country_code),
        select(CountryPublishState.country_code),
    ).subquery()
    rows = (
        await db.execute(
            select(published.c.country_code, Country.is_active).outerjoin(
                Country, Country.code == published.c.country_code
            )
        )
    ).all()
    retired = {
        code
        for code, active in rows
        if not active
        or get_country_bootstrap_config(code).get("public_release_enabled", True)
        is False
    }
    if retired:
        for model in (PublicDiseaseSeries, CountryPublishState):
            await db.execute(
                delete(model)
                .where(model.country_code.in_(retired))
                .execution_options(synchronize_session=False)
            )
    return retired


async def commit_release_projection(
    db,
    *,
    task_uuid: str,
    country_codes: list[str],
    full: bool,
    dirty_ids: list[int],
    marked_before: str | None = None,
) -> None:
    published_codes = []
    if full or country_codes:
        context = await _load_publish_context()
        available = set(context["countries_by_code"])
        unknown = sorted(set(country_codes) - available) if not full else []
        if unknown:
            raise ValueError(
                "Cannot publish unknown or disabled country scopes: "
                + ", ".join(unknown)
            )
        published_codes = [
            code
            for code in expand_country_scopes(
                context["countries_by_code"] if full else country_codes,
                available_codes=context["countries_by_code"],
            )
            if code in available
        ]
        for code in published_codes:
            # A single transaction covers all countries: one failure rolls back
            # every public row and leaves every selected marker pending.
            await _publish_country(db, code, context, force=True)
    # This function runs only after successful deployment verification. Retire
    # old disabled snapshots in that same final transaction, including on a
    # site-only release, without scanning facts or touching active timestamps.
    retired = await _retire_disabled_publications(db)
    published_codes = [code for code in published_codes if code not in retired]
    predicate = PublishDirty.id.in_(dirty_ids)
    if full and marked_before:
        predicate = PublishDirty.marked_at <= datetime.fromisoformat(marked_before)
    if dirty_ids or (full and marked_before):
        await db.execute(
            update(PublishDirty)
            .where(
                predicate,
                PublishDirty.published_at.is_(None),
                or_(
                    PublishDirty.line != "surveillance",
                    PublishDirty.scope_key.in_(published_codes),
                ),
            )
            .values(
                published_at=datetime.now(timezone.utc), published_task_uuid=task_uuid
            )
        )
