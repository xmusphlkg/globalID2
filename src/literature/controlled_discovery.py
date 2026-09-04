"""Bounded, checkpointed controlled-query discovery for ordinary syncs.

The journal whitelist remains the deterministic primary feed.  These queries
rotate through the active disease catalogue and the checked-in pathogen/topic
taxonomy to recover relevant work published outside that whitelist.  Every
network call and returned record is subject to a hard cap.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any, Awaitable, Callable


CHECKPOINT_SCHEMA = "literature_controlled_discovery.v1"
_SPACE_RE = re.compile(r"\s+")
_NON_CONCEPT_DISEASE_IDS = {"D999"}
_NON_CONCEPT_DISEASE_NAMES = {"all", "other", "total", "unknown", "unspecified"}


@dataclass(frozen=True, slots=True)
class ControlledQueryBatch:
    """One transparent concept query translated for both providers."""

    query_id: str
    categories: tuple[str, ...]
    terms: tuple[str, ...]
    crossref_query: str | None
    europe_pmc_query: str | None
    pubmed_query: str | None


@dataclass(frozen=True, slots=True)
class ControlledDiscoveryResult:
    crossref_records: list[dict[str, Any]]
    europe_pmc_records: list[dict[str, Any]]
    pubmed_records: list[dict[str, Any]]
    checkpoint: dict[str, Any]


def _term(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "")).strip().replace('"', " ")


def _unique_terms(values: list[Any], *, limit: int) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = _term(value)
        key = candidate.casefold()
        if len(candidate) < 3 or key in seen:
            continue
        seen.add(key)
        output.append(candidate)
        if len(output) >= limit:
            break
    return tuple(output)


def _quoted_or(terms: tuple[str, ...]) -> str:
    return " OR ".join(f'"{term}"' for term in terms)


def _mesh_expression(names: tuple[str, ...]) -> str:
    return " OR ".join(f'MESH:"{name}"' for name in names)


def _pubmed_mesh_expression(names: tuple[str, ...]) -> str:
    return " OR ".join(f'"{name}"[MeSH Terms]' for name in names)


def _batch(
    *,
    query_id: str,
    categories: tuple[str, ...],
    terms: tuple[str, ...],
    mesh_terms: tuple[str, ...] = (),
) -> ControlledQueryBatch | None:
    if not terms:
        return None
    lexical = _quoted_or(terms)
    mesh = _mesh_expression(mesh_terms)
    pubmed_mesh = _pubmed_mesh_expression(mesh_terms)
    europe_pmc = f"({lexical}) OR ({mesh})" if mesh else f"({lexical})"
    pubmed = f"({lexical}) OR ({pubmed_mesh})" if pubmed_mesh else f"({lexical})"
    return ControlledQueryBatch(
        query_id=query_id,
        categories=categories,
        terms=terms,
        crossref_query=lexical,
        europe_pmc_query=europe_pmc,
        pubmed_query=pubmed,
    )


def build_controlled_query_batches(
    diseases: list[dict[str, Any]],
    taxonomy: dict[str, Any],
    *,
    max_terms_per_batch: int = 8,
) -> list[ControlledQueryBatch]:
    """Build a deterministic disease/pathogen/MeSH/vaccine/AMR query plan.

    The plan intentionally does not use model-generated synonyms: its exact
    terms are reviewable in the catalogue or taxonomy and become part of the
    persisted checkpoint.
    """

    limit = max(1, min(20, int(max_terms_per_batch)))
    batches: list[ControlledQueryBatch] = []
    topics = taxonomy.get("topics") or {}

    vaccine_values: list[Any] = [
        "vaccines",
        "vaccination",
        "vaccine safety",
        "adverse event following immunization",
        "adverse event following immunisation",
    ]
    for label, aliases in topics.items():
        if "vaccin" in str(label).casefold():
            vaccine_values.extend([label, *(aliases or [])])
    vaccine_terms = _unique_terms(vaccine_values, limit=limit)
    vaccine = _batch(
        query_id="vaccine:controlled",
        categories=("vaccine", "mesh"),
        terms=vaccine_terms,
        mesh_terms=("Vaccines", "Vaccination"),
    )
    if vaccine:
        batches.append(vaccine)

    amr_values: list[Any] = ["antimicrobial resistance", "antibiotic resistance", "AMR"]
    for label, aliases in topics.items():
        if "resistance" in str(label).casefold():
            amr_values.extend([label, *(aliases or [])])
    amr_terms = _unique_terms(amr_values, limit=limit)
    amr = _batch(
        query_id="amr:controlled",
        categories=("amr", "mesh"),
        terms=amr_terms,
        mesh_terms=("Drug Resistance, Microbial",),
    )
    if amr:
        batches.append(amr)

    pathogen_rows = taxonomy.get("pathogens") or {}
    for pathogen_id in sorted(pathogen_rows):
        row = pathogen_rows[pathogen_id]
        if not isinstance(row, dict):
            continue
        terms = _unique_terms([row.get("name"), *(row.get("aliases") or [])], limit=limit)
        pathogen = _batch(
            query_id=f"pathogen:{pathogen_id.removeprefix('pathogen:')}",
            categories=("pathogen", "mesh"),
            terms=terms,
            mesh_terms=terms[:1],
        )
        if pathogen:
            batches.append(pathogen)

    for disease in sorted(diseases, key=lambda item: str(item.get("disease_id") or "")):
        disease_id = _term(disease.get("disease_id"))
        disease_name = _term(disease.get("name_en"))
        if (
            not disease_id
            or disease_id.upper() in _NON_CONCEPT_DISEASE_IDS
            or disease_name.casefold() in _NON_CONCEPT_DISEASE_NAMES
        ):
            continue
        terms = _unique_terms(
            [disease_name, disease.get("name_zh"), *(disease.get("aliases") or [])],
            limit=limit,
        )
        disease_batch = _batch(
            query_id=f"disease:{disease_id}",
            categories=("disease", "mesh"),
            terms=terms,
            mesh_terms=terms[:1],
        )
        if disease_batch:
            batches.append(disease_batch)

    return batches


def _plan_fingerprint(batches: list[ControlledQueryBatch]) -> str:
    payload = [
        {
            "query_id": batch.query_id,
            "categories": batch.categories,
            "terms": batch.terms,
            "crossref": batch.crossref_query,
            "europe_pmc": batch.europe_pmc_query,
            "pubmed": batch.pubmed_query,
        }
        for batch in batches
    ]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def select_controlled_query_batches(
    batches: list[ControlledQueryBatch],
    checkpoint: dict[str, Any] | None,
    *,
    max_queries: int,
) -> tuple[list[ControlledQueryBatch], dict[str, Any]]:
    """Select a capped rotating window, retrying failed batches first."""

    fingerprint = _plan_fingerprint(batches)
    valid_checkpoint = bool(
        isinstance(checkpoint, dict)
        and checkpoint.get("schema_version") == CHECKPOINT_SCHEMA
        and checkpoint.get("plan_fingerprint") == fingerprint
    )
    previous = checkpoint if valid_checkpoint else {}
    by_id = {batch.query_id: batch for batch in batches}
    cap = max(0, min(len(batches), int(max_queries)))
    offset = int(previous.get("next_offset") or 0) % max(1, len(batches))
    retry_ids = [
        str(query_id)
        for query_id in previous.get("retry_query_ids") or []
        if str(query_id) in by_id
    ]
    selected: list[ControlledQueryBatch] = []
    selected_ids: set[str] = set()
    for query_id in retry_ids:
        if len(selected) >= cap:
            break
        selected.append(by_id[query_id])
        selected_ids.add(query_id)

    new_count = 0
    cursor = offset
    inspected = 0
    while len(selected) < cap and inspected < len(batches):
        batch = batches[cursor]
        cursor = (cursor + 1) % len(batches)
        inspected += 1
        if batch.query_id in selected_ids:
            continue
        selected.append(batch)
        selected_ids.add(batch.query_id)
        new_count += 1

    next_offset = (offset + new_count) % max(1, len(batches))
    state = {
        "schema_version": CHECKPOINT_SCHEMA,
        "plan_fingerprint": fingerprint,
        "plan_size": len(batches),
        "start_offset": offset,
        "next_offset": next_offset,
        "selected_query_ids": [batch.query_id for batch in selected],
        "selected_categories": sorted({category for batch in selected for category in batch.categories}),
        "selected_queries": [
            {
                "query_id": batch.query_id,
                "categories": list(batch.categories),
                "terms": list(batch.terms),
                "crossref": batch.crossref_query,
                "europe_pmc": batch.europe_pmc_query,
                "pubmed": batch.pubmed_query,
            }
            for batch in selected
        ],
        "retry_query_ids": [],
        "query_errors": [],
    }
    return selected, state


async def fetch_controlled_discovery(
    *,
    crossref: Any,
    europe_pmc: Any | None,
    pubmed: Any | None = None,
    batches: list[ControlledQueryBatch],
    checkpoint: dict[str, Any] | None,
    since: datetime,
    until: datetime,
    max_queries: int,
    records_per_query: int,
    max_records: int,
    concurrency: int = 4,
) -> ControlledDiscoveryResult:
    """Execute a query window with hard query, request, and record limits."""

    total_cap = max(0, int(max_records))
    provider_count = 1 + int(europe_pmc is not None) + int(pubmed is not None)
    effective_query_cap = min(
        max(0, int(max_queries)),
        total_cap // provider_count,
    )
    selected, state = select_controlled_query_batches(
        batches,
        checkpoint,
        max_queries=effective_query_cap,
    )
    per_query_cap = max(1, int(records_per_query))
    call_specs: list[tuple[ControlledQueryBatch, str]] = []
    for batch in selected:
        call_specs.append((batch, "crossref"))
        if europe_pmc is not None and batch.europe_pmc_query:
            call_specs.append((batch, "europe_pmc"))
        if pubmed is not None and batch.pubmed_query:
            call_specs.append((batch, "pubmed"))

    # Give every selected provider/query pair one result slot before spreading
    # the remainder. This prevents a low global cap from silently advancing a
    # checkpoint past batches that one provider never queried.
    request_caps = [1 for _ in call_specs]
    remaining_budget = max(0, total_cap - len(request_caps))
    while remaining_budget and any(cap < per_query_cap for cap in request_caps):
        for index, cap in enumerate(request_caps):
            if remaining_budget <= 0:
                break
            if cap >= per_query_cap:
                continue
            request_caps[index] += 1
            remaining_budget -= 1

    calls: list[
        tuple[
            ControlledQueryBatch,
            str,
            int,
            Callable[[], Awaitable[list[dict[str, Any]]]],
        ]
    ] = []
    for (batch, provider), request_cap in zip(call_specs, request_caps, strict=True):
        if provider == "crossref":
            async def request_crossref(
                current: ControlledQueryBatch = batch,
                cap: int = request_cap,
            ) -> list[dict[str, Any]]:
                return await crossref.search_works(
                    query=str(current.crossref_query or ""),
                    since=since,
                    until=until,
                    max_records=cap,
                )

            calls.append((batch, provider, request_cap, request_crossref))
        elif provider == "europe_pmc":
            async def request_europe_pmc(
                current: ControlledQueryBatch = batch,
                cap: int = request_cap,
            ) -> list[dict[str, Any]]:
                return await europe_pmc.search_recent(
                    query=str(current.europe_pmc_query or ""),
                    since=since,
                    until=until,
                    max_records=cap,
                )

            calls.append((batch, provider, request_cap, request_europe_pmc))
        else:
            async def request_pubmed(
                current: ControlledQueryBatch = batch,
                cap: int = request_cap,
            ) -> list[dict[str, Any]]:
                return await pubmed.search_recent(
                    query=str(current.pubmed_query or ""),
                    since=since,
                    until=until,
                    max_records=cap,
                )

            calls.append((batch, provider, request_cap, request_pubmed))

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def execute_call(call: Callable[[], Awaitable[list[dict[str, Any]]]]) -> Any:
        async with semaphore:
            try:
                return await call()
            except Exception as exc:  # A secondary source must not block the whitelist sync.
                return exc

    results = await asyncio.gather(*(execute_call(call) for _, _, _, call in calls))
    crossref_records: list[dict[str, Any]] = []
    europe_pmc_records: list[dict[str, Any]] = []
    pubmed_records: list[dict[str, Any]] = []
    failed_ids: set[str] = set()
    errors: list[dict[str, str]] = []
    for (batch, provider, request_cap, _), result in zip(calls, results, strict=True):
        if isinstance(result, Exception):
            failed_ids.add(batch.query_id)
            errors.append({
                "query_id": batch.query_id,
                "provider": provider,
                "error": str(result)[:500],
            })
            continue
        destination = (
            crossref_records
            if provider == "crossref"
            else europe_pmc_records
            if provider == "europe_pmc"
            else pubmed_records
        )
        for raw in result[:request_cap]:
            if not isinstance(raw, dict):
                continue
            record = dict(raw)
            record["_research_radar_discovery"] = {
                "strategy": CHECKPOINT_SCHEMA,
                "query_id": batch.query_id,
                "categories": list(batch.categories),
                "terms": list(batch.terms),
                "provider": provider,
            }
            destination.append(record)

    state.update({
        "retry_query_ids": sorted(failed_ids),
        "query_errors": errors,
        "network_calls": len(calls),
        "records_requested": sum(cap for _, _, cap, _ in calls),
        "records_returned": len(crossref_records) + len(europe_pmc_records) + len(pubmed_records),
        "crossref_records": len(crossref_records),
        "europe_pmc_records": len(europe_pmc_records),
        "pubmed_records": len(pubmed_records),
        "max_queries": max_queries,
        "records_per_query": records_per_query,
        "max_records": max_records,
    })
    return ControlledDiscoveryResult(
        crossref_records=crossref_records,
        europe_pmc_records=europe_pmc_records,
        pubmed_records=pubmed_records,
        checkpoint=state,
    )


__all__ = [
    "CHECKPOINT_SCHEMA",
    "ControlledDiscoveryResult",
    "ControlledQueryBatch",
    "build_controlled_query_batches",
    "fetch_controlled_discovery",
    "select_controlled_query_batches",
]
