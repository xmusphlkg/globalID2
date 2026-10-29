"""Shared two-model peer review on generated knowledge briefs."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.ai.peer_review import Completion
from src.knowledge import peer_review as brief_peer_review
from src.knowledge.llm_brief_generator import AIDiseaseBriefGenerator


class FakeCompleter:
    """Scripted reviewer replies; author revisions echo ``revision``."""

    single_model = False

    def __init__(self, verdicts: list[dict], revision: dict | None = None) -> None:
        self.verdicts = list(verdicts)
        self.revision = revision or {}
        self.calls: list[str] = []

    async def __call__(self, role: str, system: str, prompt: str) -> Completion:
        self.calls.append(role)
        if role == "reviewer":
            return Completion(json.dumps(self.verdicts.pop(0)), "provider:reviewer-model")
        return Completion(json.dumps(self.revision), "provider:author-model")


@pytest.fixture
def gate(monkeypatch):
    """Replace the heavy citation/quality gate with a controllable one."""

    state = {"failures": [], "publishable": True}

    def quality(payload):
        return payload, SimpleNamespace(publishable=state["publishable"], display_mode="full")

    monkeypatch.setattr(brief_peer_review, "apply_knowledge_quality_gate", quality)
    monkeypatch.setattr(
        brief_peer_review, "validate_knowledge_citations", lambda payload, fields=None: list(state["failures"])
    )
    return state


def _payload() -> dict:
    return {
        "disease_id": "D001",
        "language": "en",
        "status": "published",
        "brief": "Measles is a viral infection [1].",
        "definition": "An acute viral disease [1].",
        "source_attribution": [{"citation_index": 1, "source_id": 9, "title": "WHO"}],
        "metadata": {},
    }


async def _review(completer, payload=None):
    return await brief_peer_review.review_brief(
        payload or _payload(),
        author_model="provider:author-model",
        language="en",
        target_sections=["brief", "definition"],
        evidence_fragments=[{"source_id": 9, "supported_sections": ["brief", "definition"], "text": "..."}],
        completer=completer,
    )


async def test_accepted_brief_is_published_without_an_author_call(gate):
    completer = FakeCompleter([{"verdict": "accept", "score": 0.9, "issues": []}])

    reviewed, final, _ = await _review(completer)

    assert reviewed.published
    assert reviewed.mode == "cross_model"
    # The generator's own draft stands in for round one.
    assert completer.calls == ["reviewer"]
    assert final["brief"] == "Measles is a viral infection [1]."


async def test_revision_is_published_only_after_the_reviewer_accepts_it(gate):
    revision = {**{f: None for f in brief_peer_review.BRIEF_FIELDS}, "brief": "Measles is a viral disease [1].",
                "definition": "An acute viral disease [1]."}
    completer = FakeCompleter(
        [{"verdict": "revise", "issues": ["brief overstates"]}, {"verdict": "accept", "score": 0.8}],
        revision=revision,
    )

    reviewed, final, _ = await _review(completer)

    assert reviewed.published
    assert completer.calls == ["reviewer", "author", "reviewer"]
    assert final["brief"] == "Measles is a viral disease [1]."
    assert any("brief overstates" in note for note in reviewed.notes)


async def test_reject_is_not_published(gate):
    reviewed, _, _ = await _review(FakeCompleter([{"verdict": "reject", "issues": ["unsupported claims"]}]))
    assert not reviewed.published


async def test_reviewer_correction_that_breaks_citations_is_not_adopted(gate):
    gate["failures"] = ["brief: unknown citation marker [7]"]
    completer = FakeCompleter(
        [
            {"verdict": "revise", "issues": ["x"], "corrected": {"brief": "Bad [7]."}},
            {"verdict": "revise", "issues": ["x"], "corrected": {"brief": "Bad [7]."}},
        ],
        revision={"brief": "Bad [7]."},
    )

    reviewed, _, _ = await _review(completer)

    assert not reviewed.published
    assert any("unknown citation marker" in note for note in reviewed.notes)


async def test_generator_publishes_only_a_peer_accepted_brief(monkeypatch):
    async def accepted(payload, **_kwargs):
        reviewed = SimpleNamespace(published=True, provenance=lambda version: {"status": "published"})
        return reviewed, {**payload, "brief": "reviewed [1]."}, SimpleNamespace(publishable=True)

    monkeypatch.setattr(brief_peer_review, "review_brief", accepted)
    manifest = SimpleNamespace(to_dict=lambda include_text=False: {"fragments": []})

    result, _, record, error = await AIDiseaseBriefGenerator._peer_review_brief(
        _payload(), SimpleNamespace(publishable=True),
        author_model="m", language="en", target_sections=["brief"], evidence_manifest=manifest,
    )

    assert error is None
    assert result["status"] == "published"
    assert result["brief"] == "reviewed [1]."
    assert result["metadata"]["peer_review"] == record


async def test_generator_returns_rejected_brief_to_draft(monkeypatch):
    async def rejected(payload, **_kwargs):
        reviewed = SimpleNamespace(published=False, provenance=lambda version: {"status": "rejected"})
        return reviewed, payload, None

    monkeypatch.setattr(brief_peer_review, "review_brief", rejected)
    manifest = SimpleNamespace(to_dict=lambda include_text=False: {"fragments": []})

    result, _, _, error = await AIDiseaseBriefGenerator._peer_review_brief(
        _payload(), SimpleNamespace(publishable=True),
        author_model="m", language="en", target_sections=["brief"], evidence_manifest=manifest,
    )

    assert error is None
    assert result["status"] == "draft"
    assert result["metadata"]["block_reason"] == "peer_review_rejected"


async def test_generator_outage_keeps_brief_as_draft_and_signals_transient_retry(monkeypatch):
    async def outage(payload, **_kwargs):
        raise RuntimeError("no route")

    monkeypatch.setattr(brief_peer_review, "review_brief", outage)
    manifest = SimpleNamespace(to_dict=lambda include_text=False: {"fragments": []})

    result, _, _, error = await AIDiseaseBriefGenerator._peer_review_brief(
        _payload(), SimpleNamespace(publishable=True),
        author_model="m", language="en", target_sections=["brief"], evidence_manifest=manifest,
    )

    assert result["status"] == "draft"
    assert result["metadata"]["block_reason"] == "peer_review_unavailable"
    # Governance treats this marker as a transient model failure.
    assert "agent completion failed" in error
