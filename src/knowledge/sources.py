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
import time
from typing import Any, Iterable
from urllib.parse import quote

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

        return self._dedupe(candidates)

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
        names = self._name_candidates(disease)
        urls: list[tuple[str, str]] = []
        for name in names[:3]:
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
        matched_name = names[0] if names else disease_id
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
        name = str(disease.get("name_en") or disease.get("standard_name_en") or disease_id)
        safe_name = name.replace("'", "''")
        url = "https://www.who.int/api/news/diseaseoutbreaknews"
        params = {
            "$top": "3",
            "$select": "Title,ItemDefaultUrl,PublicationDate",
            "$filter": f"contains(Title,'{safe_name}')",
            "$orderby": "PublicationDate desc",
        }
        response = self._get(url, params=params)
        if response is None or response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        items = payload.get("value") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return []
        candidates: list[SourceCandidate] = []
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
                    },
                )
            )
        return candidates

    def _fetch_wikidata(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        disease_id = str(disease["disease_id"])
        name = str(disease.get("name_en") or disease.get("standard_name_en") or disease_id)
        response = self._get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities",
                "format": "json",
                "language": "en",
                "type": "item",
                "limit": "1",
                "search": name,
            },
        )
        if response is None or response.status_code != 200:
            return []
        try:
            search = response.json().get("search") or []
        except ValueError:
            return []
        if not search:
            return []
        item = search[0]
        qid = item.get("id")
        label = item.get("label") or name
        description = item.get("description") or ""
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
                metadata={"qid": qid, "matched_name": name, "content_kind": "structured"},
            )
        ]

    def _fetch_wikipedia(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        disease_id = str(disease["disease_id"])
        name = str(disease.get("name_en") or disease.get("standard_name_en") or disease_id)
        title_candidates = [
            name,
            f"{name} (disease)",
            f"{name} disease",
        ]
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
                    matched_name=name,
                    metadata={
                        "matched_name": name,
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
                        "matched_name": name,
                        "candidate_title": candidate,
                        "content_kind": "summary",
                        "canonical_url": page_url,
                    },
                )
            ]

        search_result = self._search_wikipedia(disease_id=disease_id, name=name)
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

    def _search_wikipedia(self, *, disease_id: str, name: str) -> list[SourceCandidate]:
        response = self._get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": f'"{name}" disease',
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
        name = str(disease.get("name_en") or disease.get("standard_name_en") or disease_id)

        # Search for recent review articles about this disease
        search_term = f'"{name}"[Title] AND (review[pt] OR systematic review[pt]) AND ("last 10 years"[PDat])'
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
            # Fallback: broader search without title restriction
            search_term = f'"{name}" AND (review[pt]) AND ("last 5 years"[PDat])'
            search_response = self._get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={
                    "db": "pubmed",
                    "term": search_term,
                    "retmax": "3",
                    "sort": "relevance",
                    "retmode": "json",
                },
            )
            if search_response is None or search_response.status_code != 200:
                return []

        try:
            search_data = search_response.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])
        except (ValueError, KeyError):
            return []

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
                        "matched_name": name,
                        "content_kind": "abstract",
                    },
                )
            )

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
        import re

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
