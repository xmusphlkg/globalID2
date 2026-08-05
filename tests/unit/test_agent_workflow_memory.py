from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.agent_workflow import memory
from src.services.agent_workflow_service import agent_workflow_service


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def debug(self, message: str, *args: object) -> None:
        self.messages.append((message, args))

    def info(self, message: str, *args: object) -> None:
        self.messages.append((message, args))


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        qdrant=SimpleNamespace(
            url="https://qdrant.test",
            api_key="secret",
            collection_name="workflow-memory",
            vector_size=8,
        )
    )


def test_service_qdrant_client_caches_failed_initialization(monkeypatch):
    calls: list[object] = []

    def unavailable(config, logger):
        calls.append(config)
        return None

    monkeypatch.setattr(memory, "create_qdrant_client", unavailable)
    monkeypatch.setattr(agent_workflow_service, "_memory_client_checked", False)
    monkeypatch.setattr(agent_workflow_service, "_memory_client", None)

    assert agent_workflow_service._qdrant_client() is None
    assert agent_workflow_service._qdrant_client() is None
    assert calls == [agent_workflow_service.config]


def test_qdrant_search_preserves_request_and_payload_score_mapping(monkeypatch):
    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def search(self, **kwargs):
            self.calls.append(kwargs)
            return [
                SimpleNamespace(
                    id="point-1",
                    score=0.875,
                    payload={
                        "source_type": "workflow",
                        "source_name": "agent",
                        "summary": "A remembered finding",
                        "content_hash": "hash-1",
                    },
                )
            ]

    client = Client()
    results = memory.search_qdrant_memory(
        client,
        _config(),
        "query",
        3,
        embed=lambda text: [0.25, 0.75],
        ensure_collection=lambda client: None,
        unique_evidence=lambda items: items,
        logger=_Logger(),
    )

    assert client.calls == [
        {
            "collection_name": "workflow-memory",
            "query_vector": [0.25, 0.75],
            "limit": 3,
            "with_payload": True,
        }
    ]
    assert results[0].to_dict() == {
        "evidence_type": "memory",
        "source_type": "workflow",
        "source_name": "agent",
        "title": "A remembered finding",
        "url": None,
        "resolved_url": None,
        "content_snippet": "A remembered finding",
        "content_hash": "hash-1",
        "confidence": 0.7,
        "weight": 1.0,
        "metadata": {"qdrant_score": 0.875, "qdrant_id": "point-1"},
    }


def test_qdrant_search_failure_degrades_to_empty_result(monkeypatch):
    class Client:
        def search(self, **kwargs):
            raise RuntimeError("offline")

    logger = _Logger()
    assert memory.search_qdrant_memory(
        Client(),
        _config(),
        "query",
        3,
        embed=lambda text: [1.0],
        ensure_collection=lambda client: None,
        unique_evidence=lambda items: items,
        logger=logger,
    ) == []
    assert logger.messages[0][0] == "Qdrant memory search failed: %s"
    assert isinstance(logger.messages[0][1][0], RuntimeError)


def test_collection_is_created_once_with_configured_vector_size():
    class Client:
        def __init__(self) -> None:
            self.created: list[dict[str, object]] = []

        def get_collections(self):
            return SimpleNamespace(collections=[])

        def create_collection(self, **kwargs):
            self.created.append(kwargs)

    client = Client()
    memory.ensure_qdrant_collection(client, _config(), _Logger())

    assert len(client.created) == 1
    assert client.created[0]["collection_name"] == "workflow-memory"
    vector_params = client.created[0]["vectors_config"]
    assert vector_params.size == 8
    assert str(vector_params.distance).lower().endswith("cosine")


@pytest.mark.asyncio
async def test_memory_search_falls_back_to_postgres_and_maps_row():
    row = SimpleNamespace(
        summary="Postgres fallback",
        content="Longer content",
        source_ref="task-1",
        source_type="workflow",
        memory_type="workflow_summary",
        content_hash="hash-db",
        memory_uuid="memory-1",
        scope="project",
        collection_name="workflow-memory",
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [row]

    class DB:
        def __init__(self) -> None:
            self.executed = False

        async def execute(self, query):
            self.executed = True
            return Result()

    db = DB()
    qdrant_calls: list[tuple[str, int]] = []
    results = await memory.search_memory(
        db,
        prompt="fallback query",
        terms=["fallback"],
        limit=5,
        qdrant_is_enabled=lambda: True,
        qdrant_search=lambda prompt, limit: qdrant_calls.append((prompt, limit)) or [],
        unique_items=lambda items: list(dict.fromkeys(items)),
        unique_evidence=lambda items: items,
    )

    assert qdrant_calls == [("fallback query", 5)]
    assert db.executed is True
    assert results[0].metadata == {
        "memory_uuid": "memory-1",
        "scope": "project",
        "collection_name": "workflow-memory",
    }
    assert results[0].content_snippet == "Postgres fallback"


def test_upsert_preserves_memory_payload_and_embedding(monkeypatch):
    class Client:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def upsert(self, **kwargs):
            self.calls.append(kwargs)

    client = Client()
    row = SimpleNamespace(
        memory_uuid="memory-1",
        scope="project",
        memory_type="evidence",
        content="Evidence body",
        summary="Evidence summary",
        source_type="who",
        source_ref="https://who.int/item",
        content_hash="hash-1",
    )
    memory.upsert_qdrant_memory(
        client,
        _config(),
        row,
        embed=lambda text: [0.1, 0.9],
        ensure_collection=lambda client: None,
        logger=_Logger(),
    )

    assert len(client.calls) == 1
    assert client.calls[0]["collection_name"] == "workflow-memory"
    point = client.calls[0]["points"][0]
    assert point.id == "memory-1"
    assert point.vector == [0.1, 0.9]
    assert point.payload == {
        "memory_uuid": "memory-1",
        "scope": "project",
        "memory_type": "evidence",
        "content": "Evidence body",
        "summary": "Evidence summary",
        "source_type": "who",
        "source_ref": "https://who.int/item",
        "content_hash": "hash-1",
    }
