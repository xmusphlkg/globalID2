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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterable
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from requests import Request
from bs4 import BeautifulSoup

from src.core import get_logger
from src.knowledge.quality import assess_knowledge_evidence

logger = get_logger(__name__)


DEFAULT_USER_AGENT = (
    "GlobalID-KnowledgeBot/1.0 "
    "(https://globalid.local; contact: globalid-maintainer@example.com)"
)

SOURCE_LICENSES = {
    "registry_definition": "Controlled registry definition; cite the canonical registry URL",
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
        "source_type": "registry_definition",
        "label": "Controlled registry definition",
        "trust_level": "high",
        "republish_policy": "classification definition only",
        "notes": "A section-scoped source for controlled classification or surveillance entities; never medical narrative evidence.",
    },
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
# MSD is available when explicitly selected for provenance inspection, but is
# metadata-only by policy and therefore cannot improve a source-grounded brief.
SOURCE_FETCH_ORDER = tuple(
    item["source_type"]
    for item in TRUSTED_SOURCE_REGISTRY
    if item["source_type"] not in {"msd", "registry_definition"}
)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_HINTS_PATH = ROOT / "configs" / "knowledge_source_hints.json"
KNOWLEDGE_SOURCE_STRATEGY_VERSION = 10


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


@dataclass
class SourceFetchReport:
    """Candidates plus adapter-level completion state for safe refreshes."""

    candidates: list[SourceCandidate]
    adapter_outcomes: dict[str, str] = field(default_factory=dict)
    adapter_durations: dict[str, float] = field(default_factory=dict)


