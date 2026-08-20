from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard.api.routers import situation_v3
from src.domain import SituationReviewDecisionV3


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _DB:
    def __init__(self, results):
        self.results = list(results)
        self.added: list[object] = []

    async def execute(self, _statement):
        assert self.results, "unexpected Situation review query"
        return _ScalarResult(self.results.pop(0))

    def add(self, value):
        self.added.append(value)


def _client(monkeypatch, db: _DB) -> TestClient:
    @asynccontextmanager
    async def fake_get_db():
        yield db

    monkeypatch.setattr(situation_v3, "get_db", fake_get_db)
    app = FastAPI()
    app.include_router(situation_v3.router)
    return TestClient(app)


def test_signal_review_requires_existing_signal(monkeypatch) -> None:
    response = _client(monkeypatch, _DB([None])).post(
        "/situation/v3/review/signal/missing",
        json={"action": "verify", "note": "Checked source identity"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Situation v3 signal not found"


def test_missing_calibration_summary_is_explicitly_fail_closed(monkeypatch) -> None:
    response = _client(monkeypatch, _DB([None])).get(
        "/situation/v3/calibration/latest"
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "not_available",
        "automation_supported": False,
        "reason": "No registered Situation v3.2 calibration artifact",
    }


def test_signal_verified_risk_requires_attribution_and_http_evidence(monkeypatch) -> None:
    client = _client(monkeypatch, _DB([]))
    missing_rationale = client.post(
        "/situation/v3/review/signal/signal-1",
        json={
            "action": "verify",
            "note": "Analyst checked source",
            "payload": {"risk_level": "high"},
        },
    )
    assert missing_rationale.status_code == 422

    invalid_url = client.post(
        "/situation/v3/review/signal/signal-1",
        json={
            "action": "verify",
            "note": "Analyst checked source",
            "payload": {
                "risk_level": "high",
                "risk_rationale": "Official severity assessment",
                "evidence_url": "javascript:alert(1)",
            },
        },
    )
    assert invalid_url.status_code == 422
    assert "HTTP(S)" in invalid_url.json()["detail"]


def test_signal_verification_creates_auditable_decision(monkeypatch) -> None:
    db = _DB([42])
    response = _client(monkeypatch, db).post(
        "/situation/v3/review/signal/signal-1",
        json={
            "action": "verify",
            "actor": "analyst@example.test",
            "note": " Source identity and current period verified ",
            "payload": {
                "risk_level": "moderate",
                "risk_rationale": "Attributable expert assessment",
                "evidence_url": "https://example.test/assessment",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["decision_id"].startswith("decision-v3:")
    assert len(db.added) == 1
    decision = db.added[0]
    assert isinstance(decision, SituationReviewDecisionV3)
    assert decision.action == "verify"
    assert decision.note == "Source identity and current period verified"
    assert decision.payload["risk_level"] == "moderate"


def _event(
    cluster_id: str,
    *,
    review_state: str,
    geographies,
    first: str,
    last: str,
    items,
    disease_id: str = "D_TEST",
):
    return SimpleNamespace(
        cluster_id=cluster_id,
        disease_id=disease_id,
        review_state=review_state,
        geographies=geographies,
        first_published_at=first,
        last_published_at=last,
        corrected_payload={},
        items=items,
    )


def test_event_merge_moves_updates_and_preserves_union_timeline(monkeypatch) -> None:
    moved = SimpleNamespace(update_id="update-new", cluster=None)
    duplicate = SimpleNamespace(update_id="update-old", cluster=None)
    source = _event(
        "event-source",
        review_state="unreviewed",
        geographies=[{"code": "UG", "name": "Uganda"}],
        first="2026-08-01",
        last="2026-08-15",
        items=[duplicate, moved],
    )
    existing = SimpleNamespace(update_id="update-old", cluster=None)
    target = _event(
        "event-target",
        review_state="publish",
        geographies=[{"code": "CD", "name": "DR Congo"}],
        first="2026-08-03",
        last="2026-08-10",
        items=[existing],
    )
    db = _DB([source, target])
    response = _client(monkeypatch, db).post(
        "/situation/v3/review/event/event-source",
        json={
            "action": "merge",
            "note": "Duplicate official-event timeline",
            "payload": {"merged_into_cluster_id": "event-target"},
        },
    )
    assert response.status_code == 200
    assert source.review_state == "merge"
    assert source.corrected_payload == {"merged_into_cluster_id": "event-target"}
    assert target.geographies == [
        {"code": "CD", "name": "DR Congo"},
        {"code": "UG", "name": "Uganda"},
    ]
    assert target.first_published_at == "2026-08-01"
    assert target.last_published_at == "2026-08-15"
    assert moved.cluster is target
    assert duplicate.cluster is None
    assert isinstance(db.added[-1], SituationReviewDecisionV3)


def test_event_cannot_merge_into_inactive_target(monkeypatch) -> None:
    source = _event(
        "event-source",
        review_state="unreviewed",
        geographies=[],
        first="2026-08-01",
        last="2026-08-02",
        items=[],
    )
    target = _event(
        "event-target",
        review_state="suppress",
        geographies=[],
        first="2026-08-01",
        last="2026-08-02",
        items=[],
    )
    response = _client(monkeypatch, _DB([source, target])).post(
        "/situation/v3/review/event/event-source",
        json={
            "action": "merge",
            "note": "Duplicate official-event timeline",
            "payload": {"merged_into_cluster_id": "event-target"},
        },
    )
    assert response.status_code == 422
    assert "active event cluster" in response.json()["detail"]


def test_event_cannot_merge_across_diseases(monkeypatch) -> None:
    source = _event(
        "event-source",
        review_state="unreviewed",
        geographies=[],
        first="2026-08-01",
        last="2026-08-02",
        items=[],
        disease_id="D_ONE",
    )
    target = _event(
        "event-target",
        review_state="publish",
        geographies=[],
        first="2026-08-01",
        last="2026-08-02",
        items=[],
        disease_id="D_TWO",
    )
    response = _client(monkeypatch, _DB([source, target])).post(
        "/situation/v3/review/event/event-source",
        json={
            "action": "merge",
            "note": "Suspected duplicate event timeline",
            "payload": {"merged_into_cluster_id": "event-target"},
        },
    )
    assert response.status_code == 422
    assert "same disease" in response.json()["detail"]


def test_event_correction_rejects_payload_that_cannot_be_applied(monkeypatch) -> None:
    client = _client(monkeypatch, _DB([]))
    response = client.post(
        "/situation/v3/review/event/event-source",
        json={
            "action": "correct",
            "note": "Correct malformed geography",
            "payload": {"geographies": "Uganda"},
        },
    )
    assert response.status_code == 422
    assert "valid geographies" in response.json()["detail"]


def test_report_review_actions_use_publication_endpoint(monkeypatch) -> None:
    response = _client(monkeypatch, _DB([])).post(
        "/situation/v3/review/report/report-1",
        json={"action": "publish", "note": "Publish this report"},
    )
    assert response.status_code == 422
    assert "publication rollback endpoint" in response.json()["detail"]
