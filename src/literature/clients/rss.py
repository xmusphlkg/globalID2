"""Bounded publisher RSS/Atom discovery with conditional request checkpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import httpx


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_FEED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class RssIncrementalResult:
    """New feed entries plus state that is safe to persist after DB commit."""

    records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _FeedResponse:
    status_code: int
    body: bytes
    etag: str | None
    last_modified: str | None
    final_url: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _child_text(node: ET.Element, names: set[str]) -> str | None:
    for child in node:
        if _local_name(child.tag) in names:
            text = _compact_text("".join(child.itertext()))
            if text:
                return text
    return None


def _entry_link(node: ET.Element) -> str | None:
    for child in node:
        if _local_name(child.tag) != "link":
            continue
        href = _compact_text(child.attrib.get("href"))
        relation = _compact_text(child.attrib.get("rel")).lower()
        if href and relation in {"", "alternate"}:
            return href
        text = _compact_text("".join(child.itertext()))
        if text:
            return text
    return None


def _iso_datetime(value: Any) -> datetime | None:
    text = _compact_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _doi_from(values: list[Any]) -> str | None:
    for value in values:
        match = _DOI_RE.search(str(value or ""))
        if match:
            return match.group(0).rstrip(".,;:)]}").lower()
    return None


def _stable_entry_id(*, doi: str | None, guid: str | None, link: str | None, title: str, published_at: str | None) -> str:
    if doi:
        return f"doi:{doi}"
    if guid:
        return f"guid:{hashlib.sha256(guid.encode('utf-8')).hexdigest()}"
    if link:
        return f"link:{hashlib.sha256(link.encode('utf-8')).hexdigest()}"
    identity = f"{title.casefold()}|{published_at or ''}"
    return f"fallback:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _validate_feed(feed: dict[str, Any]) -> dict[str, Any]:
    feed_id = _compact_text(feed.get("feed_id"))
    url = _compact_text(feed.get("url"))
    journal = _compact_text(feed.get("journal"))
    allowed_hosts = sorted({
        _compact_text(value).lower().rstrip(".")
        for value in feed.get("allowed_hosts") or []
        if _compact_text(value)
    })
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not _FEED_ID_RE.fullmatch(feed_id):
        raise ValueError(f"Invalid publisher feed_id: {feed_id!r}")
    if parsed.scheme.lower() != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError(f"Publisher feed {feed_id!r} must use an unauthenticated HTTPS URL")
    if not allowed_hosts or hostname not in allowed_hosts:
        raise ValueError(f"Publisher feed {feed_id!r} URL host is not in allowed_hosts")
    if not journal:
        raise ValueError(f"Publisher feed {feed_id!r} is missing its trusted journal label")
    issn_value = feed.get("issn") or []
    issn = [str(value).strip().upper() for value in (issn_value if isinstance(issn_value, list) else [issn_value]) if value]
    return {
        "feed_id": feed_id,
        "url": url,
        "allowed_hosts": allowed_hosts,
        "journal": journal,
        "issn": issn,
        "publisher": _compact_text(feed.get("publisher")) or None,
    }


def _parse_feed(body: bytes, feed: dict[str, Any], *, retrieved_at: datetime) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"Publisher feed {feed['feed_id']!r} returned invalid XML") from exc
    nodes = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    records: dict[str, dict[str, Any]] = {}
    for node in nodes:
        title = _child_text(node, {"title"})
        if not title:
            continue
        link = _entry_link(node)
        guid = _child_text(node, {"guid", "id"})
        identifier = _child_text(node, {"identifier", "doi"})
        published = _child_text(node, {"pubdate", "published", "updated", "date"})
        published_at = _iso_datetime(published)
        doi = _doi_from([identifier, guid, link])
        entry_id = _stable_entry_id(
            doi=doi,
            guid=guid,
            link=link,
            title=title,
            published_at=published_at.isoformat() if published_at else None,
        )
        # Deliberately do not read description, summary, content, enclosures,
        # or linked documents. RSS is a discovery-only metadata source.
        records.setdefault(entry_id, {
            "feed_id": feed["feed_id"],
            "feed_url": feed["url"],
            "entry_id": entry_id,
            "guid": guid,
            "title": title,
            "link": link,
            "doi": doi,
            "published_at": published_at.isoformat() if published_at else None,
            "retrieved_at": retrieved_at.isoformat(),
            "journal": feed["journal"],
            "issn": feed["issn"],
            "publisher": feed["publisher"],
        })
    floor = "0000-00-00T00:00:00+00:00"
    return sorted(records.values(), key=lambda item: (item.get("published_at") or floor, item["entry_id"]))


class PublisherRssClient:
    """Poll a static feed whitelist without downloading article full text."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        max_feed_bytes: int = 2_000_000,
        seen_id_limit: int = 2_000,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.max_feed_bytes = max(1_024, max_feed_bytes)
        self.seen_id_limit = max(100, seen_id_limit)
        self.transport = transport

    async def fetch_incremental(
        self,
        *,
        feeds: list[dict[str, Any]],
        checkpoint: dict[str, Any] | None,
        max_records: int,
        concurrency: int = 3,
        now: datetime | None = None,
    ) -> RssIncrementalResult:
        retrieved_at = now or datetime.now(timezone.utc)
        trusted_feeds = [_validate_feed(feed) for feed in feeds if feed.get("enabled", True)]
        feed_ids = [feed["feed_id"] for feed in trusted_feeds]
        if len(set(feed_ids)) != len(feed_ids):
            raise ValueError("Publisher RSS whitelist contains duplicate feed_id values")
        previous = checkpoint if isinstance(checkpoint, dict) else {}
        previous_feeds = previous.get("feeds") if isinstance(previous.get("feeds"), dict) else {}
        previous_global_ids = [str(value) for value in previous.get("seen_record_ids") or [] if value]
        globally_seen = set(previous_global_ids)
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async with httpx.AsyncClient(follow_redirects=True, transport=self.transport) as client:
            async def fetch(feed: dict[str, Any]):
                state = previous_feeds.get(feed["feed_id"])
                state = state if isinstance(state, dict) else {}
                headers = {
                    "User-Agent": self.user_agent,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                }
                if state.get("etag"):
                    headers["If-None-Match"] = str(state["etag"])
                if state.get("last_modified"):
                    headers["If-Modified-Since"] = str(state["last_modified"])
                async with semaphore:
                    response = await self._request_feed(client, feed, headers=headers)
                return feed, state, response

            responses = await asyncio.gather(*(fetch(feed) for feed in trusted_feeds), return_exceptions=True)

        next_states: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        entry_groups: dict[str, dict[str, Any]] = {}
        feed_unseen_ids: dict[str, set[str]] = {}
        records_seen = 0
        not_modified = 0
        modified = 0
        duplicates = 0
        for feed, response in zip(trusted_feeds, responses):
            feed_id = feed["feed_id"]
            old_state = previous_feeds.get(feed_id)
            old_state = old_state if isinstance(old_state, dict) else {}
            if isinstance(response, Exception):
                errors.append({"feed_id": feed_id, "error": str(response)[:500]})
                next_states[feed_id] = {
                    **old_state,
                    "url": feed["url"],
                    "status": "error",
                    "last_checked_at": retrieved_at.isoformat(),
                }
                continue
            _, _, feed_response = response
            if feed_response.status_code == 304:
                not_modified += 1
                next_states[feed_id] = {
                    **old_state,
                    "url": feed["url"],
                    "etag": feed_response.etag or old_state.get("etag"),
                    "last_modified": feed_response.last_modified or old_state.get("last_modified"),
                    "status": "not_modified",
                    "last_checked_at": retrieved_at.isoformat(),
                    "last_success_at": retrieved_at.isoformat(),
                    "unseen_remaining": 0,
                }
                continue
            modified += 1
            try:
                parsed = _parse_feed(feed_response.body, feed, retrieved_at=retrieved_at)
            except ValueError as exc:
                errors.append({"feed_id": feed_id, "error": str(exc)[:500]})
                next_states[feed_id] = {
                    **old_state,
                    "url": feed["url"],
                    "status": "error",
                    "last_checked_at": retrieved_at.isoformat(),
                }
                continue
            records_seen += len(parsed)
            old_seen_ids = [str(value) for value in old_state.get("seen_ids") or [] if value]
            old_ids = set(old_seen_ids)
            unseen_ids: set[str] = set()
            for record in parsed:
                entry_id = record["entry_id"]
                if entry_id in old_ids or entry_id in globally_seen:
                    old_ids.add(entry_id)
                    continue
                unseen_ids.add(entry_id)
                group = entry_groups.get(entry_id)
                if group is None:
                    entry_groups[entry_id] = {"record": record, "origins": [(feed_id, entry_id)]}
                else:
                    group["origins"].append((feed_id, entry_id))
                    duplicates += 1
            feed_unseen_ids[feed_id] = unseen_ids
            next_states[feed_id] = {
                **old_state,
                "url": feed["url"],
                "_candidate_etag": feed_response.etag,
                "_candidate_last_modified": feed_response.last_modified,
                "seen_ids": list(dict.fromkeys([*old_seen_ids, *sorted(old_ids)]))[-self.seen_id_limit :],
                "status": "modified",
                "last_checked_at": retrieved_at.isoformat(),
                "last_success_at": retrieved_at.isoformat(),
            }

        ordered_groups = sorted(
            entry_groups.values(),
            key=lambda group: (
                group["record"].get("published_at") or "0000-00-00T00:00:00+00:00",
                group["record"]["entry_id"],
            ),
        )
        selected = ordered_groups[: max(0, max_records)]
        selected_ids: list[str] = []
        for group in selected:
            selected_ids.append(group["record"]["entry_id"])
            group["record"]["feed_origins"] = sorted({origin[0] for origin in group["origins"]})
            for feed_id, entry_id in group["origins"]:
                state = next_states[feed_id]
                state["seen_ids"] = list(dict.fromkeys([*state.get("seen_ids", []), entry_id]))[-self.seen_id_limit :]
                feed_unseen_ids.get(feed_id, set()).discard(entry_id)

        truncated = len(ordered_groups) > len(selected)
        for feed_id, state in next_states.items():
            remaining = len(feed_unseen_ids.get(feed_id, set()))
            state["unseen_remaining"] = remaining
            candidate_etag = state.pop("_candidate_etag", None)
            candidate_modified = state.pop("_candidate_last_modified", None)
            if remaining == 0 and state.get("status") == "modified":
                state["etag"] = candidate_etag
                state["last_modified"] = candidate_modified
            elif remaining:
                # Do not commit validators until every currently visible entry
                # has crossed the global record cap; otherwise the next poll
                # could receive 304 and permanently skip the remainder.
                state.pop("etag", None)
                state.pop("last_modified", None)

        next_global_ids = list(dict.fromkeys([*previous_global_ids, *selected_ids]))[-self.seen_id_limit :]
        return RssIncrementalResult(
            records=[group["record"] for group in selected],
            checkpoint={
                "strategy": "conditional-get-stable-id-v1",
                "feeds": next_states,
                "seen_record_ids": next_global_ids,
                "feed_count": len(trusted_feeds),
                "feeds_modified": modified,
                "feeds_not_modified": not_modified,
                "feed_errors": errors,
                "records_seen": records_seen,
                "records_returned": len(selected),
                "duplicates": duplicates,
                "truncated": truncated,
                "max_records": max_records,
            },
        )

    async def _request_feed(
        self,
        client: httpx.AsyncClient,
        feed: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> _FeedResponse:
        attempts = max(1, self.retries)
        for attempt in range(attempts):
            response: httpx.Response | None = None
            try:
                async with client.stream(
                    "GET",
                    feed["url"],
                    headers=headers,
                    timeout=self.timeout_seconds,
                ) as response:
                    final_url = str(response.url)
                    final_parts = urlsplit(final_url)
                    final_host = (final_parts.hostname or "").lower().rstrip(".")
                    if final_parts.scheme.lower() != "https":
                        raise ValueError(f"Publisher feed {feed['feed_id']!r} redirected outside HTTPS")
                    if final_host not in feed["allowed_hosts"]:
                        raise ValueError(f"Publisher feed {feed['feed_id']!r} redirected outside allowed_hosts")
                    if response.status_code == 304:
                        return _FeedResponse(
                            status_code=304,
                            body=b"",
                            etag=response.headers.get("ETag"),
                            last_modified=response.headers.get("Last-Modified"),
                            final_url=final_url,
                        )
                    response.raise_for_status()
                    declared_size = int(response.headers.get("Content-Length") or 0)
                    if declared_size > self.max_feed_bytes:
                        raise ValueError(f"Publisher feed {feed['feed_id']!r} exceeds the configured size limit")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_feed_bytes:
                            raise ValueError(f"Publisher feed {feed['feed_id']!r} exceeds the configured size limit")
                    return _FeedResponse(
                        status_code=response.status_code,
                        body=bytes(body),
                        etag=response.headers.get("ETag"),
                        last_modified=response.headers.get("Last-Modified"),
                        final_url=final_url,
                    )
            except (httpx.HTTPError, ValueError):
                status_code = response.status_code if response is not None else None
                retryable = isinstance(response, type(None)) or status_code in _RETRYABLE_STATUS_CODES
                if not retryable or attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(min(30.0, 0.5 * (2**attempt)))
        raise RuntimeError("unreachable")


__all__ = ["PublisherRssClient", "RssIncrementalResult"]
