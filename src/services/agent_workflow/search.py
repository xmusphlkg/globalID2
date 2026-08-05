"""Search and evidence formatting adapters for agent workflows."""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from src.core import get_logger
from src.services.agent_workflow.helpers import compact_text, extract_keywords, stable_hash, unique_items
from src.services.agent_workflow_types import EvidenceRef

logger = get_logger(__name__)

WEB_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
WEB_USER_AGENT = "GlobalID-AgentWorkflow/1.0 (+https://globalid.local)"


def build_search_queries(queries: list[str], *, prompt: str, max_rounds: int) -> list[str]:
    base = unique_items([compact_text(query, 120) for query in queries if query])
    if not base:
        base = [" ".join(extract_keywords(prompt, 6)) or prompt[:120]]
    expanded = list(base)
    keywords = extract_keywords(prompt, 6)
    if keywords:
        expanded.append(" ".join(keywords))
        expanded.append(f"{' '.join(keywords)} site:who.int")
        expanded.append(f"{' '.join(keywords)} site:cdc.gov")
        expanded.append(f"{' '.join(keywords)} site:nih.gov")
    if max_rounds <= 1:
        return unique_items(expanded)[:2]
    return unique_items(expanded)[: max(2, max_rounds * 2)]


def fetch_web_page(session: Any, url: str) -> tuple[str, str, str]:
    try:
        response = session.get(url, timeout=20, allow_redirects=True)
        response.raise_for_status()
    except Exception:
        return "", "", url
    try:
        soup = BeautifulSoup(response.text, "html.parser")
        title = ""
        if soup.title and soup.title.string:
            title = compact_text(soup.title.string, 220)
        text_blocks: list[str] = []
        for tag in soup.select("article, main, p, li, h1, h2, h3"):
            text = compact_text(tag.get_text(" ", strip=True), 220)
            if text:
                text_blocks.append(text)
            if len(" ".join(text_blocks)) > 1200:
                break
        snippet = compact_text(" ".join(text_blocks), 900)
        return title, snippet, response.url or url
    except Exception:
        return "", "", response.url or url


def guess_source_type(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "who.int" in host:
        return "who"
    if "cdc.gov" in host:
        return "cdc"
    if "nih.gov" in host or "ncbi.nlm.nih.gov" in host:
        return "nih"
    if "wikipedia.org" in host:
        return "wikipedia"
    return "web"


def guess_source_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if not host:
        return "web"
    return host


def duckduckgo_search(
    session: Any,
    query: str,
    max_results: int,
    *,
    page_fetcher: Callable[[str], tuple[str, str, str]],
    source_type_resolver: Callable[[str], str] = guess_source_type,
    source_name_resolver: Callable[[str], str] = guess_source_name,
    evidence_deduplicator: Callable[[list[EvidenceRef]], list[EvidenceRef]] | None = None,
) -> list[EvidenceRef]:
    params = {"q": query}
    results: list[EvidenceRef] = []
    try:
        response = session.get(WEB_SEARCH_ENDPOINT, params=params, timeout=20)
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Web search failed for %s: %s", query, exc)
        return results

    soup = BeautifulSoup(response.text, "html.parser")
    anchors = soup.select("a.result__a")[: max_results * 2]
    for anchor in anchors:
        title = compact_text(anchor.get_text(" ", strip=True), 200)
        url = anchor.get("href") or ""
        if not url:
            continue
        snippet = ""
        parent = anchor.find_parent("div", class_="result")
        if parent:
            snippet_node = parent.select_one(".result__snippet")
            if snippet_node:
                snippet = compact_text(snippet_node.get_text(" ", strip=True), 500)
        resolved = url
        page_title = title
        page_snippet = snippet
        if url and len(results) < max_results:
            fetched_title, fetched_snippet, resolved = page_fetcher(url)
            if fetched_title:
                page_title = fetched_title
            if fetched_snippet:
                page_snippet = fetched_snippet
        content = page_snippet or title
        results.append(
            EvidenceRef(
                evidence_type="web",
                source_type=source_type_resolver(url),
                source_name=source_name_resolver(url),
                title=page_title or title or url,
                url=url,
                resolved_url=resolved,
                content_snippet=content,
                content_hash=stable_hash(f"{url}|{content}"),
                confidence=0.7 if page_snippet else 0.5,
                metadata={"query": query},
            )
        )
        if len(results) >= max_results:
            break
    return (evidence_deduplicator or unique_evidence)(results)


def unique_evidence(items: list[EvidenceRef]) -> list[EvidenceRef]:
    seen = set()
    unique = []
    for item in items:
        marker = item.content_hash or stable_hash(f"{item.source_type}:{item.title}:{item.content_snippet}")
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def row_title(row: Any, table_name: str) -> str:
    for attr in ("title", "name", "name_en", "standard_name_en", "disease_id", "local_name", "code", "country_code"):
        value = getattr(row, attr, None)
        if value:
            return compact_text(value, 120)
    return table_name


def row_to_snippet(row: Any) -> str:
    if hasattr(row, "to_dict"):
        payload = row.to_dict()
    else:
        payload = (
            {column.name: getattr(row, column.name, None) for column in row.__table__.columns}
            if hasattr(row, "__table__")
            else {}
        )
    pieces = []
    for key in sorted(payload.keys()):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        pieces.append(f"{key}={value}")
        if len(" | ".join(pieces)) > 1000:
            break
    return " | ".join(pieces)
