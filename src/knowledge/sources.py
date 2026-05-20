"""
Source adapters for the disease knowledge base.

The adapters resolve canonical URLs, retain short source-attributed excerpts,
and preserve parsed page text plus section structure for downstream grounding.
MSD Manual is metadata-only by default because its public copyright terms are
not suitable for republishing generated derivative text without additional
review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import re
import time
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from requests import Request
from bs4 import BeautifulSoup

from src.core import get_logger

logger = get_logger(__name__)


DEFAULT_USER_AGENT = (
    "GlobalID-KnowledgeBot/1.0 "
    "(https://globalid.local; contact: globalid-maintainer@example.com)"
)

SOURCE_LICENSES = {
    "who": "WHO website terms; cite WHO URL; non-commercial/permission rules may apply",
    "who_don": "WHO Disease Outbreak News API; cite WHO URL; non-commercial/permission rules may apply",
    "wikidata": "Wikidata structured data; CC0 unless source-specific references apply",
    "wikipedia": "Wikipedia summary; CC BY-SA; attribution required",
    "pubmed": "PubMed/PMC open-access abstracts; NLM terms; cite PMID/DOI",
    "msd": "MSD Manual metadata only; public reuse requires permission/review",
    "web_search": "Trusted web search discovery snippets/page metadata; cite URL; source-specific reuse terms apply",
}

TRUSTED_SOURCE_REGISTRY = (
    {
        "source_type": "who",
        "label": "WHO official pages",
        "trust_level": "high",
        "republish_policy": "summary only",
        "notes": "Health Topics, Fact Sheets, and Q&A pages provide the primary official disease narrative.",
    },
    {
        "source_type": "who_don",
        "label": "WHO Disease Outbreak News",
        "trust_level": "high",
        "republish_policy": "summary only",
        "notes": "Use for outbreak-specific context, dates, and location signals.",
    },
    {
        "source_type": "wikidata",
        "label": "Wikidata",
        "trust_level": "medium",
        "republish_policy": "structured metadata",
        "notes": "Best for identifiers, labels, and structured entity metadata.",
    },
    {
        "source_type": "web_search",
        "label": "Trusted web search discovery",
        "trust_level": "medium",
        "republish_policy": "short snippets and public pages only",
        "notes": "Bing-like discovery layer over trusted domains such as WHO, CDC, NIH/NCBI, BMJ, MSD, and Wikipedia.",
    },
    {
        "source_type": "wikipedia",
        "label": "Wikipedia disease pages",
        "trust_level": "medium",
        "republish_policy": "short attribution-required summary",
        "notes": "Use disease pages only, not disambiguation pages.",
    },
    {
        "source_type": "pubmed",
        "label": "PubMed/PMC review articles",
        "trust_level": "medium",
        "republish_policy": "abstract summary only",
        "notes": "Use recent review articles for supplementary clinical and epidemiological context when WHO sources are unavailable.",
    },
    {
        "source_type": "msd",
        "label": "MSD Manual",
        "trust_level": "review-only",
        "republish_policy": "metadata only",
        "notes": "Store URL and metadata, but do not republish substantive text without review.",
    },
)
TRUSTED_SOURCE_TYPES = tuple(item["source_type"] for item in TRUSTED_SOURCE_REGISTRY)
SOURCE_FETCH_ORDER = tuple(item["source_type"] for item in TRUSTED_SOURCE_REGISTRY)


@dataclass
class SourceCandidate:
    disease_id: str
    source_type: str
    source_name: str
    url: str
    resolved_url: str | None = None
    title: str | None = None
    license: str | None = None
    language: str = "en"
    raw_excerpt: str | None = None
    content_text: str | None = None
    content_sections: list[dict[str, str]] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "active"
    review_status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def raw_excerpt_hash(self) -> str | None:
        if not self.raw_excerpt:
            return None
        return sha256(self.raw_excerpt.encode("utf-8")).hexdigest()


class DiseaseKnowledgeFetcher:
    """Fetch short source candidates for one standard disease row."""

    WEB_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
    TRUSTED_WEB_DOMAINS = (
        ("who.int", "WHO", True),
        ("cdc.gov", "CDC", True),
        ("nih.gov", "NIH/NCBI", True),
        ("ncbi.nlm.nih.gov", "NIH/NCBI", True),
        ("bmj.com", "BMJ", False),
        ("msdmanuals.com", "MSD Manual", False),
        ("wikipedia.org", "Wikipedia", True),
    )

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 12,
        max_excerpt_chars: int = 700,
        min_interval_seconds: float = 0.5,
        max_retries: int = 2,
    ) -> None:
        self.timeout = timeout
        self.max_excerpt_chars = max_excerpt_chars
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.max_retries = max(0, max_retries)
        self._last_request_at = 0.0
        self._response_cache: dict[str, requests.Response] = {}
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Api-User-Agent": user_agent,
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            }
        )

    def fetch(
        self,
        disease: dict[str, Any],
        *,
        enabled_sources: Iterable[str] | None = None,
    ) -> list[SourceCandidate]:
        enabled = list(enabled_sources or SOURCE_FETCH_ORDER)
        candidates: list[SourceCandidate] = []

        adapters = {
            "who": self._fetch_who_pages,
            "who_don": self._fetch_who_don,
            "wikidata": self._fetch_wikidata,
            "web_search": self._fetch_web_search,
            "wikipedia": self._fetch_wikipedia,
            "pubmed": self._fetch_pubmed,
            "msd": self._build_msd_metadata,
        }
        for key in enabled:
            adapter = adapters.get(key)
            if not adapter:
                continue
            try:
                candidates.extend(adapter(disease))
            except Exception as exc:
                logger.warning("Knowledge source adapter failed for %s/%s: %s", disease.get("disease_id"), key, exc)

        return self._rank_candidates(self._dedupe(candidates))

    def _crawl_html_page(
        self,
        *,
        disease_id: str,
        source_type: str,
        source_name: str,
        url: str,
        license: str,
        matched_name: str | None = None,
        review_status: str = "approved",
        raw_excerpt_fallback: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceCandidate | None:
        response = self._get(url)
        if response is None or response.status_code != 200:
            return None

        content = self._extract_html_content(response.text, resolved_url=response.url or url)
        title, excerpt = content["title"], content["excerpt"]
        content_text = content["content_text"]
        if not title and not excerpt and not content_text:
            return None
        if matched_name and not self._looks_relevant(matched_name, url, title, excerpt):
            return None

        return SourceCandidate(
            disease_id=disease_id,
            source_type=source_type,
            source_name=source_name,
            url=url,
            resolved_url=content["resolved_url"] or response.url or url,
            title=title or source_name,
            license=license,
            raw_excerpt=self._clip(excerpt or raw_excerpt_fallback),
            content_text=content_text,
            content_sections=content["sections"],
            review_status=review_status,
            metadata={
                **(metadata or {}),
                "canonical_url": content["canonical_url"],
                "content_language": content["content_language"],
                "content_kind": "html",
            },
        )

    def _fetch_who_pages(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        disease_id = str(disease["disease_id"])
        names = self._query_candidates(disease)
        urls: list[tuple[str, str]] = []
        for name in names[:5]:
            slug = self._slug(name)
            if not slug or len(slug) < 3:
                continue
            urls.extend(
                [
                    ("WHO Health Topics", f"https://www.who.int/health-topics/{slug}"),
                    ("WHO Fact Sheet", f"https://www.who.int/news-room/fact-sheets/detail/{slug}"),
                    ("WHO Q&A", f"https://www.who.int/news-room/questions-and-answers/item/{slug}"),
                ]
            )

        candidates: list[SourceCandidate] = []
        matched_name = self._primary_name(disease) or (names[0] if names else disease_id)
        for source_name, url in urls:
            candidate = self._crawl_html_page(
                disease_id=disease_id,
                source_type="who",
                source_name=source_name,
                url=url,
                license=SOURCE_LICENSES["who"],
                matched_name=matched_name,
                metadata={"matched_name": matched_name},
            )
            if candidate is None:
                continue
            if candidate.raw_excerpt and len(candidate.raw_excerpt.strip()) < 20:
                continue
            candidates.append(candidate)
        return candidates

    def _fetch_who_don(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        disease_id = str(disease["disease_id"])
        url = "https://www.who.int/api/news/diseaseoutbreaknews"
        candidates: list[SourceCandidate] = []
        for name in self._query_candidates(disease)[:4]:
            safe_name = name.replace("'", "''")
            params = {
                "$top": "3",
                "$select": "Title,ItemDefaultUrl,PublicationDate",
                "$filter": f"contains(Title,'{safe_name}')",
                "$orderby": "PublicationDate desc",
            }
            response = self._get(url, params=params)
            if response is None or response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            items = payload.get("value") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("Title") or item.get("title")
                item_url = item.get("ItemDefaultUrl") or item.get("Url") or ""
                if item_url and item_url.startswith("/"):
                    item_url = f"https://www.who.int{item_url}"
                page_title: str | None = None
                page_excerpt: str | None = None
                page_content: dict[str, Any] = {}
                if item_url:
                    page_candidate = self._crawl_html_page(
                        disease_id=disease_id,
                        source_type="who_don",
                        source_name="WHO Disease Outbreak News",
                        url=item_url,
                        license=SOURCE_LICENSES["who_don"],
                        matched_name=name,
                        review_status="approved",
                        metadata={
                            "publication_date": item.get("PublicationDate"),
                            "matched_name": name,
                            "page_url": item_url or None,
                        },
                        raw_excerpt_fallback=f"WHO Disease Outbreak News item related to {name}: {title or ''}",
                    )
                    if page_candidate is not None:
                        candidates.append(page_candidate)
                        continue
                    page_response = self._get(item_url)
                    if page_response is not None and page_response.status_code == 200:
                        page_content = self._extract_html_content(page_response.text, resolved_url=page_response.url or item_url)
                        page_title, page_excerpt = page_content["title"], page_content["excerpt"]
                if not page_excerpt and not page_title:
                    logger.debug("Skipping WHO DON item without fetchable page: %s", item_url or title)
                    continue
                candidates.append(
                    SourceCandidate(
                        disease_id=disease_id,
                        source_type="who_don",
                        source_name="WHO Disease Outbreak News",
                        url=item_url or url,
                        resolved_url=page_content.get("resolved_url") or item_url or url,
                        title=page_title or title or "WHO Disease Outbreak News",
                        license=SOURCE_LICENSES["who_don"],
                        raw_excerpt=self._clip(page_excerpt or f"WHO Disease Outbreak News item related to {name}: {title or ''}"),
                        content_text=page_content.get("content_text"),
                        content_sections=page_content.get("sections") or [],
                        review_status="approved",
                        metadata={
                            "publication_date": item.get("PublicationDate"),
                            "matched_name": name,
                            "page_url": item_url or None,
                            "canonical_url": page_content.get("canonical_url"),
                            "content_language": page_content.get("content_language"),
                            "content_kind": "html",
                            "relevance_score": 0.8,
                        },
                    )
                )
        return candidates

    def _fetch_wikidata(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        disease_id = str(disease["disease_id"])
        for name in self._query_candidates(disease)[:5]:
            response = self._get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities",
                    "format": "json",
                    "language": "en",
                    "type": "item",
                    "limit": "3",
                    "search": name,
                },
            )
            if response is None or response.status_code != 200:
                continue
            try:
                search = response.json().get("search") or []
            except ValueError:
                continue
            if not search:
                continue
            for item in search:
                qid = item.get("id")
                label = item.get("label") or name
                description = item.get("description") or ""
                score = self._relevance_score(self._query_candidates(disease), f"https://www.wikidata.org/wiki/{qid}", label, description)
                if score < 0.15:
                    continue
                return [
                    SourceCandidate(
                        disease_id=disease_id,
                        source_type="wikidata",
                        source_name="Wikidata",
                        url=f"https://www.wikidata.org/wiki/{qid}" if qid else "https://www.wikidata.org",
                        resolved_url=f"https://www.wikidata.org/wiki/{qid}" if qid else "https://www.wikidata.org",
                        title=label,
                        license=SOURCE_LICENSES["wikidata"],
                        raw_excerpt=self._clip(description or f"Structured Wikidata item for {label}."),
                        content_text=self._clip(description or f"Structured Wikidata item for {label}.", 2000),
                        content_sections=[],
                        review_status="approved",
                        metadata={"qid": qid, "matched_name": name, "content_kind": "structured", "relevance_score": score},
                    )
                ]
        return []

    def _fetch_wikipedia(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        disease_id = str(disease["disease_id"])
        names = self._query_candidates(disease)
        primary_name = self._primary_name(disease) or (names[0] if names else disease_id)
        title_candidates = []
        for name in names[:5]:
            title_candidates.extend([name, f"{name} (disease)", f"{name} disease"])
        for candidate in title_candidates:
            payload = self._fetch_wikipedia_summary(candidate)
            if not payload:
                continue
            title = payload.get("title") or candidate
            excerpt = payload.get("extract") or payload.get("description") or ""
            if not excerpt or self._looks_like_wikipedia_disambiguation(payload):
                continue
            page_url = (payload.get("content_urls") or {}).get("desktop", {}).get("page")
            if page_url:
                html_candidate = self._crawl_html_page(
                    disease_id=disease_id,
                    source_type="wikipedia",
                    source_name="Wikipedia",
                    url=page_url,
                    license=SOURCE_LICENSES["wikipedia"],
                    matched_name=primary_name,
                    metadata={
                        "matched_name": candidate,
                        "candidate_title": candidate,
                        "content_kind": "html",
                        "canonical_url": page_url,
                    },
                )
                if html_candidate is not None:
                    return [html_candidate]
            return [
                SourceCandidate(
                    disease_id=disease_id,
                    source_type="wikipedia",
                    source_name="Wikipedia",
                    url=page_url or f"https://en.wikipedia.org/wiki/{quote(candidate.replace(' ', '_'))}",
                    resolved_url=page_url,
                    title=title,
                    license=SOURCE_LICENSES["wikipedia"],
                    raw_excerpt=self._clip(excerpt),
                    content_text=self._clip(excerpt, 2000),
                    content_sections=[],
                    review_status="approved",
                    metadata={
                        "matched_name": candidate,
                        "candidate_title": candidate,
                        "content_kind": "summary",
                        "canonical_url": page_url,
                        "relevance_score": self._relevance_score(names, page_url or "", title, excerpt),
                    },
                )
            ]

        for name in names[:5]:
            search_result = self._search_wikipedia(disease_id=disease_id, name=name, query_terms=names)
            if search_result:
                return search_result
        return []

    def _fetch_wikipedia_summary(self, title: str) -> dict[str, Any] | None:
        response = self._get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title)}")
        if response is None or response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    def _search_wikipedia(self, *, disease_id: str, name: str, query_terms: list[str]) -> list[SourceCandidate]:
        response = self._get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": f'"{name}" disease virus',
                "srlimit": "5",
                "srnamespace": "0",
            },
        )
        if response is None or response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        results = payload.get("query", {}).get("search") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or ""
            summary = self._fetch_wikipedia_summary(title)
            if not summary or self._looks_like_wikipedia_disambiguation(summary):
                continue
            excerpt = summary.get("extract") or summary.get("description") or ""
            if not excerpt:
                continue
            page_url = (summary.get("content_urls") or {}).get("desktop", {}).get("page")
            score = self._relevance_score(query_terms, page_url or "", summary.get("title") or title, excerpt)
            if score < 0.25:
                continue
            return [
                SourceCandidate(
                    disease_id=disease_id,
                    source_type="wikipedia",
                    source_name="Wikipedia",
                    url=page_url or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                    resolved_url=page_url,
                    title=summary.get("title") or title,
                    license=SOURCE_LICENSES["wikipedia"],
                    raw_excerpt=self._clip(excerpt),
                    content_text=self._clip(excerpt, 2000),
                    content_sections=[],
                    review_status="approved",
                    metadata={
                        "matched_name": name,
                        "candidate_title": title,
                        "search_fallback": True,
                        "content_kind": "summary",
                        "canonical_url": page_url,
                        "relevance_score": score,
                    },
                )
            ]
        return []

    @staticmethod
    def _looks_like_wikipedia_disambiguation(payload: dict[str, Any]) -> bool:
        title = str(payload.get("title") or "").lower()
        description = str(payload.get("description") or "").lower()
        extract = str(payload.get("extract") or "").lower()
        if "disambiguation" in title or "disambiguation" in description:
            return True
        if "may refer to" in extract:
            return True
        if "refers to:" in extract and len(extract) < 250:
            return True
        return False

    def _fetch_pubmed(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        """Fetch recent review articles from PubMed E-utilities for supplementary knowledge."""
        disease_id = str(disease["disease_id"])
        query_candidates = self._query_candidates(disease)
        id_list: list[str] = []
        search_term_used = ""
        for search_term in self._pubmed_search_terms(query_candidates):
            search_response = self._get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": search_term,
                    "retmax": "5",
                    "sort": "relevance",
                    "retmode": "json",
                },
            )
            if search_response is None or search_response.status_code != 200:
                continue
            try:
                search_data = search_response.json()
                id_list = search_data.get("esearchresult", {}).get("idlist", [])
            except (ValueError, KeyError):
                continue
            if id_list:
                search_term_used = search_term
                break

        if not id_list:
            return []

        # Fetch article summaries
        summary_response = self._get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(id_list[:3]),
                "retmode": "json",
            },
        )
        if summary_response is None or summary_response.status_code != 200:
            return []

        try:
            summary_data = summary_response.json()
            results = summary_data.get("result", {})
        except (ValueError, KeyError):
            return []

        # Fetch abstracts via efetch
        abstract_response = self._get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(id_list[:3]),
                "rettype": "abstract",
                "retmode": "xml",
            },
        )
        abstracts_by_pmid: dict[str, str] = {}
        if abstract_response is not None and abstract_response.status_code == 200:
            try:
                abstract_soup = BeautifulSoup(abstract_response.content, "xml")
                for article in abstract_soup.find_all("PubmedArticle"):
                    pmid_tag = article.find("PMID")
                    abstract_tag = article.find("Abstract")
                    if pmid_tag and abstract_tag:
                        pmid = pmid_tag.get_text(strip=True)
                        abstract_text = " ".join(
                            t.get_text(" ", strip=True)
                            for t in abstract_tag.find_all("AbstractText")
                        )
                        abstracts_by_pmid[pmid] = abstract_text
            except Exception as exc:
                logger.debug("PubMed abstract XML parse failed: %s", exc)

        candidates: list[SourceCandidate] = []
        uid_list = results.get("uids", id_list[:3])
        for pmid in uid_list:
            article = results.get(str(pmid))
            if not isinstance(article, dict):
                continue

            title = article.get("title") or ""
            authors = article.get("authors") or []
            first_author = authors[0].get("name", "") if authors else ""
            pub_date = article.get("pubdate") or article.get("epubdate") or ""
            source_journal = article.get("source") or ""
            doi = ""
            article_ids = article.get("articleids") or []
            for aid in article_ids:
                if isinstance(aid, dict) and aid.get("idtype") == "doi":
                    doi = aid.get("value", "")
                    break

            pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            abstract = abstracts_by_pmid.get(str(pmid), "")

            # Build content text from abstract
            content_text = abstract if abstract else f"Review article: {title}"
            if len(content_text) > 2000:
                content_text = content_text[:2000].rstrip() + "..."

            # Build citation-style excerpt
            citation = f"{first_author} et al. {title} {source_journal}. {pub_date}."
            excerpt = f"{citation} {abstract[:400]}..." if abstract and len(abstract) > 400 else f"{citation} {abstract}"

            candidates.append(
                SourceCandidate(
                    disease_id=disease_id,
                    source_type="pubmed",
                    source_name="PubMed",
                    url=pubmed_url,
                    resolved_url=pubmed_url,
                    title=title.rstrip("."),
                    license=SOURCE_LICENSES["pubmed"],
                    raw_excerpt=self._clip(excerpt),
                    content_text=content_text,
                    content_sections=[
                        {"heading": "Abstract", "text": self._clip(abstract) or ""}
                    ] if abstract else [],
                    review_status="approved",
                    metadata={
                        "pmid": str(pmid),
                        "doi": doi,
                        "first_author": first_author,
                        "journal": source_journal,
                        "pub_date": pub_date,
                        "matched_name": query_candidates[0] if query_candidates else disease_id,
                        "query_candidates": query_candidates[:8],
                        "search_term": search_term_used,
                        "content_kind": "abstract",
                        "relevance_score": self._relevance_score(query_candidates, pubmed_url, title, abstract),
                    },
                )
            )

        return candidates

    def _fetch_web_search(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        """Search trusted public-health domains when direct source adapters miss a disease concept."""
        disease_id = str(disease["disease_id"])
        query_terms = self._query_candidates(disease)
        candidates: list[SourceCandidate] = self._fetch_crossref_metadata(disease_id=disease_id, query_terms=query_terms)
        if len(candidates) >= 4:
            return candidates[:6]

        seen_urls: set[str] = set()
        for candidate in candidates:
            seen_urls.add(candidate.url)
        for query in self._web_search_queries(query_terms):
            for item in self._duckduckgo_search(query, max_results=5):
                url = item["url"]
                if url in seen_urls:
                    continue
                profile = self._trusted_web_domain(url)
                if profile is None:
                    continue
                source_name, may_store_page_text = profile
                score = self._relevance_score(query_terms, url, item.get("title"), item.get("snippet"))
                if score < 0.18:
                    continue
                seen_urls.add(url)

                title = item.get("title") or source_name
                snippet = item.get("snippet") or title
                resolved_url = url
                content_text = snippet
                content_sections: list[dict[str, str]] = []
                metadata: dict[str, Any] = {
                    "adapter": "web_search",
                    "query": query,
                    "domain": urlparse(url).netloc.lower(),
                    "content_kind": "search_result",
                    "relevance_score": score,
                }
                if may_store_page_text:
                    page = self._crawl_html_page(
                        disease_id=disease_id,
                        source_type="web_search",
                        source_name=source_name,
                        url=url,
                        license=SOURCE_LICENSES["web_search"],
                        matched_name=None,
                        review_status="approved",
                        metadata=metadata,
                        raw_excerpt_fallback=snippet,
                    )
                    if page is not None:
                        page.metadata["adapter"] = "web_search"
                        page.metadata["query"] = query
                        page.metadata["domain"] = urlparse(url).netloc.lower()
                        page.metadata["relevance_score"] = score
                        candidates.append(page)
                        continue

                candidates.append(
                    SourceCandidate(
                        disease_id=disease_id,
                        source_type="web_search",
                        source_name=source_name,
                        url=url,
                        resolved_url=resolved_url,
                        title=title,
                        license=SOURCE_LICENSES["web_search"],
                        raw_excerpt=self._clip(snippet),
                        content_text=self._clip(content_text, 2000),
                        content_sections=content_sections,
                        review_status="approved" if may_store_page_text else "requires_review",
                        metadata=metadata,
                    )
                )
                if len(candidates) >= 6:
                    return candidates
        return candidates

    def _fetch_crossref_metadata(self, *, disease_id: str, query_terms: list[str]) -> list[SourceCandidate]:
        candidates: list[SourceCandidate] = []
        seen_urls: set[str] = set()
        for query in query_terms[:2]:
            response = self._get(
                "https://api.crossref.org/works",
                params={
                    "query.title": query,
                    "rows": "4",
                    "select": "DOI,title,container-title,publisher,issued,URL,abstract,score,type",
                },
            )
            if response is None or response.status_code != 200:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            items = payload.get("message", {}).get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = self._first_text(item.get("title"))
                container = self._first_text(item.get("container-title"))
                doi = str(item.get("DOI") or "").strip()
                url = str(item.get("URL") or "").strip()
                if not url and doi:
                    url = f"https://doi.org/{doi}"
                if not title or not url or url in seen_urls:
                    continue
                year = self._issued_year(item.get("issued"))
                publisher = str(item.get("publisher") or "Crossref").strip()
                abstract = self._strip_html(item.get("abstract"))
                score = self._relevance_score(query_terms, url, title, abstract or container or publisher)
                if score < 0.25:
                    continue
                seen_urls.add(url)
                citation_parts = [
                    f"Scholarly metadata: {title}.",
                    f"Container: {container}." if container else "",
                    f"Publisher: {publisher}." if publisher else "",
                    f"Year: {year}." if year else "",
                    f"DOI: {doi}." if doi else "",
                ]
                metadata_text = " ".join(part for part in citation_parts if part)
                content_text = f"{metadata_text} {abstract}".strip() if abstract else metadata_text
                candidates.append(
                    SourceCandidate(
                        disease_id=disease_id,
                        source_type="web_search",
                        source_name="Crossref scholarly metadata",
                        url=url,
                        resolved_url=url,
                        title=title,
                        license=SOURCE_LICENSES["web_search"],
                        raw_excerpt=self._clip(content_text),
                        content_text=self._clip(content_text, 2000),
                        content_sections=[{"heading": "Scholarly metadata", "text": self._clip(content_text) or ""}],
                        review_status="approved" if abstract else "requires_review",
                        metadata={
                            "adapter": "web_search",
                            "provider": "crossref",
                            "query": query,
                            "doi": doi,
                            "publisher": publisher,
                            "container_title": container,
                            "year": year,
                            "crossref_type": item.get("type"),
                            "crossref_score": item.get("score"),
                            "content_kind": "scholarly_metadata",
                            "relevance_score": score,
                        },
                    )
                )
                if len(candidates) >= 6:
                    return candidates
        return candidates

    def _build_msd_metadata(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        disease_id = str(disease["disease_id"])
        name = str(disease.get("name_en") or disease.get("standard_name_en") or disease_id)
        return [
            SourceCandidate(
                disease_id=disease_id,
                source_type="msd",
                source_name="MSD Manual Professional Edition",
                url=f"https://www.msdmanuals.com/professional/SearchResults?query={quote(name)}",
                resolved_url=f"https://www.msdmanuals.com/professional/SearchResults?query={quote(name)}",
                title=f"MSD Manual search metadata for {name}",
                license=SOURCE_LICENSES["msd"],
                raw_excerpt="Metadata-only fallback. Public reuse of MSD Manual text requires permission or manual review.",
                content_text=None,
                content_sections=[],
                review_status="requires_review",
                metadata={"matched_name": name, "metadata_only": True},
            )
        ]

    def _duckduckgo_search(self, query: str, *, max_results: int) -> list[dict[str, str]]:
        response = self._get(self.WEB_SEARCH_ENDPOINT, params={"q": query})
        if response is None or response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[dict[str, str]] = []
        for anchor in soup.select("a.result__a")[: max_results * 3]:
            url = self._normalize_search_result_url(anchor.get("href") or "")
            if not url:
                continue
            title = self._clip(anchor.get_text(" ", strip=True), 220) or url
            snippet = ""
            parent = anchor.find_parent("div", class_="result")
            if parent:
                snippet_node = parent.select_one(".result__snippet")
                if snippet_node:
                    snippet = self._clip(snippet_node.get_text(" ", strip=True), 700) or ""
            results.append({"title": title, "url": url, "snippet": snippet})
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _normalize_search_result_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("//"):
            url = f"https:{url}"
        if url.startswith("/"):
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            uddg = query.get("uddg")
            if uddg:
                return unquote(uddg[0])
            return ""
        parsed = urlparse(url)
        if parsed.netloc.lower().endswith("duckduckgo.com"):
            query = parse_qs(parsed.query)
            uddg = query.get("uddg")
            if uddg:
                return unquote(uddg[0])
        return url

    def _trusted_web_domain(self, url: str) -> tuple[str, bool] | None:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        for domain, source_name, may_store_page_text in self.TRUSTED_WEB_DOMAINS:
            if host == domain or host.endswith(f".{domain}"):
                return source_name, may_store_page_text
        return None

    @staticmethod
    def _first_text(value: Any) -> str:
        if isinstance(value, list):
            for item in value:
                text = " ".join(str(item or "").split()).strip()
                if text:
                    return text
            return ""
        return " ".join(str(value or "").split()).strip()

    @staticmethod
    def _issued_year(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        parts = value.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            return str(parts[0][0])
        return ""

    @staticmethod
    def _strip_html(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return " ".join(BeautifulSoup(text, "html.parser").get_text(" ", strip=True).split())

    @staticmethod
    def _web_search_queries(query_terms: list[str]) -> list[str]:
        terms = query_terms[:5]
        if not terms:
            return []
        queries = [f'"{term}" disease' for term in terms[:3]]
        primary = terms[0]
        queries.extend(
            [
                f'"{primary}" CDC NIH WHO',
                f'"{primary}" BMJ MSD Manual',
            ]
        )
        return DiseaseKnowledgeFetcher._unique_strings(queries)[:5]

    @staticmethod
    def _pubmed_search_terms(query_terms: list[str]) -> list[str]:
        terms = [term for term in query_terms[:7] if term]
        if not terms:
            return []
        title_abstract = " OR ".join(f'"{term}"[Title/Abstract]' for term in terms)
        all_fields = " OR ".join(f'"{term}"[All Fields]' for term in terms[:5])
        return [
            f"({title_abstract}) AND (review[pt] OR systematic review[pt] OR guideline[pt])",
            f"({title_abstract})",
            f"({all_fields}) AND (review[pt] OR systematic review[pt])",
            f"({all_fields})",
        ]

    def _get(self, url: str, params: dict[str, str] | None = None) -> requests.Response | None:
        cache_key = Request("GET", url, params=params).prepare().url or url
        cached = self._response_cache.get(cache_key)
        if cached is not None:
            return cached

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                logger.debug("Knowledge source request failed: %s (%s)", url, exc)
                if attempt >= self.max_retries:
                    return None
                time.sleep(0.35 * (attempt + 1))
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (attempt + 1)
                time.sleep(delay)
                continue

            self._response_cache[cache_key] = response
            return response
        return None

    def _throttle(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _extract_html_content(self, html: str, *, resolved_url: str | None = None) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        title = None
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(" ", strip=True) if h1 else None

        canonical = None
        canonical_link = soup.find("link", attrs={"rel": "canonical"})
        if canonical_link and canonical_link.get("href"):
            canonical = str(canonical_link["href"]).strip()
        if not canonical:
            og = soup.find("meta", attrs={"property": "og:url"})
            if og and og.get("content"):
                canonical = str(og["content"]).strip()

        content_language = None
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang"):
            content_language = str(html_tag.get("lang")).strip()

        container = (
            soup.find("article")
            or soup.find("main")
            or soup.select_one(".content")
            or soup.select_one(".article")
            or soup.find("section")
            or soup.body
            or soup
        )

        ordered_blocks = container.find_all(["h1", "h2", "h3", "p"], recursive=True) if container else []
        paragraphs = []
        content_sections: list[dict[str, str]] = []
        active_section: dict[str, Any] | None = None
        for element in ordered_blocks:
            text = element.get_text(" ", strip=True)
            if not text:
                continue
            if element.name in {"h1", "h2", "h3"}:
                if active_section and active_section.get("paragraphs"):
                    section_text = " ".join(active_section["paragraphs"])
                    content_sections.append(
                        {
                            "heading": active_section.get("heading"),
                            "text": self._clip(section_text) or section_text,
                        }
                    )
                active_section = {"heading": text, "paragraphs": []}
                continue
            paragraphs.append(text)
            if active_section is None:
                active_section = {"heading": title, "paragraphs": []}
            active_section.setdefault("paragraphs", []).append(text)

        if active_section and active_section.get("paragraphs"):
            section_text = " ".join(active_section["paragraphs"])
            content_sections.append(
                {
                    "heading": active_section.get("heading"),
                    "text": self._clip(section_text) or section_text,
                }
            )

        excerpt = " ".join(paragraphs[:4]) if paragraphs else None
        content_text = " ".join(paragraphs[:8]) if paragraphs else None
        if content_text and len(content_text) > 4000:
            content_text = content_text[:4000].rstrip() + "..."

        meta = soup.find("meta", attrs={"name": "description"})
        if not excerpt and meta and meta.get("content"):
            excerpt = str(meta["content"]).strip()
        if not content_text and excerpt:
            content_text = excerpt

        headings = []
        for selector in ("article h2", "article h3", "main h2", "main h3", "h2", "h3"):
            for heading in soup.select(selector):
                text = heading.get_text(" ", strip=True)
                if text:
                    headings.append(text)
                if len(headings) >= 6:
                    break
            if len(headings) >= 6:
                break

        return {
            "title": title,
            "excerpt": excerpt,
            "content_text": content_text,
            "sections": content_sections or [{"heading": heading} for heading in headings],
            "resolved_url": resolved_url,
            "canonical_url": canonical,
            "content_language": content_language,
        }

    @staticmethod
    def _slug(value: str) -> str:
        slug = value.strip().lower()
        slug = slug.replace("&", "and").replace("/", " ")
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        return slug.strip("-")

    def _looks_relevant(self, name: str, url: str, title: str | None, excerpt: str | None) -> bool:
        name_tokens = [token for token in self._slug(name).split("-") if len(token) >= 3]
        if not name_tokens:
            return False
        haystack = self._slug(" ".join([url or "", title or "", excerpt or ""]))
        return any(token in haystack for token in name_tokens)

    @staticmethod
    def _name_candidates(disease: dict[str, Any]) -> list[str]:
        names = [
            disease.get("name_en"),
            disease.get("standard_name_en"),
            disease.get("name_zh"),
            disease.get("standard_name_zh"),
        ]
        result = []
        for name in names:
            if name and str(name).strip() and str(name).strip() not in result:
                result.append(str(name).strip())
        return result

    @staticmethod
    def _primary_name(disease: dict[str, Any]) -> str:
        for key in ("name_en", "standard_name_en", "name_zh", "standard_name_zh", "disease_id"):
            value = disease.get(key)
            if value and str(value).strip():
                return str(value).strip()
        return ""

    @classmethod
    def _query_candidates(cls, disease: dict[str, Any]) -> list[str]:
        phrases = cls._name_candidates(disease)
        description = cls._clean_search_phrase(disease.get("description"))
        if description:
            phrases.append(description)

        corpus = " ".join(phrases).lower()
        expanded: list[str] = []
        for phrase in phrases:
            expanded.extend(cls._phrase_variants(phrase))

        if "arenaviral" in corpus or "arenavirus" in corpus:
            expanded.extend(["arenaviral hemorrhagic fever", "New World arenavirus", "New World arenaviruses"])
        if "south american" in corpus and "hemorrhagic" in corpus:
            expanded.extend(["South American hemorrhagic fevers", "New World arenavirus"])

        return cls._unique_strings([phrase for phrase in expanded if len(phrase) >= 3])[:12]

    @staticmethod
    def _clean_search_phrase(value: Any) -> str:
        text = " ".join(str(value or "").split())
        text = re.sub(r"\bsurveillance concept\b", "", text, flags=re.I)
        text = re.sub(r"\btracked in .* catalogue\b", "", text, flags=re.I)
        return " ".join(text.split()).strip(" ,;:-")

    @classmethod
    def _phrase_variants(cls, phrase: str) -> list[str]:
        text = " ".join(str(phrase or "").split()).strip()
        if not text:
            return []
        variants = [text]
        lower = text.lower()
        if lower.endswith(" fever"):
            variants.append(f"{text}s")
        if lower.endswith(" fevers"):
            variants.append(text[:-1])
        if "hemorrhagic" in lower:
            variants.append(re.sub("hemorrhagic", "haemorrhagic", text, flags=re.I))
        if "haemorrhagic" in lower:
            variants.append(re.sub("haemorrhagic", "hemorrhagic", text, flags=re.I))
        return cls._unique_strings(variants)

    @classmethod
    def _relevance_score(cls, query_terms: list[str], url: str, title: Any, excerpt: Any) -> float:
        haystack = cls._slug(" ".join([url or "", str(title or ""), str(excerpt or "")]))
        if not haystack:
            return 0.0
        tokens: list[str] = []
        phrase_bonus = 0.0
        for term in query_terms:
            slug = cls._slug(term)
            if slug and slug in haystack:
                phrase_bonus = max(phrase_bonus, 0.45)
            tokens.extend(token for token in slug.split("-") if len(token) >= 4)
        unique_tokens = cls._unique_strings(tokens)
        if not unique_tokens:
            return phrase_bonus
        matched = sum(1 for token in unique_tokens if token in haystack)
        token_score = matched / min(len(unique_tokens), 8)
        return min(1.0, phrase_bonus + token_score * 0.65)

    @staticmethod
    def _unique_strings(values: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            text = " ".join(str(value or "").split()).strip()
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    def _clip(self, text: str | None, limit: int | None = None) -> str | None:
        if not text:
            return None
        compact = " ".join(str(text).split())
        max_chars = self.max_excerpt_chars if limit is None else max(0, limit)
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars].rstrip() + "..."

    @staticmethod
    def _dedupe(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
        seen: set[tuple[str, str, str]] = set()
        result: list[SourceCandidate] = []
        for candidate in candidates:
            key = (candidate.disease_id, candidate.source_type, candidate.url)
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result

    @staticmethod
    def _rank_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
        source_weight = {
            "who": 100,
            "who_don": 95,
            "web_search": 82,
            "pubmed": 78,
            "wikipedia": 70,
            "wikidata": 58,
            "msd": 20,
        }

        def score(candidate: SourceCandidate) -> tuple[float, int, str]:
            relevance = 0.0
            try:
                relevance = float((candidate.metadata or {}).get("relevance_score") or 0.0)
            except (TypeError, ValueError):
                relevance = 0.0
            has_content = 1 if candidate.content_text else 0
            return (
                source_weight.get(candidate.source_type, 30) + relevance * 10,
                has_content,
                candidate.title or candidate.url,
            )

        return sorted(candidates, key=score, reverse=True)
