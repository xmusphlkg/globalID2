"""Memory storage and Qdrant adapters for agent workflows."""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any, Callable

from sqlalchemy import or_, select

from src.domain import AgentWorkflowMemory
from src.services.agent_workflow.helpers import compact_text, extract_keywords
from src.services.agent_workflow_types import EvidenceRef


def qdrant_enabled(config: Any) -> bool:
    try:
        return bool(config.qdrant.url)
    except Exception:
        return False


def create_qdrant_client(config: Any, logger: Any) -> Any:
    try:
        from qdrant_client import QdrantClient

        return QdrantClient(url=config.qdrant.url, api_key=config.qdrant.api_key)
    except Exception as exc:
        logger.info("Qdrant client unavailable, falling back to Postgres memory: %s", exc)
        return None


def embed_text(text: str, vector_size: int) -> list[float]:
    vector = [0.0] * int(vector_size)
    for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", (text or "").lower()):
        digest = hashlib.md5(token.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % len(vector)
        vector[bucket] += 1.0
    norm = sum(value * value for value in vector) ** 0.5
    if norm:
        vector = [value / norm for value in vector]
    return vector


def ensure_qdrant_collection(client: Any, config: Any, logger: Any) -> None:
    try:
        from qdrant_client.http import models as qm

        collections = client.get_collections().collections
        names = {item.name for item in collections}
        if config.qdrant.collection_name in names:
            return
        client.create_collection(
            collection_name=config.qdrant.collection_name,
            vectors_config=qm.VectorParams(size=int(config.qdrant.vector_size), distance=qm.Distance.COSINE),
        )
    except Exception as exc:
        logger.debug("Qdrant collection ensure failed: %s", exc)


def qdrant_hits_to_evidence(hits: Any) -> list[EvidenceRef]:
    results: list[EvidenceRef] = []
    for hit in hits:
        payload = hit.payload or {}
        results.append(
            EvidenceRef(
                evidence_type="memory",
                source_type=str(payload.get("source_type") or "memory"),
                source_name=str(payload.get("source_name") or "memory"),
                title=str(payload.get("summary") or payload.get("content") or "memory"),
                content_snippet=compact_text(payload.get("summary") or payload.get("content") or "", 800),
                content_hash=str(payload.get("content_hash") or ""),
                confidence=0.7,
                metadata={"qdrant_score": getattr(hit, "score", None), "qdrant_id": str(hit.id)},
            )
        )
    return results


def search_qdrant_memory(
    client: Any,
    config: Any,
    prompt: str,
    limit: int,
    *,
    embed: Callable[[str], list[float]],
    ensure_collection: Callable[[Any], None],
    unique_evidence: Callable[[list[EvidenceRef]], list[EvidenceRef]],
    logger: Any,
) -> list[EvidenceRef]:
    if client is None:
        return []
    try:
        ensure_collection(client)
        hits = client.search(
            collection_name=config.qdrant.collection_name,
            query_vector=embed(prompt),
            limit=limit,
            with_payload=True,
        )
    except Exception as exc:
        logger.debug("Qdrant memory search failed: %s", exc)
        return []
    return unique_evidence(qdrant_hits_to_evidence(hits))


def memory_payload(memory: AgentWorkflowMemory) -> dict[str, Any]:
    return {
        "memory_uuid": memory.memory_uuid,
        "scope": memory.scope,
        "memory_type": memory.memory_type,
        "content": memory.content,
        "summary": memory.summary,
        "source_type": memory.source_type,
        "source_ref": memory.source_ref,
        "content_hash": memory.content_hash,
    }


def upsert_qdrant_memory(
    client: Any,
    config: Any,
    memory: AgentWorkflowMemory,
    *,
    embed: Callable[[str], list[float]],
    ensure_collection: Callable[[Any], None],
    logger: Any,
) -> None:
    if client is None:
        return
    try:
        from qdrant_client.http import models as qm

        ensure_collection(client)
        client.upsert(
            collection_name=config.qdrant.collection_name,
            points=[
                qm.PointStruct(
                    id=memory.memory_uuid,
                    vector=embed(memory.summary or memory.content or ""),
                    payload=memory_payload(memory),
                )
            ],
        )
    except Exception as exc:
        logger.debug("Qdrant upsert failed: %s", exc)


def postgres_memory_to_evidence(row: AgentWorkflowMemory) -> EvidenceRef:
    snippet = row.summary or row.content or row.source_ref or ""
    return EvidenceRef(
        evidence_type="memory",
        source_type=row.source_type or "memory",
        source_name=row.memory_type,
        title=row.summary or row.memory_type,
        content_snippet=compact_text(snippet, 800),
        content_hash=row.content_hash,
        confidence=0.7,
        metadata={
            "memory_uuid": row.memory_uuid,
            "scope": row.scope,
            "collection_name": row.collection_name,
        },
    )


async def search_memory(
    db: Any,
    *,
    prompt: str,
    terms: list[str],
    limit: int,
    qdrant_is_enabled: Callable[[], bool],
    qdrant_search: Callable[[str, int], list[EvidenceRef]],
    unique_items: Callable[[list[str]], list[str]],
    unique_evidence: Callable[[list[EvidenceRef]], list[EvidenceRef]],
) -> list[EvidenceRef]:
    unique_terms = [term for term in unique_items([compact_text(t, 80) for t in terms]) if term]
    if not unique_terms:
        unique_terms = extract_keywords(prompt, 5)

    if qdrant_is_enabled():
        qdrant_results = await asyncio.to_thread(qdrant_search, prompt, limit)
        if qdrant_results:
            return qdrant_results

    query = select(AgentWorkflowMemory).where(AgentWorkflowMemory.status == "active")
    conditions = []
    for term in unique_terms:
        like = f"%{term}%"
        conditions.append(
            or_(
                AgentWorkflowMemory.summary.ilike(like),
                AgentWorkflowMemory.content.ilike(like),
                AgentWorkflowMemory.source_ref.ilike(like),
            )
        )
    if conditions:
        query = query.where(or_(*conditions))
    rows = (await db.execute(query.order_by(AgentWorkflowMemory.created_at.desc()).limit(limit))).scalars().all()
    return unique_evidence([postgres_memory_to_evidence(row) for row in rows])