class DiseaseKnowledgeFetcher:
    """Fetch short source candidates for one standard disease row."""

    WEB_SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"
    _host_rate_lock = threading.Lock()
    _host_last_request_at: dict[str, float] = {}
    TRUSTED_WEB_DOMAINS = (
        ("who.int", "WHO", True),
        ("cdc.gov", "CDC", True),
        ("nih.gov", "NIH/NCBI", True),
        ("ncbi.nlm.nih.gov", "NIH/NCBI", True),
        ("ecdc.europa.eu", "ECDC", True),
        ("chp.gov.hk", "Hong Kong Centre for Health Protection", True),
        ("health.gov.au", "Australian Department of Health", True),
        ("canada.ca", "Government of Canada", True),
        ("gov.uk", "UK Government", True),
        ("mhlw.go.jp", "Japan Ministry of Health, Labour and Welfare", True),
        ("niid.go.jp", "Japan National Institute of Infectious Diseases", True),
        ("bmj.com", "BMJ", False),
        ("msdmanuals.com", "MSD Manual", False),
        ("wikipedia.org", "Wikipedia", True),
    )
    # All source refreshes in one worker share these reservations.  Without a
    # shared limit, a temporarily slow public endpoint can receive dozens of
    # identical requests from parallel repair tasks, turning one timeout into
    # a self-amplifying backlog.  This is deliberately process-local: it
    # protects an upstream during a worker run without making source health a
    # durable correctness boundary.
    _adapter_health_lock = threading.Lock()
    _adapter_health: dict[str, dict[str, float | int]] = {}
    _ADAPTER_INFLIGHT_LIMITS = {
        "who": 2,
        "who_don": 1,
        "wikidata": 2,
        "web_search": 2,
        "wikipedia": 1,
        "pubmed": 2,
        "msd": 1,
    }
    _ADAPTER_COOLDOWN_SECONDS = (15, 30, 60, 120, 300)

    @classmethod
    def reset_adapter_health(cls) -> None:
        """Clear process-local transport state for a controlled lifecycle/test reset."""

        with cls._adapter_health_lock:
            cls._adapter_health.clear()

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 12,
        adapter_timeout_seconds: int = 45,
        targeted_request_timeout_seconds: int = 6,
        targeted_adapter_timeout_seconds: int = 18,
        max_excerpt_chars: int = 700,
        min_interval_seconds: float = 0.5,
        max_retries: int = 2,
        source_hints_path: Path | None = DEFAULT_SOURCE_HINTS_PATH,
    ) -> None:
        self.timeout = timeout
        self.adapter_timeout_seconds = max(1, adapter_timeout_seconds)
        self.targeted_request_timeout_seconds = max(
            1, min(targeted_request_timeout_seconds, self.timeout)
        )
        self.targeted_adapter_timeout_seconds = max(
            1, min(targeted_adapter_timeout_seconds, self.adapter_timeout_seconds)
        )
        self.max_excerpt_chars = max_excerpt_chars
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.max_retries = max(0, max_retries)
        self.source_hints_path = source_hints_path
        self._last_request_at = 0.0
        self._request_error_count = 0
        self._request_error_lock = threading.Lock()
        self._adapter_request_state = threading.local()
        self._response_cache_lock = threading.Lock()
        self._response_cache: dict[str, requests.Response] = {}
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Api-User-Agent": user_agent,
                "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            }
        )

    @classmethod
    def _reserve_adapter(cls, adapter: str) -> str | None:
        """Reserve bounded adapter capacity, returning a non-error skip state."""

        now = time.monotonic()
        with cls._adapter_health_lock:
            state = cls._adapter_health.setdefault(
                adapter,
                {"in_flight": 0, "failure_count": 0, "retry_after": 0.0},
            )
            if float(state.get("retry_after") or 0.0) > now:
                return "cooldown"
            limit = cls._ADAPTER_INFLIGHT_LIMITS.get(adapter, 1)
            if int(state.get("in_flight") or 0) >= limit:
                return "busy"
            state["in_flight"] = int(state.get("in_flight") or 0) + 1
        return None

    @classmethod
    def _finish_adapter(cls, adapter: str, outcome: str) -> None:
        """Release an adapter reservation and update a short transport circuit."""

        now = time.monotonic()
        with cls._adapter_health_lock:
            state = cls._adapter_health.setdefault(
                adapter,
                {"in_flight": 0, "failure_count": 0, "retry_after": 0.0},
            )
            state["in_flight"] = max(0, int(state.get("in_flight") or 0) - 1)
            if outcome in {"success", "success_empty"}:
                state["failure_count"] = 0
                state["retry_after"] = 0.0
                return
            if outcome != "error":
                return
            failures = min(8, int(state.get("failure_count") or 0) + 1)
            cooldown = cls._ADAPTER_COOLDOWN_SECONDS[
                min(failures - 1, len(cls._ADAPTER_COOLDOWN_SECONDS) - 1)
            ]
            state["failure_count"] = failures
            state["retry_after"] = max(float(state.get("retry_after") or 0.0), now + cooldown)

    @classmethod
    def _record_adapter_timeout(cls, adapter: str) -> None:
        """Open the circuit promptly while a timed-out request unwinds."""

        now = time.monotonic()
        with cls._adapter_health_lock:
            state = cls._adapter_health.setdefault(
                adapter,
                {"in_flight": 0, "failure_count": 0, "retry_after": 0.0},
            )
            failures = min(8, int(state.get("failure_count") or 0) + 1)
            cooldown = cls._ADAPTER_COOLDOWN_SECONDS[
                min(failures - 1, len(cls._ADAPTER_COOLDOWN_SECONDS) - 1)
            ]
            state["failure_count"] = failures
            state["retry_after"] = max(float(state.get("retry_after") or 0.0), now + cooldown)

    def fetch(
        self,
        disease: dict[str, Any],
        *,
        enabled_sources: Iterable[str] | None = None,
        target_sections: Iterable[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[SourceCandidate]:
        return self.fetch_with_report(
            disease,
            enabled_sources=enabled_sources,
            target_sections=target_sections,
            cancel_event=cancel_event,
        ).candidates

    def fetch_with_report(
        self,
        disease: dict[str, Any],
        *,
        enabled_sources: Iterable[str] | None = None,
        target_sections: Iterable[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SourceFetchReport:
        enabled = list(enabled_sources or SOURCE_FETCH_ORDER)
        target_sections = self._unique_strings(target_sections or ())
        source_hints = self._source_hints(disease)
        disease = {
            **disease,
            "target_sections": target_sections,
            "query_aliases": self._unique_strings(
                [
                    *(disease.get("query_aliases") or []),
                    *(source_hints.get("aliases") or []),
                ]
            ),
        }
        candidates: list[SourceCandidate] = []
        adapter_outcomes: dict[str, str] = {}
        adapter_durations: dict[str, float] = {}

        def is_cancelled() -> bool:
            return bool(cancel_event and cancel_event.is_set())

        registry_definition = self._build_registry_definition_source(disease)
        if registry_definition is not None:
            candidates.append(registry_definition)
            adapter_outcomes["registry_definition"] = "success"
            adapter_durations["registry_definition"] = 0.0

            # A registry definition is deliberately permitted only for an
            # entity whose whole required profile is the same scoped field.
            # Once that contract is met, remote discovery cannot improve this
            # task: it can only add unrelated medical prose and make a
            # definition-only refresh wait for slow PubMed/Wikipedia calls.
            # Keep the short-circuit narrow so infectious and other
            # multi-section profiles always retain the normal evidence sweep.
            if self._registry_definition_covers_request(
                registry_definition,
                disease=disease,
                target_sections=target_sections,
            ):
                return SourceFetchReport(
                    candidates=[registry_definition],
                    adapter_outcomes=adapter_outcomes,
                    adapter_durations=adapter_durations,
                )

        candidates.extend(
            self._fetch_configured_sources(
                disease,
                source_hints.get("sources") or [],
                enabled_sources=enabled,
                cancel_event=cancel_event,
            )
        )

        adapters = {
            "who": self._fetch_who_pages,
            "who_don": self._fetch_who_don,
            "wikidata": self._fetch_wikidata,
            "web_search": self._fetch_web_search,
            "wikipedia": self._fetch_wikipedia,
            "pubmed": self._fetch_pubmed,
            "msd": self._build_msd_metadata,
        }

        def run_adapters(
            keys: Iterable[str],
            *,
            discovery_round: str,
            disease_payload: dict[str, Any],
        ) -> None:
            selected = []
            for key in keys:
                adapter = adapters.get(key)
                if adapter is None:
                    continue
                reservation_state = self._reserve_adapter(key)
                if reservation_state is not None:
                    adapter_outcomes[key] = reservation_state
                    adapter_durations.setdefault(key, 0.0)
                    continue
                selected.append((key, adapter))
            if not selected:
                return
            if is_cancelled():
                for key, _adapter in selected:
                    self._finish_adapter(key, "cancelled")
                return

            def run_one(key: str, adapter: Any) -> tuple[str, list[SourceCandidate], str, float]:
                self._adapter_request_state.error_count = 0
                # Targeted recovery is intentionally fail-fast. A subsequent
                # discovery round gets a fresh chance, while this task avoids
                # multiplying one unavailable upstream into minute-long work.
                targeted = bool(target_sections)
                self._adapter_request_state.request_timeout_seconds = (
                    self.targeted_request_timeout_seconds if targeted else self.timeout
                )
                self._adapter_request_state.max_retries = 0 if targeted else self.max_retries
                self._adapter_request_state.is_adapter_worker = True
                started_at = time.monotonic()
                outcome = "error"
                try:
                    discovered = adapter(disease_payload)
                    for candidate in discovered:
                        candidate.metadata = {
                            **(candidate.metadata or {}),
                            "discovery_round": discovery_round,
                            "target_sections": target_sections,
                        }
                    outcome = (
                        "success"
                        if discovered
                        else "error"
                        if getattr(self._adapter_request_state, "error_count", 0) > 0
                        else "success_empty"
                    )
                    return key, discovered, outcome, time.monotonic() - started_at
                except Exception as exc:
                    logger.warning(
                        "Knowledge source adapter failed for {}/{}: {}",
                        disease.get("disease_id"),
                        key,
                        exc,
                    )
                    return key, [], "error", time.monotonic() - started_at
                finally:
                    self._finish_adapter(key, outcome)
                    self._close_adapter_session()

            # Adapters are independent discovery paths. The global per-host
            # limiter still serializes calls to one upstream while unrelated
            # WHO, PubMed and Wikipedia requests can overlap.
            executor = ThreadPoolExecutor(max_workers=min(4, len(selected)))
            futures = {
                executor.submit(run_one, key, adapter): key
                    for key, adapter in selected
            }
            adapter_timeout_seconds = (
                self.targeted_adapter_timeout_seconds
                if target_sections
                else self.adapter_timeout_seconds
            )
            deadline = time.monotonic() + float(adapter_timeout_seconds)
            pending = set(futures)
            done: set[Any] = set()
            while pending and not is_cancelled():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                completed, pending = wait(
                    pending,
                    timeout=min(0.25, remaining),
                    return_when=FIRST_COMPLETED,
                )
                done.update(completed)
            for future in done:
                try:
                    key, discovered, outcome, elapsed = future.result()
                except Exception as exc:
                    key = futures[future]
                    logger.warning(
                        "Knowledge source adapter future failed for {}/{}: {}",
                        disease.get("disease_id"),
                        key,
                        exc,
                    )
                    discovered, outcome, elapsed = [], "error", adapter_timeout_seconds
                candidates.extend(discovered)
                previous = adapter_outcomes.get(key)
                if previous == "success" or outcome == "success":
                    adapter_outcomes[key] = "success"
                elif previous == "error" or outcome == "error":
                    adapter_outcomes[key] = "error"
                else:
                    adapter_outcomes[key] = "success_empty"
                adapter_durations[key] = round(
                    adapter_durations.get(key, 0.0) + elapsed,
                    3,
                )
            for future in pending:
                key = futures[future]
                future.cancel()
                previous = adapter_outcomes.get(key)
                if is_cancelled():
                    adapter_outcomes[key] = previous or "cancelled"
                    adapter_durations[key] = round(
                        adapter_durations.get(key, 0.0),
                        3,
                    )
                    logger.info(
                        "Knowledge source adapter cancelled for {}/{} during worker shutdown",
                        disease.get("disease_id"),
                        key,
                    )
                else:
                    adapter_outcomes[key] = previous or "timeout"
                    self._record_adapter_timeout(key)
                    adapter_durations[key] = round(
                        adapter_durations.get(key, 0.0) + adapter_timeout_seconds,
                        3,
                    )
                    logger.warning(
                        "Knowledge source adapter timed out for {}/{} after {}s",
                        disease.get("disease_id"),
                        key,
                        adapter_timeout_seconds,
                    )
            executor.shutdown(wait=False, cancel_futures=True)

        run_adapters(enabled, discovery_round="primary", disease_payload=disease)

        if is_cancelled():
            return SourceFetchReport(
                candidates=self._rank_candidates(self._dedupe(candidates)),
                adapter_outcomes=adapter_outcomes,
                adapter_durations=adapter_durations,
            )

        # A restricted or weak first pass must not silently fall back to a
        # catalogue template. Automatically broaden discovery to substantive
        # public-health sources before declaring the knowledge task blocked.
        if not assess_knowledge_evidence(candidates).sufficient:
            discovered_aliases = self._discovered_query_aliases(disease, candidates)
            enriched_disease = {
                **disease,
                # Reviewed ontology/source-series aliases are the entity
                # boundary. Discovery can extend them, but must never replace
                # them with a broad label from a related condition.
                "query_aliases": self._unique_strings(
                    [
                        *(disease.get("query_aliases") or []),
                        *discovered_aliases,
                    ]
                )[:12],
            }
            enrichment_sources = ["who", "web_search", "wikipedia", "pubmed"]
            if not discovered_aliases:
                enrichment_sources = [key for key in enrichment_sources if key not in enabled]
            else:
                # Retrying a timed-out/failed upstream within the same task
                # creates no new evidence and previously doubled the worst
                # case from one adapter deadline to two. A later bounded
                # source-discovery round retries it after cooldown.
                enrichment_sources = [
                    key
                    for key in enrichment_sources
                    if adapter_outcomes.get(key) not in {"timeout", "error"}
                ]
            run_adapters(
                enrichment_sources,
                discovery_round="enrichment",
                disease_payload=enriched_disease,
            )

        return SourceFetchReport(
            candidates=self._rank_candidates(self._dedupe(candidates)),
            adapter_outcomes=adapter_outcomes,
            adapter_durations=adapter_durations,
        )

    @staticmethod
    def _registry_definition_covers_request(
        candidate: SourceCandidate,
        *,
        disease: dict[str, Any],
        target_sections: Iterable[str],
    ) -> bool:
        """Return whether one official registry source satisfies this request.

        The source builder already limits registry candidates to a
        definition-only profile. Rechecking the persisted scope here makes the
        early return explicit and resilient to future registry types.
        """
        schema = disease.get("profile_schema")
        required = schema.get("required_fields") if isinstance(schema, dict) else None
        requested = DiseaseKnowledgeFetcher._unique_strings(target_sections)
        allowed = DiseaseKnowledgeFetcher._unique_strings(
            (candidate.metadata or {}).get("allowed_sections") or ()
        )
        coverage = requested or DiseaseKnowledgeFetcher._unique_strings(required or ())
        return (
            candidate.source_type == "registry_definition"
            and candidate.review_status == "approved"
            and bool(coverage)
            and set(coverage).issubset(allowed)
            and list(required or ()) == allowed
        )

    @staticmethod
    def _build_registry_definition_source(
        disease: dict[str, Any],
    ) -> SourceCandidate | None:
        """Create narrow controlled-registry evidence for a non-clinical entity.

        Registry provenance is never promoted into medical evidence. It can
        define only a profile whose sole required field is its registry
        definition, such as an ICD residual category or a named SINAN
        surveillance concept from the controlled catalogue.
        """
        schema = disease.get("profile_schema")
        required = schema.get("required_fields") if isinstance(schema, dict) else None
        if list(required or ()) != ["definition"]:
            return None
        name = " ".join(
            str(disease.get("name_en") or disease.get("standard_name_en") or "").split()
        ).strip()
        disease_id = str(disease.get("disease_id") or "").strip()
        provenance = " ".join(str(disease.get("source") or "").split()).strip()
        description = " ".join(str(disease.get("description") or "").split()).strip()
        if not name or not disease_id:
            return None

        if provenance.casefold() == "icd-10":
            code = " ".join(str(disease.get("icd_10") or "").split()).strip()
            if not code:
                return None
            url = f"https://icd.who.int/browse10/2019/en#/{quote(code)}"
            content = (
                f"WHO ICD-10 classification entry {code}: {name}. "
                "This official entry is used only to define the classification entity."
            )
            return SourceCandidate(
                disease_id=disease_id,
                source_type="registry_definition",
                source_name="WHO ICD-10",
                url=url,
                resolved_url=url,
                title=f"ICD-10 {code}: {name}",
                license=SOURCE_LICENSES["registry_definition"],
                raw_excerpt=content,
                content_text=content,
                review_status="approved",
                metadata={
                    "authority_level": "high",
                    "content_kind": "registry_definition",
                    "registry_definition": True,
                    "registry_kind": "icd10",
                    "section_scoped": True,
                    "allowed_sections": ["definition"],
                    "relevance_score": 1.0,
                },
            )

        ontology_context = disease.get("ontology_context")
        ontology_context = ontology_context if isinstance(ontology_context, dict) else {}
        ontology_definition = " ".join(
            str(ontology_context.get("definition") or "").split()
        ).strip()
        facet_tags = ontology_context.get("facet_tags")
        facet_tags = facet_tags if isinstance(facet_tags, dict) else {}
        surveillance_scope = facet_tags.get("surveillance_scope")
        if not isinstance(surveillance_scope, (list, tuple, set)):
            surveillance_scope = [surveillance_scope]
        is_aggregate_scope = "surveillance_scope.aggregate" in {
            str(value or "").strip().casefold()
            for value in surveillance_scope
            if str(value or "").strip()
        }
        if ontology_definition and is_aggregate_scope:
            url = "https://github.com/xmusphlkg/globalID2/blob/development/configs/disease_ontology.json"
            content = (
                f"GlobalID controlled disease ontology concept {disease_id}: {name}. "
                f"Definition: {ontology_definition} "
                "This source defines the non-additive classification scope only; it does not "
                "supply clinical, epidemiological, exposure, or prevention claims."
            )
            return SourceCandidate(
                disease_id=disease_id,
                source_type="registry_definition",
                source_name="GlobalID Disease Ontology",
                url=url,
                resolved_url=url,
                title=f"GlobalID ontology concept {disease_id}: {name}",
                license=SOURCE_LICENSES["registry_definition"],
                raw_excerpt=content,
                content_text=content,
                review_status="approved",
                metadata={
                    "authority_level": "controlled",
                    "content_kind": "registry_definition",
                    "registry_definition": True,
                    "registry_kind": "globalid_aggregate_concept",
                    "catalogue_provenance": provenance,
                    "section_scoped": True,
                    "allowed_sections": ["definition"],
                    "relevance_score": 1.0,
                },
            )

        sinan_concept = re.search(
            r"\bSINAN\b.{0,120}\b(?:surveillance|occupational health)\s+concept\b",
            description,
            flags=re.IGNORECASE,
        )
        if "brazil datasus sinan" not in provenance.casefold() or not sinan_concept:
            return None
        url = "https://www.gov.br/saude/pt-br/composicao/svsa/sistemas-de-informacao/sinan"
        content = (
            "Brazil Ministry of Health describes SINAN as the national notification "
            "and investigation system for diseases and health events. "
            f"The controlled GlobalID catalogue records the Brazil DATASUS SINAN registry label "
            f"'{name}' with provenance statement: {description}. "
            "This source is used only to define the surveillance entity, not clinical, "
            "epidemiological, exposure, or prevention claims."
        )
        return SourceCandidate(
            disease_id=disease_id,
            source_type="registry_definition",
            source_name="Brazil Ministry of Health SINAN",
            url=url,
            resolved_url=url,
            title=f"SINAN registry entity: {name}",
            license=SOURCE_LICENSES["registry_definition"],
            raw_excerpt=content,
            content_text=content,
            review_status="approved",
            metadata={
                "authority_level": "high",
                "content_kind": "registry_definition",
                "registry_definition": True,
                "registry_kind": "sinan_catalogue_provenance",
                "catalogue_provenance": provenance,
                "section_scoped": True,
                "allowed_sections": ["definition"],
                "relevance_score": 1.0,
            },
        )

    @staticmethod
    def _build_icd10_definition_source(
        disease: dict[str, Any],
    ) -> SourceCandidate | None:
        """Compatibility entry point for the ICD-only source builder."""
        return DiseaseKnowledgeFetcher._build_registry_definition_source(disease)

    def _source_hints(self, disease: dict[str, Any]) -> dict[str, Any]:
        """Load optional, reviewed aliases and official entry URLs."""

        if self.source_hints_path is None or not self.source_hints_path.exists():
            return {}
        try:
            payload = json.loads(self.source_hints_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Unable to load knowledge source hints: {}", exc)
            return {}
        diseases = payload.get("diseases") if isinstance(payload, dict) else None
        if not isinstance(diseases, dict):
            return {}
        entry = diseases.get(str(disease.get("disease_id") or "").upper())
        return entry if isinstance(entry, dict) else {}

    def _fetch_configured_sources(
        self,
        disease: dict[str, Any],
        hints: Iterable[dict[str, Any]],
        *,
        enabled_sources: Iterable[str],
        cancel_event: threading.Event | None = None,
    ) -> list[SourceCandidate]:
        """Crawl reviewed official URLs before relying on search discovery."""

        enabled = set(enabled_sources)
        disease_id = str(disease["disease_id"])
        candidates: list[SourceCandidate] = []
        for hint in hints:
            if cancel_event and cancel_event.is_set():
                break
            if not isinstance(hint, dict):
                continue
            source_type = str(hint.get("source_type") or "web_search").strip()
            url = str(hint.get("url") or "").strip()
            if source_type not in enabled or not url:
                continue
            source_name = str(hint.get("source_name") or "Official source").strip()
            candidate = self._crawl_html_page(
                disease_id=disease_id,
                source_type=source_type,
                source_name=source_name,
                url=url,
                license=SOURCE_LICENSES.get(source_type, SOURCE_LICENSES["web_search"]),
                review_status="approved",
                metadata={
                    "configured_source_hint": True,
                    "authority_level": hint.get("authority_level") or "high",
                    "configured_title": hint.get("title"),
                    "matched_aliases": disease.get("query_aliases") or [],
                    "relevance_score": 1.0,
                },
            )
            if candidate is None:
                content_text = self._clip(hint.get("content_text"), limit=12_000)
                content_sections = hint.get("content_sections")
                if content_text:
                    candidate = SourceCandidate(
                        disease_id=disease_id,
                        source_type=source_type,
                        source_name=source_name,
                        url=url,
                        resolved_url=url,
                        title=str(hint.get("title") or source_name).strip(),
                        license=SOURCE_LICENSES.get(
                            source_type, SOURCE_LICENSES["web_search"]
                        ),
                        raw_excerpt=self._clip(hint.get("raw_excerpt") or content_text),
                        content_text=content_text,
                        content_sections=(
                            [dict(section) for section in content_sections if isinstance(section, dict)]
                            if isinstance(content_sections, list)
                            else []
                        ),
                        review_status="approved",
                        metadata={
                            "configured_source_hint": True,
                            "offline_reviewed_summary": True,
                            "authority_level": hint.get("authority_level") or "high",
                            "configured_title": hint.get("title"),
                            "matched_aliases": disease.get("query_aliases") or [],
                            "relevance_score": 1.0,
                            "content_kind": "reviewed_source_summary",
                        },
                    )
            else:
                # A reviewed source hint is a concise, source-faithful map of
                # a long official page.  Keep the live capture for provenance
                # and freshness, but retain those reviewed sections as well:
                # otherwise a parser layout change or an early prompt excerpt
                # can hide a known, citable section such as surveillance.
                reviewed_text = self._clip(hint.get("content_text"), limit=12_000)
                reviewed_sections = hint.get("content_sections")
                if reviewed_text:
                    existing_text = str(candidate.content_text or "").strip()
                    if reviewed_text not in existing_text:
                        candidate.content_text = "\n\n".join(
                            value
                            for value in (reviewed_text, existing_text)
                            if value
                        )
                if isinstance(reviewed_sections, list):
                    candidate.content_sections = [
                        *[
                            dict(section)
                            for section in reviewed_sections
                            if isinstance(section, dict)
                        ],
                        *(candidate.content_sections or []),
                    ]
                candidate.metadata = {
                    **(candidate.metadata or {}),
                    "configured_source_hint": True,
                    "configured_reviewed_summary": bool(reviewed_text),
                    "configured_title": hint.get("title"),
                    "matched_aliases": disease.get("query_aliases") or [],
                    "relevance_score": 1.0,
                }
            if candidate is not None:
                candidates.append(candidate)
        return candidates

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
        # A section repair needs focused grounding, not a broad catalogue
        # crawl. Keeping this bounded also prevents one slow WHO endpoint from
        # consuming the whole source-refresh deadline.
        max_names = 2 if disease.get("target_sections") else 5
        for name in names[:max_names]:
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
                    logger.debug("Skipping WHO DON item without fetchable page: {}", item_url or title)
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
        # Catalogue descriptions contain boundary prose and negations that
        # PubMed may silently tokenize into a broad query. Literature search
        # should use reviewed entity names/aliases and their safe variants;
        # descriptions remain useful for web ranking, not E-utilities terms.
        query_candidates = self._pubmed_query_candidates(disease)
        id_list: list[str] = []
        search_term_used = ""
        for search_term in self._pubmed_search_terms(
            query_candidates,
            disease.get("target_sections") or (),
        ):
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
                logger.debug("PubMed abstract XML parse failed: {}", exc)

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
            relevance_score = self._relevance_score(
                query_candidates,
                pubmed_url,
                title,
                abstract,
            )
            if relevance_score < 0.5:
                logger.debug(
                    "Skipping weak PubMed match for {}: {} ({:.2f})",
                    disease_id,
                    title,
                    relevance_score,
                )
                continue

            # Build content text from abstract
            content_text = abstract or None
            if content_text and len(content_text) > 2000:
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
                    review_status="approved" if abstract else "rejected",
                    metadata={
                        "pmid": str(pmid),
                        "doi": doi,
                        "first_author": first_author,
                        "journal": source_journal,
                        "pub_date": pub_date,
                        "matched_name": query_candidates[0] if query_candidates else disease_id,
                        "query_candidates": query_candidates[:8],
                        "search_term": search_term_used,
                        "content_kind": "abstract" if abstract else "scholarly_metadata",
                        "qualification_reason": None if abstract else "metadata_only",
                        "relevance_score": relevance_score,
                    },
                )
            )

        return candidates

    def _fetch_web_search(self, disease: dict[str, Any]) -> list[SourceCandidate]:
        """Search trusted public-health domains when direct source adapters miss a disease concept."""
        disease_id = str(disease["disease_id"])
        query_terms = self._query_candidates(disease)
        target_sections = disease.get("target_sections") or ()
        # Crossref title metadata cannot ground a missing profile section. Do
        # not spend two remote requests on it during targeted recovery.
        candidates = (
            []
            if target_sections
            else self._fetch_crossref_metadata(disease_id=disease_id, query_terms=query_terms)
        )
        candidate_limit = 3 if target_sections else 6
        max_results = 2 if target_sections else 5

        seen_urls: set[str] = set()
        for candidate in candidates:
            seen_urls.add(candidate.url)
        for query in self._web_search_queries(
            query_terms,
            target_sections,
        ):
            for item in self._duckduckgo_search(query, max_results=max_results):
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
                        review_status="approved" if may_store_page_text else "rejected",
                        metadata=metadata,
                    )
                )
                if len(candidates) >= candidate_limit:
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
                        review_status="approved" if abstract else "rejected",
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
                            "qualification_reason": "metadata_only" if not abstract else None,
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
                review_status="rejected",
                metadata={
                    "matched_name": name,
                    "metadata_only": True,
                    "qualification_reason": "metadata_only",
                },
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
    def _web_search_queries(
        query_terms: list[str],
        target_sections: Iterable[str] = (),
    ) -> list[str]:
        terms = query_terms[:8]
        if not terms:
            return []
        primary = terms[0]
        hints = DiseaseKnowledgeFetcher._section_query_hints(target_sections)
        if hints:
            # Recovery rounds are already scoped to known missing sections.
            # A few section-specific official-domain queries provide stronger
            # evidence than repeating the broad discovery sweep (11 queries).
            targeted = [
                f'"{primary}" {hint} site:who.int OR site:cdc.gov OR site:health.gov.au OR site:gov.uk OR site:canada.ca OR site:mhlw.go.jp OR site:niid.go.jp'
                for hint in hints[:2]
            ]
            targeted.append(f'"{primary}" site:who.int OR site:cdc.gov OR site:health.gov.au OR site:mhlw.go.jp OR site:niid.go.jp')
            return DiseaseKnowledgeFetcher._unique_strings(targeted)[:3]
        queries = [f'"{term}" disease' for term in terms[:3]]
        queries.extend(
            [
                f'"{primary}" site:who.int',
                f'"{primary}" site:cdc.gov',
                f'"{primary}" site:nih.gov OR site:ncbi.nlm.nih.gov',
                f'"{primary}" site:ecdc.europa.eu OR site:health.gov.au OR site:gov.uk OR site:canada.ca OR site:mhlw.go.jp OR site:niid.go.jp',
            ]
        )
        return DiseaseKnowledgeFetcher._unique_strings(queries)[:11]

    @staticmethod
    def _pubmed_search_terms(
        query_terms: list[str],
        target_sections: Iterable[str] = (),
    ) -> list[str]:
        # Non-Latin disease names can be translated by PubMed into a very broad
        # numeric token (for example, 肠病毒71型感染 became just "71"), producing
        # unrelated abstracts. Keep PubMed discovery to Latin aliases while
        # retaining localized names for the other adapters.
        terms = [
            term
            for term in query_terms[:10]
            if term
            and re.search(r"[A-Za-z]", term)
            and not re.search(r"[\u3400-\u9fff]", term)
        ]
        if not terms:
            return []
        title_abstract = " OR ".join(f'"{term}"[Title/Abstract]' for term in terms)
        all_fields = " OR ".join(f'"{term}"[All Fields]' for term in terms[:5])
        terms_out = [
            f"({title_abstract}) AND (review[pt] OR systematic review[pt] OR guideline[pt])",
            f"({title_abstract})",
            f"({all_fields}) AND (review[pt] OR systematic review[pt])",
            f"({all_fields})",
        ]
        hints = DiseaseKnowledgeFetcher._section_query_hints(target_sections)
        if hints:
            section_clause = " OR ".join(f'"{hint}"[Title/Abstract]' for hint in hints[:4])
            # Two concise attempts are sufficient for a repair: targeted
            # reviews first, then a generic review fallback. The legacy five
            # queries amplified upstream timeouts under parallel repair load.
            return [
                f"({title_abstract}) AND ({section_clause}) AND (review[pt] OR systematic review[pt] OR guideline[pt])",
                f"({title_abstract}) AND (review[pt] OR systematic review[pt] OR guideline[pt])",
            ]
        return terms_out

    @staticmethod
    def _section_query_hints(target_sections: Iterable[str]) -> list[str]:
        mapping = {
            "brief": ["public health overview"],
            "definition": ["definition etiology"],
            "clinical_features": ["clinical features symptoms complications"],
            "epidemiology": ["epidemiology burden outbreak"],
            "transmission": ["transmission route exposure mechanism"],
            "prevention": ["prevention control vaccination"],
            "surveillance_note": ["surveillance case definition reporting"],
            "risk_groups": ["risk groups vulnerable populations"],
        }
        hints: list[str] = []
        requested = {str(field) for field in target_sections}
        # Definition/clinical/epidemiology are usually present on overview
        # pages. Spend the limited targeted-query budget on the sections most
        # often responsible for incomplete profiles.
        priority = (
            "surveillance_note",
            "risk_groups",
            "prevention",
            "transmission",
            "clinical_features",
            "epidemiology",
            "definition",
            "brief",
        )
        for field in priority:
            if field not in requested:
                continue
            hints.extend(mapping.get(str(field), ()))
        return DiseaseKnowledgeFetcher._unique_strings(hints)

    def _get(self, url: str, params: dict[str, str] | None = None) -> requests.Response | None:
        cache_key = Request("GET", url, params=params).prepare().url or url
        with self._response_cache_lock:
            cached = self._response_cache.get(cache_key)
        if cached is not None:
            return cached

        request_timeout = int(
            getattr(self._adapter_request_state, "request_timeout_seconds", self.timeout)
        )
        max_retries = int(
            getattr(self._adapter_request_state, "max_retries", self.max_retries)
        )
        session = self._request_session()
        for attempt in range(max_retries + 1):
            self._throttle(url)
            try:
                response = session.get(url, params=params, timeout=request_timeout)
            except requests.RequestException as exc:
                logger.debug("Knowledge source request failed: {} ({})", url, exc)
                if attempt >= max_retries:
                    self._record_request_error()
                    return None
                time.sleep(0.35 * (attempt + 1))
                continue

            if response.status_code in {429, 500, 502, 503, 504} and attempt < max_retries:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 0.5 * (attempt + 1)
                time.sleep(delay)
                continue

            if response.status_code in {429, 500, 502, 503, 504}:
                self._record_request_error()

            with self._response_cache_lock:
                self._response_cache[cache_key] = response
            return response
        return None

    def _request_session(self) -> requests.Session | Any:
        """Use a per-adapter Session while preserving injectable test clients."""
        if not getattr(self._adapter_request_state, "is_adapter_worker", False):
            return self.session
        if not isinstance(self.session, requests.Session):
            return self.session
        session = getattr(self._adapter_request_state, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(self.session.headers)
            self._adapter_request_state.session = session
        return session

    def _close_adapter_session(self) -> None:
        session = getattr(self._adapter_request_state, "session", None)
        if session is not None:
            try:
                session.close()
            except requests.RequestException:
                pass
        for attribute in (
            "session",
            "is_adapter_worker",
            "request_timeout_seconds",
            "max_retries",
        ):
            if hasattr(self._adapter_request_state, attribute):
                delattr(self._adapter_request_state, attribute)

    def _record_request_error(self) -> None:
        with self._request_error_lock:
            self._request_error_count += 1
        self._adapter_request_state.error_count = (
            getattr(self._adapter_request_state, "error_count", 0) + 1
        )

    def _throttle(self, url: str) -> None:
        if self.min_interval_seconds <= 0:
            return
        host = urlparse(url).netloc.lower() or "unknown"
        with self._host_rate_lock:
            elapsed = time.monotonic() - self._host_last_request_at.get(host, 0.0)
            if elapsed < self.min_interval_seconds:
                time.sleep(self.min_interval_seconds - elapsed)
            now = time.monotonic()
            self._host_last_request_at[host] = now
            self._last_request_at = now

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
        query_aliases = disease.get("query_aliases")
        if isinstance(query_aliases, (list, tuple, set)):
            names.extend(query_aliases)
        for name in names:
            if name and str(name).strip() and str(name).strip() not in result:
                result.append(str(name).strip())
        return result

    @classmethod
    def _discovered_query_aliases(
        cls,
        disease: dict[str, Any],
        candidates: list[SourceCandidate],
    ) -> list[str]:
        """Harvest canonical labels/acronyms from grounded entity sources for round two."""
        existing = cls._query_candidates(disease)
        existing_keys = {value.lower() for value in existing}
        aliases: list[str] = []
        for candidate in candidates:
            if candidate.review_status != "approved" or not candidate.content_text:
                continue
            if candidate.source_type in {"wikipedia", "wikidata"}:
                title = re.sub(r"\s*[-–—]\s*Wikipedia\s*$", "", candidate.title or "", flags=re.I).strip()
                if (
                    3 <= len(title) <= 100
                    and title.lower() not in existing_keys
                    and cls._relevance_score(existing, "", title, "") >= 0.7
                ):
                    aliases.append(title)
                metadata_title = str((candidate.metadata or {}).get("candidate_title") or "").strip()
                if (
                    3 <= len(metadata_title) <= 100
                    and metadata_title.lower() not in existing_keys
                    and cls._relevance_score(existing, "", metadata_title, "") >= 0.7
                ):
                    aliases.append(metadata_title)

            excerpt = " ".join(
                part for part in (candidate.raw_excerpt, candidate.content_text) if part
            )[:1800]
            for match in re.finditer(
                r"\b([A-Za-z][A-Za-z0-9 -]{3,70}?)\s*\(([A-Z][A-Z0-9-]{1,14})\)",
                excerpt,
            ):
                long_name, acronym = match.group(1).strip(), match.group(2).strip()
                if cls._relevance_score(existing, "", long_name, acronym) >= 0.25:
                    aliases.extend([long_name, acronym])
        return cls._unique_strings(aliases)[:8]

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

    @classmethod
    def _pubmed_query_candidates(cls, disease: dict[str, Any]) -> list[str]:
        expanded: list[str] = []
        for phrase in cls._name_candidates(disease):
            if re.search(r"[\u3400-\u9fff]", phrase):
                continue
            expanded.extend(cls._phrase_variants(phrase))
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
        without_parenthetical = re.sub(r"\s*\([^)]{2,80}\)\s*", " ", text).strip()
        if without_parenthetical and without_parenthetical != text:
            variants.append(without_parenthetical)
        for suffix in (" infection", " disease", " syndrome", " virus"):
            if lower.endswith(suffix) and len(text) > len(suffix) + 2:
                variants.append(text[: -len(suffix)].strip())
        if lower.endswith(" fever"):
            variants.append(f"{text}s")
        if lower.endswith(" fevers"):
            variants.append(text[:-1])
        if "hemorrhagic" in lower:
            variants.append(re.sub("hemorrhagic", "haemorrhagic", text, flags=re.I))
        if "haemorrhagic" in lower:
            variants.append(re.sub("haemorrhagic", "hemorrhagic", text, flags=re.I))

        exposed_match = re.fullmatch(
            r"(?P<subject>child(?:ren)?|infant(?:s)?|newborn(?:s)?)\s+exposed\s+to\s+(?P<agent>.+)",
            text,
            flags=re.I,
        )
        if exposed_match:
            subject = exposed_match.group("subject")
            agent = exposed_match.group("agent").strip()
            variants.extend(
                [
                    f"{agent}-exposed {subject}",
                    f"{agent} exposed {subject}",
                ]
            )

        if re.search(r"human\s+t-(?:cell\s+)?lymphotropic\s+virus\s+1\s+or\s+2", text, re.I):
            variants.extend(
                [
                    "HTLV-1",
                    "HTLV-2",
                    "human T-cell lymphotropic virus",
                    "human T-lymphotropic virus",
                ]
            )

        if "penicillin" in lower and "pneumococcal" in lower:
            variants.extend(
                [
                    "penicillin-resistant pneumococcus",
                    "penicillin-resistant pneumococci",
                    "penicillin-nonsusceptible Streptococcus pneumoniae",
                    "penicillin-resistant Streptococcus pneumoniae",
                ]
            )

        # Numbered enteroviruses are indexed inconsistently across surveillance
        # catalogues and literature (for example a full name, a compact EV form,
        # or a hyphenated EV form). Genus letters are deliberately not guessed;
        # canonical labels discovered from entity sources are harvested later.
        enterovirus_match = re.search(
            r"\benterovirus\s+(?:type\s+)?(?:[a-z]\s*)?(\d{1,3})\b",
            text,
            flags=re.I,
        )
        if enterovirus_match:
            number = enterovirus_match.group(1)
            variants.extend(
                [
                    f"Enterovirus {number}",
                    f"EV{number}",
                    f"EV-{number}",
                ]
            )
        return cls._unique_strings(variants)

    @classmethod
    def _relevance_score(cls, query_terms: list[str], url: str, title: Any, excerpt: Any) -> float:
        haystack = cls._slug(" ".join([url or "", str(title or ""), str(excerpt or "")]))
        if not haystack:
            return 0.0
        best_score = 0.0
        for term in query_terms:
            slug = cls._slug(term)
            tokens = cls._unique_strings(
                token for token in slug.split("-") if len(token) >= 3
            )
            if not tokens:
                continue
            matched = sum(1 for token in tokens if token in haystack)
            token_score = matched / len(tokens)
            phrase_bonus = 0.45 if slug and slug in haystack else 0.0
            best_score = max(best_score, phrase_bonus + token_score * 0.65)
        return min(1.0, best_score)

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
            "registry_definition": 96,
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
