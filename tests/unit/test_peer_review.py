"""Shared two-model peer review loop (src/ai/peer_review.py)."""

from __future__ import annotations

import json

import pytest

from src.ai.peer_review import (
    PROTOCOL_VERSION,
    AIOutputError,
    Completion,
    peer_review,
    review_mode,
)


class ScriptedCompleter:
    """Replays author/reviewer replies in order and records every prompt."""

    def __init__(self, author: list[dict], reviewer: list[dict], *, single_model: bool = False) -> None:
        self.replies = {"author": list(author), "reviewer": list(reviewer)}
        self.prompts: dict[str, list[str]] = {"author": [], "reviewer": []}
        self.single_model = single_model

    async def __call__(self, role: str, system: str, prompt: str) -> Completion:
        self.prompts[role].append(prompt)
        model = "model-a" if role == "author" or self.single_model else "model-b"
        return Completion(text=json.dumps(self.replies[role].pop(0)), model=model)


def _author_prompt(notes, previous):
    return json.dumps({"notes": notes, "previous": previous})


def _sanitize(draft):
    value = draft.get("value")
    if value == "invented":
        return {"value": None}, ["guardrail: dropped invented value"]
    return {"value": value}, []


async def _run(completer, *, retry_rounds=0):
    return await peer_review(
        completer,
        author_system="author",
        reviewer_system="reviewer",
        author_prompt=_author_prompt,
        reviewer_prompt=lambda draft: json.dumps(draft),
        sanitize=_sanitize,
        usable=lambda draft: bool(draft.get("value")),
        retry_rounds=retry_rounds,
    )


async def test_accept_publishes_with_cross_model_provenance():
    result = await _run(ScriptedCompleter([{"value": "x"}], [{"verdict": "accept", "score": 0.9}]))
    assert result.published and result.rounds == 1
    record = result.provenance("line.v1")
    assert record["review_protocol"] == PROTOCOL_VERSION
    assert record["review_mode"] == "cross_model"
    assert record["prompt_version"] == "line.v1"


async def test_reject_without_retry_is_final():
    completer = ScriptedCompleter([{"value": "x"}], [{"verdict": "reject", "issues": ["wrong"]}])
    result = await _run(completer)
    assert result.status == "rejected"
    assert len(completer.prompts["author"]) == 1


async def test_retry_cycle_feeds_reasons_back_and_can_publish():
    completer = ScriptedCompleter(
        [{"value": "x"}, {"value": "y"}],
        [{"verdict": "reject", "issues": ["target is an umbrella concept"]}, {"verdict": "accept"}],
    )
    result = await _run(completer, retry_rounds=2)
    assert result.published and result.payload == {"value": "y"}
    assert result.rounds == 2
    retry_prompt = json.loads(completer.prompts["author"][1])
    assert any("umbrella concept" in note for note in retry_prompt["notes"])
    assert retry_prompt["previous"] == {"value": "x"}
    assert "retry cycle started" in result.notes


async def test_retry_cycle_exhausted_stays_rejected():
    completer = ScriptedCompleter(
        [{"value": "x"}, {"value": "y"}, {"value": "z"}, {"value": "w"}],
        [{"verdict": "revise", "issues": ["a"]}] * 4,
    )
    result = await _run(completer, retry_rounds=2)
    assert result.status == "rejected"
    assert result.rounds == 4
    assert len(completer.prompts["author"]) == 4


async def test_guardrail_notes_are_recorded_and_block_unusable_accept():
    completer = ScriptedCompleter(
        [{"value": "invented"}, {"value": "ok"}],
        [{"verdict": "accept"}, {"verdict": "accept"}],
    )
    result = await _run(completer)
    assert result.published and result.payload == {"value": "ok"}
    assert "guardrail: dropped invented value" in result.notes
    assert "guardrail: accepted draft was missing required fields" in result.notes


async def test_same_model_refused_unless_single_model_mode():
    class SameModel(ScriptedCompleter):
        async def __call__(self, role, system, prompt):
            reply = await super().__call__(role, system, prompt)
            return Completion(text=reply.text, model="model-a")

    with pytest.raises(AIOutputError):
        await _run(SameModel([{"value": "x"}], [{"verdict": "accept"}]))

    result = await _run(ScriptedCompleter([{"value": "x"}], [{"verdict": "accept"}], single_model=True))
    assert result.published and result.mode == "single_model"


def test_review_mode_labels():
    assert review_mode("a", "b") == "cross_model"
    assert review_mode("a", "a") == "single_model"
    assert review_mode(None, "a") is None


async def test_initial_draft_skips_the_first_author_call():
    calls = []

    async def complete(role, system, prompt):
        calls.append(role)
        return Completion(text=json.dumps({"verdict": "accept", "score": 0.9, "issues": []}), model="reviewer-model")

    reviewed = await peer_review(
        complete,
        author_system="a",
        reviewer_system="r",
        author_prompt=lambda notes, previous: "author",
        reviewer_prompt=lambda draft: "review",
        sanitize=lambda draft: (draft, []),
        usable=lambda draft: bool(draft.get("value")),
        initial_draft=Completion(text=json.dumps({"value": "x"}), model="pipeline-model"),
    )

    assert calls == ["reviewer"]
    assert reviewed.published
    assert reviewed.author_model == "pipeline-model"
    assert reviewed.mode == "cross_model"
