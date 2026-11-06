"""Commit public curves and the selected publication acknowledgements together.

Normal releases commit the frozen curves they actually rendered. Formal facts
may advance during export/build/upload; only the selected dirty IDs are
acknowledged, so later collections remain pending.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
    prepared_dir: Path | None = None,
    country_scopes_exact: bool = False,
    require_prepared: bool = False,
) -> None:
    if require_prepared and (full or country_codes) and prepared_dir is None:
        raise ValueError("Concurrent release requires a frozen publication projection")
    if require_prepared and marked_before:
        raise ValueError("Concurrent release requires exact dirty IDs, not a timestamp cutoff")
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
            for code in (sorted(country_codes) if country_scopes_exact and not full else expand_country_scopes(
                context["countries_by_code"] if full else country_codes,
                available_codes=context["countries_by_code"],
            ))
            if code in available
        ]
        manifest = None
        if prepared_dir is not None:
            from src.generation.release_projection_snapshot import load_projection_manifest, validate_read_snapshot_projection

            manifest = (validate_read_snapshot_projection if require_prepared else load_projection_manifest)(prepared_dir)
            if set(manifest["countries"]) != set(published_codes):
                raise ValueError("Prepared release projection does not match release scopes")
        for code in published_codes:
            # A single transaction covers all countries: one failure rolls back
            # every public row and leaves every selected marker pending.
            if manifest is None:
                await _publish_country(db, code, context, force=True)
            else:
                from src.generation.release_projection_snapshot import read_projection_country
                from src.generation.public_series_store import build_public_series_row, _replace_country_rows

                payload = read_projection_country(prepared_dir, manifest, code)
                watermark = payload.get("source_high_water")
                high_water = datetime.fromisoformat(watermark) if watermark else None
                rows = []
                for disease_id, series in sorted(payload["disease_series"].items()):
                    row = build_public_series_row(code, disease_id, series, source_high_water=high_water)
                    if row is not None:
                        rows.append(row)
                await _replace_country_rows(
                    db, code, rows, source_high_water=high_water,
                    stored_disease_ids=context["stored_disease_ids"],
                )
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
