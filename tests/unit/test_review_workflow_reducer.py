"""The business state machine stays finite regardless of provider output."""

import json
import pytest

from src.literature.review_workflow.reducer import initial_state, reduce_completion


def reply(step, state, payload, *, kind="articles", model=None):
    return reduce_completion(
        kind,
        step,
        state,
        text=json.dumps(payload),
        model=model or ("p:a" if step == "author" else "p:b"),
    )


@pytest.mark.parametrize(
    "kind,invalid", [("articles", "reject"), ("summaries", "exclude")]
)
async def test_invalid_vocabulary_cannot_publish_even_if_reviewer_accepts(
    kind, invalid
):
    state, step, calls = initial_state(), "author", 0
    while step != "terminal":
        transition = reply(
            step,
            state,
            {"decision": invalid} if step == "author" else {"verdict": "accept"},
            kind=kind,
        )
        state, step, calls = transition.state, transition.step, calls + 1
    assert calls == 8
    assert not transition.reviewed.published
    assert transition.reviewed.payload["decision"] is None


def test_malformed_author_outputs_end_after_two_rounds_as_deferred():
    state = initial_state()
    for i in range(2):
        transition = reduce_completion(
            "articles", "author", state, text="broken", model="p:a"
        )
        state = transition.state
    assert transition.step == "terminal" and transition.outcome == "deferred"
    assert state["round"] == 2 and state["opinion"] == 1


def test_final_reviewer_correction_is_sanitized_and_adopted():
    first = reply("author", initial_state(), {"decision": "publish"})
    revision = reply(
        "reviewer", first.state, {"verdict": "revise", "issues": ["wrong decision"]}
    )
    assert revision.step == "author" and revision.state["round"] == 2
    second = reply("author", revision.state, {"decision": "hold"})
    final = reply(
        "reviewer",
        second.state,
        {
            "verdict": "revise",
            "corrected": {"decision": "exclude", "reasons": ["evidence"]},
        },
    )
    assert final.reviewed.published and final.reviewed.payload["decision"] == "exclude"
    assert final.reviewed.verdict == "revise" and final.reviewed.rounds == 2


@pytest.mark.parametrize("degraded", [False, True])
def test_same_named_model_on_different_provider_requires_explicit_degraded_policy(
    degraded,
):
    author = reply(
        "author",
        initial_state(allow_degraded_review=degraded),
        {"decision": "publish"},
        model="first:model-a",
    )
    reviewer = reply(
        "reviewer", author.state, {"verdict": "accept"}, model="second:model-a"
    )
    assert (reviewer.reviewed is not None) is degraded
    assert reviewer.outcome == (None if degraded else "deferred")


def test_reviewer_parse_failures_consume_rounds_and_only_one_second_opinion():
    state, step, calls = initial_state(), "author", 0
    while step != "terminal":
        text = '{"decision":"publish"}' if step == "author" else "broken"
        transition = reduce_completion(
            "articles",
            step,
            state,
            text=text,
            model="p:a" if step == "author" else "p:b",
        )
        state, step, calls = transition.state, transition.step, calls + 1
    assert calls == 8 and len(state["history"]) == 2
    assert not transition.reviewed.published
