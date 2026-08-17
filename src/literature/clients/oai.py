"""Bounded OAI-PMH metadata discovery for official public-health guidance."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import httpx

from .base import LiteratureHttpClient


_OAI_NS = "http://www.openarchives.org/OAI/2.0/"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RESPONSE_BYTES = 4_000_000


@dataclass(frozen=True, slots=True)
class OaiIncrementalResult:
    records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _iso(value: Any) -> datetime | None:
    text = _compact(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_page(body: bytes) -> tuple[list[dict[str, Any]], str | None]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError("Official-guidance OAI endpoint returned invalid XML") from exc
    error = root.find(f"{{{_OAI_NS}}}error")
    if error is not None:
        code = _compact(error.attrib.get("code")) or "unknown"
        if code == "noRecordsMatch":
            return [], None
        raise ValueError(f"Official-guidance OAI error {code}: {_compact(error.text)[:300]}")

    records: list[dict[str, Any]] = []
    for node in root.findall(f".//{{{_OAI_NS}}}record"):
        header = node.find(f"{{{_OAI_NS}}}header")
        if header is None or header.attrib.get("status") == "deleted":
            continue
        identifier = _compact(header.findtext(f"{{{_OAI_NS}}}identifier"))
        datestamp = _compact(header.findtext(f"{{{_OAI_NS}}}datestamp"))
        if not identifier or _iso(datestamp) is None:
            continue
        fields: dict[str, list[str]] = {}
        metadata = node.find(f"{{{_OAI_NS}}}metadata")
        if metadata is not None:
            for element in metadata.iter():
                if not element.tag.startswith(f"{{{_DC_NS}}}"):
                    continue
                name = element.tag.rsplit("}", 1)[-1].lower()
                value = _compact(element.text)
                if value:
                    fields.setdefault(name, []).append(value)
        records.append({
            "oai_identifier": identifier,
            "datestamp": datestamp,
            "sets": [
                value
                for element in header.findall(f"{{{_OAI_NS}}}setSpec")
                if (value := _compact(element.text))
            ],
            "fields": fields,
        })
    token_node = root.find(f".//{{{_OAI_NS}}}resumptionToken")
    token = _compact(token_node.text) if token_node is not None else ""
    return records, token or None


class OfficialGuidanceOaiClient(LiteratureHttpClient):
    """Read Dublin Core metadata only; never downloads linked documents."""

    def __init__(
        self,
        *,
        endpoint: str,
        contact_email: str,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname != "iris.who.int"
            or parsed.username
            or parsed.password
            or parsed.path.rstrip("/") != "/server/oai/request"
        ):
            raise ValueError("Official guidance OAI endpoint must be the reviewed WHO IRIS HTTPS endpoint")
        contact = contact_email.strip() or "research-radar@globalinfectiousdisease.com"
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{contact})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.endpoint = endpoint
        self.transport = transport

    async def _get_page(self, client: httpx.AsyncClient, params: dict[str, str]) -> bytes:
        attempts = max(1, self.retries)
        for attempt in range(attempts):
            response: httpx.Response | None = None
            try:
                response = await client.get(self.endpoint, params=params, timeout=self.timeout_seconds)
                if response.status_code != 200:
                    response.raise_for_status()
                body = response.content
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise ValueError("Official-guidance OAI response exceeded the 4 MB metadata limit")
                return body
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                status = response.status_code if response is not None else None
                if attempt + 1 >= attempts or (status is not None and status not in _RETRYABLE_STATUS_CODES):
                    raise RuntimeError(f"Official-guidance OAI request failed: {exc}") from exc
                await asyncio.sleep(min(10.0, 0.5 * (2**attempt)))
        raise AssertionError("unreachable")

    async def fetch_incremental(
        self,
        *,
        since: datetime,
        until: datetime,
        max_records: int,
        checkpoint: dict[str, Any] | None = None,
    ) -> OaiIncrementalResult:
        if until < since:
            raise ValueError("Official guidance OAI until must not precede since")
        limit = max(1, int(max_records))
        previous = checkpoint if isinstance(checkpoint, dict) else {}
        continuing = bool(previous.get("truncated"))
        window_from = (
            _compact(previous.get("from")) if continuing else ""
        ) or since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        window_until = (
            _compact(previous.get("until")) if continuing else ""
        ) or until.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        request_token = (_compact(previous.get("request_token")) or None) if continuing else None
        already_consumed = {
            str(value) for value in previous.get("consumed_on_page") or [] if value
        } if continuing else set()
        returned: list[dict[str, Any]] = []
        records_seen = 0
        last_datestamp: str | None = None

        async with httpx.AsyncClient(
            follow_redirects=False,
            transport=self.transport,
            headers={"User-Agent": self.user_agent, "Accept": "application/xml, text/xml"},
        ) as client:
            while len(returned) < limit:
                page_request_token = request_token
                params = (
                    {"verb": "ListRecords", "resumptionToken": request_token}
                    if request_token
                    else {
                        "verb": "ListRecords",
                        "metadataPrefix": "oai_dc",
                        "from": window_from,
                        "until": window_until,
                    }
                )
                page, next_token = _parse_page(await self._get_page(client, params))
                records_seen += len(page)
                pending = [row for row in page if row["oai_identifier"] not in already_consumed]
                remaining = limit - len(returned)
                returned.extend(pending[:remaining])
                if returned:
                    last_datestamp = max(str(row["datestamp"]) for row in returned)

                if len(pending) > remaining:
                    consumed = [row["oai_identifier"] for row in page if row["oai_identifier"] in already_consumed]
                    consumed.extend(row["oai_identifier"] for row in pending[:remaining])
                    return OaiIncrementalResult(returned, {
                        "strategy": "oai-pmh-datestamp",
                        "from": window_from,
                        "until": window_until,
                        "request_token": page_request_token,
                        "consumed_on_page": consumed,
                        "truncated": True,
                        "records_seen": records_seen,
                        "records_returned": len(returned),
                        "through_datestamp": last_datestamp,
                    })
                if not next_token:
                    return OaiIncrementalResult(returned, {
                        "strategy": "oai-pmh-datestamp",
                        "from": window_from,
                        "until": window_until,
                        "request_token": None,
                        "consumed_on_page": [],
                        "truncated": False,
                        "records_seen": records_seen,
                        "records_returned": len(returned),
                        "through_datestamp": last_datestamp,
                    })
                request_token = next_token
                already_consumed = set()

        raise AssertionError("unreachable")


__all__ = ["OaiIncrementalResult", "OfficialGuidanceOaiClient"]
