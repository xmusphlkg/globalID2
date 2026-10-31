"""Source windows are planned from task options and already stored periods."""
from datetime import datetime
from src.core.database import get_db


def history_start_year(task, updater, default_start_year=None):
    current = datetime.now().year
    value = default_start_year if default_start_year is not None else (task.input_data or {}).get("start_year")
    try:
        value = int(value if value is not None else updater.full_history_start_year)
    except (TypeError, ValueError):
        value = int(updater.full_history_start_year)
    return max(1900, min(value, current))


async def plan_months(task, updater, *, force, fill_missing, start_year=None):
    if force:
        return updater.history_months(start_year=start_year or history_start_year(task, updater))
    if not fill_missing:
        return None
    now = datetime.now()
    months = set()
    for offset in range(max(1, updater.refresh_recent_months)):
        index = now.year * 12 + now.month - 1 - offset
        months.add((index // 12, index % 12 + 1))
    async with get_db() as db:
        existing = await updater.get_db_months(db)
    for year in range(start_year or history_start_year(task, updater), now.year + 1):
        for month in range(1, (now.month if year == now.year else 12) + 1):
            if (year, month) not in existing:
                months.add((year, month))
    return sorted(months)


def chunk_months(values, chunk_size):
    return [values] if chunk_size <= 0 else [values[index:index + chunk_size] for index in range(0, len(values), chunk_size)]
