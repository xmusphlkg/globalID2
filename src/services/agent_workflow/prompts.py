"""Prompt builders for the agent workflow roles.

These functions intentionally contain no service state or external I/O so prompt
contracts can be reviewed and tested independently from workflow execution.
"""
from __future__ import annotations

import json
from typing import Any


def planner_system_prompt() -> str:
    return (
        "You are the planner for a generic multi-expert research workflow. "
        "Return valid JSON only. "
        "Choose the smallest useful plan. "
        "Use step_type values only from: web_search, db_lookup, memory_lookup, analysis, internal_action, review, finalize. "
        "Each node must include step_key, step_type, title, instruction, search_queries, target_tables, action, parameters, depends_on, max_results, confidence, metadata. "
        "Prefer evidence gathering before analysis. "
        "If the prompt implies an internal action and it is allowed, include one internal_action step. "
        "Do not create unnecessary loops. "
        "The JSON object should contain risk_level, summary, and plan."
    )


def analysis_system_prompt() -> str:
    return (
        "You are the analyst in a generic multi-expert research workflow. "
        "Return valid JSON only. "
        "Derive findings only from the provided evidence and context. "
        "Every finding must reference supporting evidence hashes or URLs when possible. "
        "If evidence is insufficient, state an open question instead of guessing."
    )


def review_system_prompt() -> str:
    return (
        "You are the reviewer in a generic multi-expert research workflow. "
        "Return valid JSON only. "
        "Check whether the findings are supported by the evidence, whether actions are consistent, and whether there are unsupported claims. "
        "Report issues clearly and provide follow-up search queries when gaps remain."
    )


def synthesizer_system_prompt() -> str:
    return (
        "You are the synthesizer in a generic multi-expert research workflow. "
        "Return valid JSON only. "
        "Produce a concise evidence report with summary, findings, citations, actions_taken, artifacts, open_questions, run_log_digest, risk_level, status, and confidence. "
        "Do not add unsupported claims."
    )


def planner_prompt(
    *,
    prompt: str,
    task_uuid: str,
    task_name: str,
    payload: dict[str, Any],
    search_scope: str,
    allowed_actions: set[str],
) -> str:
    request = {
        "task_uuid": task_uuid,
        "task_name": task_name,
        "prompt": prompt,
        "mode": payload.get("mode") or "research",
        "output_format": payload.get("output_format") or "evidence_report",
        "search_scope": search_scope,
        "allowed_actions": sorted(allowed_actions),
        "memory_scope": payload.get("memory_scope") or "project",
        "country_id": payload.get("country_id"),
        "hints": payload.get("hints") or {},
    }
    return (
        "Create a compact research plan for this task. "
        "Keep the number of steps as small as possible. "
        "If the task only needs evidence gathering, use web_search / db_lookup / memory_lookup / analysis / review / finalize. "
        "If an internal action is required, insert exactly one internal_action node and set its action and parameters. "
        "Output JSON with keys: risk_level, summary, plan. "
        "The plan must be an array of nodes, each with: step_key, step_type, title, instruction, search_queries, target_tables, action, parameters, depends_on, max_results, confidence, metadata.\n\n"
        + json.dumps(request, ensure_ascii=False, indent=2)
    )


def analysis_prompt(payload: dict[str, Any]) -> str:
    return (
        "Analyze the evidence and produce structured findings. "
        "Output JSON with keys: summary, findings, open_questions, confidence, evidence_map, notes.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def review_prompt(payload: dict[str, Any]) -> str:
    return (
        "Review the analysis against the evidence. "
        "Output JSON with keys: approved, score, issues, missing_evidence, rewrite_instruction, follow_up_search_queries, assessment, confidence.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def synthesizer_prompt(payload: dict[str, Any]) -> str:
    return (
        "Synthesize the final evidence report. "
        "Output JSON with keys: summary, findings, citations, actions_taken, artifacts, open_questions, run_log_digest, risk_level, status, confidence.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
