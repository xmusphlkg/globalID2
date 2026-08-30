"""Bounded discovery from the official bioRxiv/medRxiv public API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .base import LiteratureHttpClient


@dataclass(frozen=True, slots=True)
class PreprintResult:
    records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


def _date(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).date().isoformat()


def _parse_checkpoint_date(value: Any, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class BiorxivClient(LiteratureHttpClient):
    """Fetch public preprint metadata; never requests JATS, HTML, or PDFs."""

    BASE_URL = "https://api.biorxiv.org"
    SERVERS = ("biorxiv", "medrxiv")
    # Official details endpoints currently return 30 records per page.
    PAGE_SIZE = 30
    MAX_RECORDS = 500

    def __init__(
        self,
        *,
        contact_email: str,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact_email.strip()})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.transport = transport

    async def fetch_recent(
        self,
        *,
        since: datetime,
        until: datetime,
        max_records: int,
        servers: tuple[str, ...] = SERVERS,
        checkpoint: dict[str, Any] | None = None,
    ) -> PreprintResult:
        invalid = sorted(set(servers) - set(self.SERVERS))
        if invalid:
            raise ValueError(f"Unsupported preprint server: {invalid[0]}")
        limit = min(self.MAX_RECORDS, max(0, max_records))
        previous = checkpoint if isinstance(checkpoint, dict) and checkpoint.get("truncated") else {}
        range_since = _parse_checkpoint_date(previous.get("from_date"), since)
        range_until = _parse_checkpoint_date(previous.get("through_date"), until)
        previous_servers = previous.get("servers") if isinstance(previous.get("servers"), dict) else {}
        per_server_limit = max(1, (limit + max(1, len(servers)) - 1) // max(1, len(servers)))
        records: list[dict[str, Any]] = []
        states: dict[str, dict[str, Any]] = {}
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True, transport=self.transport) as client:
            for server in servers:
                old_state = previous_servers.get(server)
                old_state = old_state if isinstance(old_state, dict) else {}
                try:
                    cursor = max(0, int(old_state.get("next_cursor") or 0))
                except (TypeError, ValueError):
                    cursor = 0
                server_records: list[dict[str, Any]] = []
                pages = 0
                total: int | None = None
                locally_truncated = False
                while len(server_records) < per_server_limit and len(records) + len(server_records) < limit:
                    payload = await self.get_json(
                        client,
                        f"/details/{server}/{_date(range_since)}/{_date(range_until)}/{cursor}",
                        params={},
                    )
                    pages += 1
                    messages = [row for row in payload.get("messages") or [] if isinstance(row, dict)]
                    if messages and total is None:
                        try:
                            total = max(0, int(messages[0].get("total") or 0))
                        except (TypeError, ValueError):
                            total = None
                    rows = [row for row in payload.get("collection") or [] if isinstance(row, dict)]
                    remaining = min(per_server_limit - len(server_records), limit - len(records) - len(server_records))
                    selected = rows[:remaining]
                    server_records.extend({**row, "server": server} for row in selected)
                    cursor += len(selected)
                    locally_truncated = len(selected) < len(rows)
                    if (
                        len(rows) < self.PAGE_SIZE
                        or not rows
                        or locally_truncated
                        or (total is not None and cursor >= total)
                    ):
                        break
                    if total is None and len(rows) == self.PAGE_SIZE and len(server_records) >= per_server_limit:
                        locally_truncated = True
                records.extend(server_records)
                states[server] = {
                    "pages_fetched": pages,
                    "records_returned": len(server_records),
                    "records_total": total,
                    "next_cursor": cursor,
                    "truncated": locally_truncated or bool(total is not None and cursor < total),
                }
                if len(records) >= limit:
                    break
        return PreprintResult(
            records=records[:limit],
            checkpoint={
                "provider": "biorxiv-api",
                "strategy": "date-cursor-v1",
                "from_date": _date(range_since),
                "through_date": _date(range_until),
                "servers": states,
                "records_returned": min(len(records), limit),
                "truncated": any(state["truncated"] for state in states.values()),
                "max_records": limit,
            },
        )


__all__ = ["BiorxivClient", "PreprintResult"]
