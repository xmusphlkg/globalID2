"""Rebuild JSON publication scopes from authoritative database change rows."""
from __future__ import annotations

from sqlalchemy import select

from src.core.country_library import get_country_bootstrap_config
from src.domain import Country
from src.domain.collection import PublishDirty

from .planning import plan_changes


async def validate_data_selection(db, *, country_codes: list[str], dirty_ids: list[int], expected_task_uuid: str | None = None) -> dict:
    """Return the exact plan only when IDs and country scopes agree with the DB.

    Published markers remain readable for their own task: a successful pointer switch
    followed by a failed finalization can retry the same immutable selection. This helper
    never expands the selection to newly arriving dirty rows and never trusts a
    plan supplied in task input. The publisher separately fences obsolete bases.
    """
    if (not dirty_ids or any(type(value) is not int or value <= 0 for value in dirty_ids)
            or len(set(dirty_ids)) != len(dirty_ids)):
        raise ValueError("JSON publication requires unique positive dirty IDs")
    if (not country_codes or any(not isinstance(code, str) or not code
            or code != code.strip().upper() for code in country_codes)
            or len(set(country_codes)) != len(country_codes)):
        raise ValueError("JSON publication requires unique canonical country scopes")

    rows = list((await db.execute(select(PublishDirty).where(
        PublishDirty.id.in_(dirty_ids),
    ).order_by(PublishDirty.id))).scalars().all())
    missing = sorted(set(dirty_ids) - {row.id for row in rows})
    if missing:
        raise ValueError(f"Selected JSON dirty IDs are missing: {missing}")
    if any(row.line != "surveillance" for row in rows):
        raise ValueError("JSON publication requires selected surveillance change IDs")
    if expected_task_uuid is not None and any(
        row.published_at is not None and row.published_task_uuid != expected_task_uuid
        for row in rows
    ):
        raise ValueError("Selected JSON changes were already published by another task")

    countries = (await db.execute(select(Country.code, Country.is_active))).all()
    available = {
        str(code).strip().upper() for code, active in countries
        if active and get_country_bootstrap_config(code).get("public_release_enabled", True) is not False
    }
    plan = plan_changes(rows, available, full=True, max_countries=500)
    if plan["dirty_ids"] != sorted(dirty_ids):
        excluded = sorted(set(dirty_ids) - set(plan["dirty_ids"]))
        raise ValueError(f"Selected JSON changes have disabled, unavailable or unknown country scopes: {excluded}")
    if plan["country_codes"] != sorted(country_codes):
        raise ValueError(
            "Selected JSON country scopes do not match the database changes: "
            f"expected {plan['country_codes']}, received {sorted(country_codes)}"
        )
    plan["available_country_codes"] = sorted(available)
    return plan
