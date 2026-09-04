"""Bounded PubMed discovery through NCBI E-utilities.

The client uses metadata endpoints only. It does not request article full text,
HTML, PDFs, or publisher content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree

import httpx

from .base import AsyncRequestLimiter, LiteratureHttpClient


@dataclass(frozen=True, slots=True)
class PubMedResult:
    records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


def _date(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).date().isoformat()


def _clean_term(value: Any) -> str:
    return " ".join(str(value or "").replace('"', " ").split())


def _xml_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _journal_query(journals: list[dict[str, Any]]) -> str:
    terms: list[str] = []
    seen: set[str] = set()
    for journal in journals:
        issn = _clean_term(journal.get("issn"))
        if issn and issn not in seen:
            terms.append(f'"{issn}"[ISSN]')
            seen.add(issn)
        name = _clean_term(journal.get("name"))
        if name:
            key = name.casefold()
            if key not in seen:
                terms.append(f'"{name}"[Journal]')
                seen.add(key)
    if not terms:
        return "journal article[Publication Type]"
    return f"({' OR '.join(terms)}) AND journal article[Publication Type]"


class PubMedClient(LiteratureHttpClient):
    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    MAX_PAGE_SIZE = 100

    def __init__(
        self,
        *,
        contact_email: str,
        api_key: str = "",
        tool: str = "GIDSResearchRadar",
        timeout_seconds: float = 30.0,
        retries: int = 3,
        min_interval_seconds: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        email = contact_email.strip() or "research-radar@globalinfectiousdisease.com"
        super().__init__(
            user_agent=f"GIDS-Research-Radar/1.0 (mailto:{email})",
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        self.contact_email = email
        self.api_key = api_key.strip()
        self.tool = _clean_term(tool) or "GIDSResearchRadar"
        default_interval = 0.11 if self.api_key else 0.34
        self.limiter = AsyncRequestLimiter(
            default_interval if min_interval_seconds is None else min_interval_seconds
        )
        self.transport = transport

    def _params(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = {
            **params,
            "tool": self.tool,
            "email": self.contact_email,
        }
        if self.api_key:
            merged["api_key"] = self.api_key
        return merged

    async def _get_json(self, client: httpx.AsyncClient, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        await self.limiter.wait()
        return await self.get_json(client, url, params=self._params(params))

    async def _get_xml(self, client: httpx.AsyncClient, url: str, *, params: dict[str, Any]) -> str:
        await self.limiter.wait()
        return await self.get_text(
            client,
            url,
            params=self._params(params),
            accept="application/xml,text/xml",
        )

    async def fetch_incremental(
        self,
        *,
        journals: list[dict[str, Any]],
        since: datetime,
        until: datetime,
        max_records: int,
        checkpoint: dict[str, Any] | None = None,
    ) -> PubMedResult:
        """Fetch recent PubMed records for the curated journal registry."""

        limit = max(0, int(max_records))
        if limit == 0:
            return PubMedResult([], {"provider": "pubmed", "records_returned": 0})
        retstart = 0
        if isinstance(checkpoint, dict) and checkpoint.get("truncated"):
            try:
                retstart = max(0, int(checkpoint.get("next_retstart") or 0))
            except (TypeError, ValueError):
                retstart = 0
        term = _journal_query(journals)
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True, transport=self.transport) as client:
            ids, searched, total = await self._search_ids(
                client,
                term=term,
                since=since,
                until=until,
                max_records=limit,
                retstart=retstart,
            )
            records = await self._summaries(client, ids)
        next_retstart = retstart + searched
        return PubMedResult(
            records=records[:limit],
            checkpoint={
                "provider": "pubmed",
                "strategy": "esearch-esummary-pdat-v1",
                "from_publication_date": _date(since),
                "through_publication_date": _date(until),
                "records_total": total,
                "records_seen": searched,
                "records_returned": len(records[:limit]),
                "next_retstart": next_retstart,
                "truncated": total is not None and next_retstart < total and len(records) >= limit,
                "max_records": limit,
            },
        )

    async def search_recent(
        self,
        *,
        query: str,
        since: datetime,
        until: datetime,
        max_records: int,
    ) -> list[dict[str, Any]]:
        term = f"({query}) AND journal article[Publication Type]" if query else "journal article[Publication Type]"
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True, transport=self.transport) as client:
            ids, _, _ = await self._search_ids(
                client,
                term=term,
                since=since,
                until=until,
                max_records=max_records,
                retstart=0,
            )
            return await self._summaries(client, ids)

    async def fetch_abstracts(self, pmids: list[str], *, batch_size: int = MAX_PAGE_SIZE) -> dict[str, dict[str, Any]]:
        """Fetch PubMed XML abstracts for known PMIDs without requesting full text."""

        result: dict[str, dict[str, Any]] = {}
        unique_pmids = list(dict.fromkeys(str(value).strip() for value in pmids if str(value).strip()))
        if not unique_pmids:
            return result
        size = max(1, min(self.MAX_PAGE_SIZE, int(batch_size or self.MAX_PAGE_SIZE)))
        async with httpx.AsyncClient(base_url=self.BASE_URL, follow_redirects=True, transport=self.transport) as client:
            for offset in range(0, len(unique_pmids), size):
                batch = unique_pmids[offset : offset + size]
                payload = await self._get_xml(
                    client,
                    "/efetch.fcgi",
                    params={
                        "db": "pubmed",
                        "retmode": "xml",
                        "id": ",".join(batch),
                    },
                )
                result.update(_parse_abstracts_xml(payload))
        return result

    async def _search_ids(
        self,
        client: httpx.AsyncClient,
        *,
        term: str,
        since: datetime,
        until: datetime,
        max_records: int,
        retstart: int,
    ) -> tuple[list[str], int, int | None]:
        ids: list[str] = []
        total: int | None = None
        searched = 0
        while len(ids) < max_records:
            retmax = min(self.MAX_PAGE_SIZE, max_records - len(ids))
            payload = await self._get_json(
                client,
                "/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "retmode": "json",
                    "term": term,
                    "datetype": "pdat",
                    "mindate": _date(since),
                    "maxdate": _date(until),
                    "sort": "pub date",
                    "retstart": retstart + searched,
                    "retmax": retmax,
                },
            )
            result = payload.get("esearchresult") or {}
            if total is None:
                try:
                    total = max(0, int(result.get("count") or 0))
                except (TypeError, ValueError):
                    total = None
            page_ids = [str(value) for value in result.get("idlist") or [] if value]
            ids.extend(page_ids)
            searched += len(page_ids)
            if not page_ids or len(page_ids) < retmax:
                break
            if total is not None and retstart + searched >= total:
                break
        return ids[:max_records], searched, total

    async def _summaries(self, client: httpx.AsyncClient, ids: list[str]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for offset in range(0, len(ids), self.MAX_PAGE_SIZE):
            batch = ids[offset : offset + self.MAX_PAGE_SIZE]
            if not batch:
                continue
            payload = await self._get_json(
                client,
                "/esummary.fcgi",
                params={
                    "db": "pubmed",
                    "retmode": "json",
                    "id": ",".join(batch),
                },
            )
            result = payload.get("result") or {}
            for uid in result.get("uids") or batch:
                record = result.get(str(uid))
                if isinstance(record, dict):
                    records.append(record)
        return records


def _parse_abstracts_xml(payload: str) -> dict[str, dict[str, Any]]:
    root = ElementTree.fromstring(payload)
    records: dict[str, dict[str, Any]] = {}
    for article in root.findall(".//PubmedArticle"):
        pmid = _xml_text(article.find("./MedlineCitation/PMID"))
        if not pmid:
            continue
        abstract = article.find("./MedlineCitation/Article/Abstract")
        sections: list[dict[str, str | None]] = []
        for element in abstract.findall("./AbstractText") if abstract is not None else []:
            text = _xml_text(element)
            if not text:
                continue
            label = _clean_term(element.attrib.get("Label") or element.attrib.get("NlmCategory"))
            section_text = f"{label}: {text}" if label else text
            sections.append({
                "label": label or None,
                "text": text,
                "section_text": section_text,
            })
        abstract_text = " ".join(str(section["section_text"]) for section in sections).strip()
        if abstract_text:
            records[pmid] = {
                "pmid": pmid,
                "abstractText": abstract_text,
                "abstractSections": sections,
                "source": "pubmed-efetch",
            }
    return records


__all__ = ["PubMedClient", "PubMedResult"]
