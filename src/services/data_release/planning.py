"""Plan bounded publication work from visible, transactional change markers."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from src.core.country_library import expand_country_scopes


def plan_changes(rows: list[Any], available_codes: set[str], *, max_countries: int = 8, full: bool = False) -> dict:
    """Keep a legacy parent marker atomic; precise markers never fan out.

    Repeated markers for a scope coalesce into one unit. Only the exact selected
    IDs may be acknowledged: concurrent commits and deferred groups stay pending.
    A legacy group larger than the budget runs alone, rather than acknowledging
    its parent after publishing only a subset of its children.
    """
    if max_countries < 1:
        raise ValueError("Publication country budget must be positive")
    groups: OrderedDict[tuple[str, str], list[Any]] = OrderedDict()
    for row in rows:
        groups.setdefault((row.line, row.scope_key), []).append(row)
    selected_ids, countries, changes = [], set(), []
    deferred, oversized, continuation = 0, [], []
    for (line, scope), members in groups.items():
        if line != "surveillance":
            selected_ids.extend(row.id for row in members)
            continue
        if scope not in available_codes:
            deferred += len(members)
            continue
        precise = all(
            isinstance(getattr(row, "change_set", None), dict)
            and row.change_set.get("version") == 1
            and row.change_set.get("precise") is True
            for row in members
        )
        affected = {scope} if precise else set(expand_country_scopes([scope], available_codes=available_codes)) & available_codes
        if not full and countries and affected - countries and len(countries | affected) > max_countries:
            deferred += len(members)
            continuation.extend(row.id for row in members)
            continue
        countries.update(affected)
        selected_ids.extend(row.id for row in members)
        if len(affected) > max_countries:
            oversized.append(scope)
        for row in members:
            changes.append({"id": row.id, "scope": scope, "change_set": getattr(row, "change_set", None)})
    return {
        "version": 1, "country_codes": sorted(countries),
        "dirty_ids": sorted(selected_ids), "changes": changes,
        "deferred_change_count": deferred, "country_budget": max_countries,
        "oversized_legacy_scopes": oversized,
        "continuation_dirty_ids": sorted(continuation),
    }
