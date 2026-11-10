"""Pure bounded peer-review transitions. Transport retries belong to the runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from src.ai.model_identity import model_identity
from src.ai.peer_review import (
    MAX_ROUNDS,
    AIOutputError,
    ReviewedOutput,
    _verdict,
    clean_enum,
    parse_json_object,
)
from src.literature.review_application import review_outcome

POLICY_VERSION = "literature-review-workflow.v1"
MAX_OPINIONS = 2


def initial_state(*, allow_degraded_review: bool = False) -> dict[str, Any]:
    return {
        "round": 1,
        "opinion": 1,
        "draft": {},
        "notes": [],
        "parsed_any": False,
        "author_model": None,
        "reviewer_model": None,
        "previous_models": [],
        "history": [],
        "allow_degraded_review": allow_degraded_review,
    }


def model_exclusions(models: list[str | None]) -> list[str]:
    return sorted(
        {
            name
            for model in models
            if model
            for name in (str(model), model_identity(model))
            if name
        }
    )


def sanitize(kind: str, draft: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    allowed = (
        ("publish", "exclude", "hold")
        if kind == "articles"
        else ("publish", "reject", "hold")
    )
    decision = clean_enum(draft.get("decision"), allowed)
    reasons = draft.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = []
    reasons = [
        " ".join(str(reason).split())[:400] for reason in reasons if str(reason).strip()
    ][:8]
    notes = (
        []
        if decision
        else [f"guardrail: invalid decision {str(draft.get('decision'))[:40]}"]
    )
    return {"decision": decision, "reasons": reasons}, notes


@dataclass(frozen=True)
class Transition:
    step: str
    state: dict[str, Any]
    reviewed: ReviewedOutput | None = None
    outcome: str | None = None


def _finish(state: dict[str, Any], reviewed: ReviewedOutput) -> Transition:
    state["history"] = [*state["history"], asdict(reviewed)]
    if review_outcome(reviewed) == "hold" and state["opinion"] < MAX_OPINIONS:
        previous = model_exclusions(
            [state.get("author_model"), state.get("reviewer_model")]
        )
        next_state = initial_state(allow_degraded_review=state["allow_degraded_review"])
        next_state.update(
            opinion=state["opinion"] + 1,
            previous_models=previous,
            history=state["history"],
        )
        return Transition("author", next_state)
    if state["opinion"] > 1:
        reviewed.notes = ["second opinion completed", *reviewed.notes]
    return Transition("terminal", state, reviewed=reviewed)


def _next_round(state: dict[str, Any]) -> Transition:
    if state["round"] < MAX_ROUNDS:
        state["round"] += 1
        return Transition("author", state)
    if not state["parsed_any"]:
        return Transition("terminal", state, outcome="deferred")
    return _finish(
        state,
        ReviewedOutput(
            "rejected",
            state["draft"],
            state.get("author_model"),
            state.get("reviewer_model"),
            "revise",
            state["round"],
            state["notes"],
            None,
            state.get("input_mode"),
        ),
    )


def reduce_completion(
    kind: str,
    step: str,
    previous: dict[str, Any],
    *,
    text: str,
    model: str | None,
    input_mode: str | None = None,
) -> Transition:
    if kind not in {"articles", "summaries"} or step not in {"author", "reviewer"}:
        raise ValueError("Invalid literature review kind or step")
    state = deepcopy(previous)
    if not (
        1 <= state["round"] <= MAX_ROUNDS and 1 <= state["opinion"] <= MAX_OPINIONS
    ):
        raise ValueError("Review exceeded its persisted business bounds")
    state[f"{step}_model"] = model
    if step == "author":
        state["input_mode"] = input_mode or state.get("input_mode")
        try:
            draft, notes = sanitize(kind, parse_json_object(text))
        except AIOutputError as exc:
            state["notes"].append(f"round {state['round']} author: {exc}")
            state["reviewer_notes"] = [
                "Reply with one complete JSON object only; the previous reply was unreadable."
            ]
            return _next_round(state)
        state.update(draft=draft, parsed_any=True)
        state["notes"].extend(notes)
        return Transition("reviewer", state)

    if not state["allow_degraded_review"] and (
        not model
        or not state.get("author_model")
        or model_identity(model) == model_identity(state["author_model"])
    ):
        state["notes"].append(
            "Independent reviewer model unavailable or provenance missing"
        )
        return Transition("terminal", state, outcome="deferred")
    try:
        verdict, issues, score, corrected = _verdict(parse_json_object(text))
    except AIOutputError as exc:
        state["notes"].append(f"round {state['round']} reviewer: {exc}")
        state["reviewer_notes"] = [
            "The reviewer's reply was unreadable; re-check every field and resend the draft."
        ]
        state["previous"] = state["draft"]
        return _next_round(state)
    state["notes"].extend(
        f"round {state['round']} reviewer: {issue}" for issue in issues
    )
    if verdict == "accept" and state["draft"].get("decision"):
        return _finish(
            state,
            ReviewedOutput(
                "published",
                state["draft"],
                state["author_model"],
                model,
                verdict,
                state["round"],
                state["notes"],
                score,
                state.get("input_mode"),
            ),
        )
    if verdict == "reject":
        return _finish(
            state,
            ReviewedOutput(
                "rejected",
                state["draft"],
                state["author_model"],
                model,
                verdict,
                state["round"],
                state["notes"],
                score,
                state.get("input_mode"),
            ),
        )
    if verdict == "accept":
        state["notes"].append("guardrail: accepted draft was missing required fields")
    if corrected and state["round"] == MAX_ROUNDS:
        draft, notes = sanitize(kind, corrected)
        state["notes"].extend(notes)
        if draft.get("decision"):
            state["notes"].append("reviewer correction adopted in final round")
            return _finish(
                state,
                ReviewedOutput(
                    "published",
                    draft,
                    state["author_model"],
                    model,
                    "revise",
                    state["round"],
                    state["notes"],
                    score,
                    state.get("input_mode"),
                ),
            )
    state["reviewer_notes"] = issues or [
        "reviewer requested a revision without details; re-check every field"
    ]
    state["previous"] = corrected or state["draft"]
    return _next_round(state)
