"""Disease duplicate and new-disease audit service backed by the AI model center."""

from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import uuid4

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from src.core.logging import get_logger
from src.ai.model_center import (
    clear_route_rate_limit,
    extract_retry_after_seconds,
    get_runtime_routes,
    is_model_unavailable_error,
    is_rate_limit_error,
    mark_route_rate_limited,
    mark_route_unavailable,
    update_model_check_result,
    update_provider_check_result,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STANDARD = ROOT / "configs" / "standard_diseases.csv"
DEFAULT_MAPPING_DIR = ROOT / "configs" / "mapping"
DEFAULT_CURRENT_DATA_DIR = ROOT / "data" / "current"
DEFAULT_AUDIT_LOG_PATH = ROOT / "logs" / "disease-audit.jsonl"
logger = get_logger(__name__)

FOOTNOTE_RE = re.compile(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+")
PUNCT_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
DISEASE_COLUMN_HINTS = {
    "disease",
    "disease_name",
    "diseaseid",
    "disease_id",
    "condition",
    "condition_name",
    "local_name",
    "name",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "associated",
    "by",
    "caused",
    "disease",
    "diseases",
    "fever",
    "infection",
    "infections",
    "of",
    "other",
    "syndrome",
    "the",
    "unspecified",
    "virus",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    text = FOOTNOTE_RE.sub("", text)
    text = text.replace("e. coli", "escherichia coli")
    text = text.replace("e coli", "escherichia coli")
    text = text.replace("vero toxin", "shiga toxin")
    text = text.replace("verotoxin", "shiga toxin")
    text = re.sub(r"\b(vtec|ehec)\b", "stec", text)
    text = PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def token_set(value: object) -> set[str]:
    return {token for token in clean(value).split() if token and token not in STOPWORDS}


def split_aliases(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split("|") if item.strip()]


def is_deprecated(row: dict[str, str]) -> bool:
    haystack = " ".join(
        str(row.get(key, "") or "")
        for key in ("standard_name_en", "description", "source")
    ).lower()
    return "deprecated duplicate" in haystack or "do not use" in haystack


def is_code_like(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z]\d+[a-z0-9.]*", value)) or bool(re.fullmatch(r"\d+[a-z0-9.]*", value))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _candidate_ids_from_text(text: str) -> list[str]:
    return sorted(set(re.findall(r"\bD\d{3}\b", text)))


def _catalogue_rows_for_ids(rows: list[dict[str, str]], ids: set[str]) -> list[dict[str, str]]:
    return [
        {
            "disease_id": row.get("disease_id", ""),
            "standard_name_en": row.get("standard_name_en", ""),
            "standard_name_zh": row.get("standard_name_zh", ""),
            "category": row.get("category", ""),
            "icd_10": row.get("icd_10", ""),
            "icd_11": row.get("icd_11", ""),
            "description": row.get("description", ""),
            "source": row.get("source", ""),
        }
        for row in rows
        if row.get("disease_id") in ids
    ]


class DiseaseDuplicateAuditService:
    def __init__(
        self,
        standard_path: Path = DEFAULT_STANDARD,
        mapping_dir: Path = DEFAULT_MAPPING_DIR,
        current_data_dir: Path = DEFAULT_CURRENT_DATA_DIR,
    ) -> None:
        self.standard_path = standard_path
        self.mapping_dir = mapping_dir
        self.current_data_dir = current_data_dir

    def run_local_audit(self, include_new_disease_candidates: bool = True) -> dict[str, Any]:
        rows = read_csv(self.standard_path)
        high, similar = self._standard_duplicate_findings(rows)
        mapping_review = self._mapping_conflict_findings()
        new_candidates = self._new_disease_candidates(rows) if include_new_disease_candidates else []

        return {
            "generated_at": _utcnow(),
            "standard_catalogue": str(self.standard_path),
            "mapping_directory": str(self.mapping_dir),
            "current_data_directory": str(self.current_data_dir),
            "summary": {
                "high_confidence_standard_duplicates": len(high),
                "mapping_term_review_candidates": len(mapping_review),
                "similar_name_review_candidates": len(similar),
                "new_disease_candidates": len(new_candidates),
            },
            "high_confidence_standard_duplicates": high,
            "mapping_term_review_candidates": mapping_review,
            "similar_name_review_candidates": similar,
            "new_disease_candidates": new_candidates,
            "ai_review": None,
        }

    async def run_ai_review(
        self,
        audit: dict[str, Any],
        max_candidates: int = 40,
        run_id: str | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or str(uuid4())
        rows = read_csv(self.standard_path)
        payload = self._build_ai_payload(audit, rows, max_candidates=max_candidates)
        self._record_event(
            logs,
            run_id,
            "ai_payload_prepared",
            "Prepared disease audit payload for model review",
            candidate_count=len(payload.get("candidates") or []),
            catalogue_row_count=len(payload.get("catalogue_rows") or []),
            max_candidates=max_candidates,
        )

        runtime_routes = await get_runtime_routes()
        routes = [route for route in runtime_routes if route.get("available_for_routing")]
        if not routes:
            degraded_routes = [
                route
                for route in runtime_routes
                if route.get("has_api_key") and not route.get("rate_limit_active")
            ]
            if degraded_routes:
                self._record_event(
                    logs,
                    run_id,
                    "fallback_to_degraded_routes",
                    "No healthy model-center routes are available; trying enabled routes with API keys that are not rate-limited.",
                    level="warning",
                    route_count=len(degraded_routes),
                    unavailable_routes=[self._route_log_meta(route) for route in degraded_routes],
                )
                routes = degraded_routes

        if not routes:
            self._record_event(
                logs,
                run_id,
                "no_model_routes",
                "No model-center routes can be used for disease audit.",
                level="error",
                runtime_route_count=len(runtime_routes),
                routes=[self._route_log_meta(route) for route in runtime_routes],
            )
            raise RuntimeError("No active model-center routes are available. Configure and enable a model first.")

        errors: list[dict[str, Any]] = []
        for route in routes:
            started = time.perf_counter()
            self._record_event(
                logs,
                run_id,
                "route_attempt_started",
                "Trying model route for disease audit AI review.",
                route=self._route_log_meta(route),
            )
            try:
                result = await self._chat_json(route, payload, run_id=run_id, logs=logs)
                await clear_route_rate_limit(route, "Disease duplicate audit succeeded")
                result["model_route"] = {
                    "model_id": route.get("model_id"),
                    "model_key": route.get("model_key"),
                    "model_name": route.get("model_name"),
                    "provider_key": route.get("provider_key"),
                    "provider_name": route.get("provider_name"),
                }
                self._record_event(
                    logs,
                    run_id,
                    "route_attempt_succeeded",
                    "Disease audit AI review completed with this route.",
                    duration=round(time.perf_counter() - started, 3),
                    route=self._route_log_meta(route),
                    recommendation_count=len(result.get("recommendations") or []),
                    warning_count=len(result.get("warnings") or []),
                )
                return result
            except Exception as exc:
                message = str(exc)
                hint = self.workload_failure_hint(exc)
                self._record_event(
                    logs,
                    run_id,
                    "route_attempt_failed",
                    "Model route failed during disease audit AI review.",
                    level="error",
                    duration=round(time.perf_counter() - started, 3),
                    route=self._route_log_meta(route),
                    error=message,
                    hint=hint,
                )
                errors.append(
                    {
                        "model_key": route.get("model_key"),
                        "model_name": route.get("model_name"),
                        "provider_key": route.get("provider_key"),
                        "error": message,
                        "hint": hint,
                    }
                )
                if is_rate_limit_error(exc):
                    await mark_route_rate_limited(
                        route,
                        message,
                        retry_after_seconds=extract_retry_after_seconds(exc),
                    )
                    continue
                if is_model_unavailable_error(exc):
                    await mark_route_unavailable(route, message)
                    continue
                if self._is_auth_error(exc):
                    await update_model_check_result(int(route["model_id"]), "unavailable", message)
                    await update_provider_check_result(int(route["provider_id"]), "unavailable", message)
                    continue
                if self._is_route_configuration_error(exc):
                    await update_model_check_result(int(route["model_id"]), "unavailable", message)
                    await update_provider_check_result(int(route["provider_id"]), "unavailable", message)
                    continue

        self._record_event(
            logs,
            run_id,
            "ai_review_failed",
            "All model-center routes failed for disease audit AI review.",
            level="error",
            errors=errors,
        )
        raise RuntimeError(f"All model-center routes failed for duplicate audit: {errors}")

    async def status(self, include_new_disease_candidates: bool = True) -> dict[str, Any]:
        audit = self.run_local_audit(include_new_disease_candidates=include_new_disease_candidates)
        routes = await get_runtime_routes()
        safe_routes = []
        for route in routes:
            safe_routes.append(
                {
                    "model_id": route.get("model_id"),
                    "model_key": route.get("model_key"),
                    "model_name": route.get("model_name"),
                    "provider_id": route.get("provider_id"),
                    "provider_key": route.get("provider_key"),
                    "provider_name": route.get("provider_name"),
                    "api_style": route.get("api_style"),
                    "priority": route.get("priority"),
                    "has_api_key": route.get("has_api_key"),
                    "available_for_routing": route.get("available_for_routing"),
                    "last_check_status": route.get("last_check_status"),
                    "rate_limit_active": route.get("rate_limit_active"),
                    "rate_limit_scope": route.get("rate_limit_scope"),
                    "rate_limit_cooldown_until": route.get("rate_limit_cooldown_until"),
                    "rate_limit_remaining_seconds": route.get("rate_limit_remaining_seconds"),
                }
            )

        return {
            "generated_at": _utcnow(),
            "module": "disease_duplicate_audit",
            "local_summary": audit["summary"],
            "model_center": {
                "route_count": len(safe_routes),
                "active_route_count": len([item for item in safe_routes if item.get("available_for_routing")]),
                "routes": safe_routes,
            },
        }

    def _standard_duplicate_findings(self, rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        high: list[dict[str, Any]] = []
        similar: list[dict[str, Any]] = []
        active = [row for row in rows if not is_deprecated(row)]

        for field, label in (
            ("standard_name_zh", "Chinese name"),
            ("standard_name_en", "English name"),
        ):
            grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in active:
                raw = str(row.get(field, "") or "").strip()
                key = raw if field == "standard_name_zh" else clean(raw)
                if key:
                    grouped[key].append(row)
            for key, matches in sorted(grouped.items()):
                ids = sorted({row["disease_id"] for row in matches})
                if len(ids) > 1:
                    high.append(
                        {
                            "category": "high_confidence_standard_duplicate",
                            "term": key,
                            "field": field,
                            "finding": (
                                f"Duplicate {label} `{key}`: "
                                + "; ".join(f"{row['disease_id']}={row.get('standard_name_en', '')}" for row in matches)
                            ),
                            "candidate_ids": ids,
                            "catalogue_rows": _catalogue_rows_for_ids(rows, set(ids)),
                        }
                    )

        for index, left in enumerate(active):
            left_name = left.get("standard_name_en", "")
            left_tokens = token_set(left_name)
            if len(left_tokens) < 2:
                continue
            for right in active[index + 1 :]:
                right_name = right.get("standard_name_en", "")
                right_tokens = token_set(right_name)
                if len(right_tokens) < 2:
                    continue
                if left.get("category") and right.get("category") and left.get("category") != right.get("category"):
                    continue
                overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
                ratio = SequenceMatcher(None, clean(left_name), clean(right_name)).ratio()
                if overlap >= 0.82 or ratio >= 0.92:
                    ids = [left["disease_id"], right["disease_id"]]
                    similar.append(
                        {
                            "category": "similar_name_review",
                            "finding": (
                                f"{left['disease_id']} `{left_name}` <> "
                                f"{right['disease_id']} `{right_name}` "
                                f"(token_overlap={overlap:.2f}, text_similarity={ratio:.2f})"
                            ),
                            "candidate_ids": ids,
                            "token_overlap": round(overlap, 3),
                            "text_similarity": round(ratio, 3),
                            "catalogue_rows": _catalogue_rows_for_ids(rows, set(ids)),
                        }
                    )

        return high, similar

    def _mapping_conflict_findings(self) -> list[dict[str, Any]]:
        terms: dict[str, list[dict[str, str]]] = defaultdict(list)
        for path in sorted(self.mapping_dir.glob("*.csv")):
            for row in read_csv(path):
                disease_id = str(row.get("disease_id", "") or "").strip()
                if not disease_id:
                    continue
                candidates = [
                    row.get("local_name", ""),
                    row.get("local_code", ""),
                    *split_aliases(row.get("aliases", "")),
                ]
                for term in candidates:
                    key = clean(term)
                    if len(key) < 3 or key in {"total", "unknown"} or is_code_like(key):
                        continue
                    terms[key].append(
                        {
                            "disease_id": disease_id,
                            "mapping_file": path.name,
                            "term": str(term).strip(),
                        }
                    )

        findings: list[dict[str, Any]] = []
        for key, matches in sorted(terms.items()):
            ids = sorted({item["disease_id"] for item in matches})
            if len(ids) <= 1:
                continue
            samples = "; ".join(
                f"{item['disease_id']}@{item['mapping_file']}=`{item['term']}`"
                for item in matches[:6]
            )
            findings.append(
                {
                    "category": "mapping_term_review",
                    "term": key,
                    "finding": f"Mapping term `{key}` maps to multiple disease IDs {ids}: {samples}",
                    "candidate_ids": ids,
                    "samples": matches,
                }
            )
        return findings

    def _known_terms(self, rows: list[dict[str, str]]) -> set[str]:
        known: set[str] = set()
        for row in rows:
            for value in (
                row.get("disease_id", ""),
                row.get("standard_name_en", ""),
                row.get("standard_name_zh", ""),
                row.get("icd_10", ""),
                row.get("icd_11", ""),
            ):
                key = clean(value)
                if key:
                    known.add(key)

        for path in sorted(self.mapping_dir.glob("*.csv")):
            for row in read_csv(path):
                for value in (
                    row.get("disease_id", ""),
                    row.get("local_name", ""),
                    row.get("local_code", ""),
                    *split_aliases(row.get("aliases", "")),
                ):
                    key = clean(value)
                    if key:
                        known.add(key)
        return known

    def _new_disease_candidates(self, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        if not self.current_data_dir.exists():
            return []

        known = self._known_terms(rows)
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for path in sorted(self.current_data_dir.glob("**/*.csv")):
            try:
                with path.open(newline="", encoding="utf-8-sig") as handle:
                    reader = csv.DictReader(handle)
                    fieldnames = reader.fieldnames or []
                    disease_columns = [
                        name
                        for name in fieldnames
                        if clean(name).replace(" ", "_") in DISEASE_COLUMN_HINTS
                    ]
                    if not disease_columns:
                        continue
                    country_code = path.parent.name.upper()
                    for row in reader:
                        for column in disease_columns:
                            raw = str(row.get(column, "") or "").strip()
                            key = clean(raw)
                            if not key or key in known or key in {"total", "unknown"} or is_code_like(key):
                                continue
                            if re.fullmatch(r"D\d{3}", raw.strip().upper()):
                                continue
                            item_key = (country_code, key)
                            item = grouped.setdefault(
                                item_key,
                                {
                                    "category": "new_disease_candidate",
                                    "country_code": country_code,
                                    "term": key,
                                    "raw_terms": [],
                                    "files": [],
                                    "row_count": 0,
                                    "finding": "",
                                },
                            )
                            if raw not in item["raw_terms"]:
                                item["raw_terms"].append(raw)
                            file_label = str(path.relative_to(ROOT))
                            if file_label not in item["files"]:
                                item["files"].append(file_label)
                            item["row_count"] += 1
            except (OSError, UnicodeDecodeError, csv.Error):
                continue

        output = sorted(grouped.values(), key=lambda item: (-int(item["row_count"]), item["country_code"], item["term"]))
        for item in output:
            item["finding"] = (
                f"Potential new or unmapped disease term `{item['term']}` in {item['country_code']} "
                f"({item['row_count']} rows; examples: {', '.join(item['raw_terms'][:3])})"
            )
        return output

    def _build_ai_payload(self, audit: dict[str, Any], rows: list[dict[str, str]], max_candidates: int) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for key in (
            "high_confidence_standard_duplicates",
            "mapping_term_review_candidates",
            "similar_name_review_candidates",
            "new_disease_candidates",
        ):
            candidates.extend(audit.get(key) or [])

        if max_candidates > 0:
            candidates = candidates[:max_candidates]

        ids = {
            disease_id
            for candidate in candidates
            for disease_id in (
                candidate.get("candidate_ids")
                or _candidate_ids_from_text(str(candidate.get("finding") or ""))
            )
        }

        return {
            "task": "Review infectious-disease ontology candidates for duplicate concepts and newly observed unmapped disease terms.",
            "rules": [
                "Recommend merge only when disease concepts are truly equivalent across surveillance systems.",
                "Keep separate for parent/child concepts, aggregate/subtype concepts, syndrome/infection distinctions, pregnancy/congenital variants, or materially different surveillance case definitions.",
                "For new_disease_candidate, classify as add_standard_disease only if the term looks like a legitimate infectious-disease surveillance concept not covered by existing catalogue or aliases.",
                "Prefer the older or broader canonical ID only when concepts are equivalent and the other ID is a clear duplicate.",
                "Do not invent disease IDs. For additions, return canonical_id as null and suggest a proposed English/Chinese standard name.",
                "Return JSON only.",
            ],
            "expected_json_shape": {
                "summary": {
                    "merge": 0,
                    "keep_separate": 0,
                    "add_standard_disease": 0,
                    "needs_human_review": 0,
                },
                "recommendations": [
                    {
                        "finding": "string",
                        "category": "mapping_term_review|similar_name_review|high_confidence_standard_duplicate|new_disease_candidate",
                        "candidate_ids": ["D001"],
                        "decision": "merge|keep_separate|add_standard_disease|needs_human_review",
                        "confidence": "high|medium|low",
                        "canonical_id": None,
                        "merge_ids": ["D002"],
                        "proposed_standard_name_en": None,
                        "proposed_standard_name_zh": None,
                        "rationale_zh": "string",
                        "rationale_en": "string",
                        "suggested_actions": ["string"],
                    }
                ],
                "warnings": ["string"],
            },
            "candidates": candidates,
            "catalogue_rows": _catalogue_rows_for_ids(rows, ids),
        }

    async def _chat_json(
        self,
        route: dict[str, Any],
        payload: dict[str, Any],
        run_id: str | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        style = str(route.get("api_style") or "openai_compatible").lower()
        system_prompt = (
            "You are a cautious infectious-disease ontology reviewer for a multilingual "
            "public-health surveillance database. Analyze duplicate and newly observed disease candidates. "
            "False merges are worse than leaving a candidate open. Return only valid JSON."
        )
        user_content = json.dumps(payload, ensure_ascii=False, indent=2)
        configured_max_tokens = route.get("max_tokens")
        max_tokens = int(configured_max_tokens) if configured_max_tokens is not None else 1600
        max_tokens = max(512, min(max_tokens, 3000))
        temperature = float(route.get("temperature") if route.get("temperature") is not None else 0.1)

        if style == "anthropic":
            client = AsyncAnthropic(api_key=route.get("api_key"))
            response = await client.messages.create(
                model=str(route.get("model_name") or ""),
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = "\n".join(
                str(block.text)
                for block in response.content
                if getattr(block, "type", None) == "text" and getattr(block, "text", None)
            )
        else:
            text = await self._call_openai_compatible_with_base_url_fallback(
                route=route,
                system_prompt=system_prompt,
                user_content=user_content,
                max_tokens=max_tokens,
                temperature=temperature,
                run_id=run_id,
                logs=logs,
            )

        try:
            if self._looks_like_html(text):
                raise RuntimeError(
                    "Model route returned an HTML page instead of a chat completion. "
                    "Check the provider base_url in the model center; OpenAI-compatible "
                    "gateways usually need an API path such as /v1, not the web console URL."
                )
            return json.loads(self._extract_json_object(text))
        except json.JSONDecodeError as exc:
            return {
                "summary": {
                    "merge": 0,
                    "keep_separate": 0,
                    "add_standard_disease": 0,
                    "needs_human_review": 0,
                },
                "recommendations": [],
                "warnings": [f"Model response was not valid JSON: {exc}"],
                "raw_response": text,
            }

    @staticmethod
    def _extract_json_object(text: str) -> str:
        value = text.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", value, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            value = fenced.group(1).strip()
        if value.startswith("{") and value.endswith("}"):
            return value
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            return value[start : end + 1]
        return value

    @staticmethod
    def _openai_compatible_response_text(response: Any) -> str:
        if isinstance(response, str):
            return response

        if isinstance(response, dict):
            output_text = response.get("output_text")
            if isinstance(output_text, str):
                return output_text
            choices = response.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict):
                    message = first.get("message")
                    if isinstance(message, dict):
                        content = message.get("content")
                        return DiseaseDuplicateAuditService._content_to_text(content)
                    text = first.get("text")
                    if isinstance(text, str):
                        return text
            return json.dumps(response, ensure_ascii=False)

        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str):
            return output_text

        choices = getattr(response, "choices", None)
        if choices:
            first = choices[0]
            message = getattr(first, "message", None)
            if message is not None:
                content = getattr(message, "content", None)
                return DiseaseDuplicateAuditService._content_to_text(content)
            text = getattr(first, "text", None)
            if isinstance(text, str):
                return text

        return str(response)

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                else:
                    text = getattr(item, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return str(content)

    async def _call_openai_compatible_with_base_url_fallback(
        self,
        route: dict[str, Any],
        system_prompt: str,
        user_content: str,
        max_tokens: int,
        temperature: float,
        run_id: str | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> str:
        base_url = str(route.get("base_url") or "").rstrip("/")
        base_urls: list[str | None] = [base_url or None]
        if base_url and not base_url.endswith("/v1"):
            base_urls.append(f"{base_url}/v1")

        html_error: RuntimeError | None = None
        attempt_errors: list[dict[str, Any]] = []
        for candidate_base_url in base_urls:
            self._record_event(
                logs,
                run_id,
                "openai_base_url_attempt",
                "Calling OpenAI-compatible chat completion endpoint.",
                route=self._route_log_meta(route),
                base_url=self._safe_base_url(candidate_base_url),
                max_tokens=max_tokens,
                temperature=temperature,
            )
            try:
                client = AsyncOpenAI(
                    api_key=route.get("api_key"),
                    base_url=candidate_base_url,
                    default_headers=(route.get("extra_headers") or None),
                )
                response = await client.chat.completions.create(
                    model=str(route.get("model_name") or ""),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                text = self._openai_compatible_response_text(response)
                if not self._looks_like_html(text):
                    self._record_event(
                        logs,
                        run_id,
                        "openai_base_url_succeeded",
                        "OpenAI-compatible route returned usable response text.",
                        route=self._route_log_meta(route),
                        base_url=self._safe_base_url(candidate_base_url),
                        response_chars=len(text),
                    )
                    return text
                html_error = RuntimeError(
                    "Model route returned an HTML page instead of a chat completion. "
                    "Check the provider base_url in the model center; OpenAI-compatible "
                    "gateways usually need an API path such as /v1, not the web console URL."
                )
                attempt_errors.append({"base_url": self._safe_base_url(candidate_base_url), "error": str(html_error)})
                self._record_event(
                    logs,
                    run_id,
                    "openai_base_url_failed",
                    "OpenAI-compatible route returned HTML instead of response text.",
                    level="warning",
                    route=self._route_log_meta(route),
                    base_url=self._safe_base_url(candidate_base_url),
                    error=str(html_error),
                )
            except Exception as exc:
                attempt_errors.append({"base_url": self._safe_base_url(candidate_base_url), "error": str(exc)})
                self._record_event(
                    logs,
                    run_id,
                    "openai_base_url_failed",
                    "OpenAI-compatible route failed for this base URL.",
                    level="warning",
                    route=self._route_log_meta(route),
                    base_url=self._safe_base_url(candidate_base_url),
                    error=str(exc),
                )
                if self._should_not_try_base_url_fallback(exc):
                    raise
                continue

        if html_error is not None:
            raise html_error
        raise RuntimeError(f"OpenAI-compatible route returned no usable response text: {attempt_errors}")

    @staticmethod
    def _is_auth_error(error: Exception) -> bool:
        status_code = getattr(error, "status_code", None)
        try:
            normalized_status = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            normalized_status = None
        message = str(error).lower()
        return normalized_status in {401, 403} or any(
            marker in message
            for marker in (
                "invalid_api_key",
                "incorrect api key",
                "unauthorized",
                "permission denied",
                "authentication",
            )
        )

    @staticmethod
    def workload_failure_hint(error: Exception) -> str | None:
        message = str(error).lower()
        if "bad_response_status_code" not in message and "openai_error" not in message:
            return None
        return (
            "The model-center chat test can pass while this audit fails because it uses a short "
            "marker prompt, but disease audit sends a larger structured JSON task. "
            "For OpenAI-compatible gateways such as New API/One API, bad_response_status_code "
            "usually means the upstream model/channel rejected this real workload. Check model "
            "permission, channel quota, context/output token limits, and per-model routing rules."
        )

    @staticmethod
    def _looks_like_html(text: str) -> bool:
        normalized = text.lstrip().lower()
        return normalized.startswith("<!doctype html") or normalized.startswith("<html")

    @staticmethod
    def _is_route_configuration_error(error: Exception) -> bool:
        message = str(error).lower()
        return "returned an html page" in message or "web console url" in message

    @staticmethod
    def _should_not_try_base_url_fallback(error: Exception) -> bool:
        if is_rate_limit_error(error) or DiseaseDuplicateAuditService._is_auth_error(error):
            return True
        status_code = getattr(error, "status_code", None)
        try:
            normalized_status = int(status_code) if status_code is not None else None
        except (TypeError, ValueError):
            normalized_status = None
        return normalized_status in {400, 401, 403, 429}

    @staticmethod
    def _safe_base_url(value: str | None) -> str:
        return value or "default-openai-base-url"

    @staticmethod
    def _route_log_meta(route: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_id": route.get("model_id"),
            "model_key": route.get("model_key"),
            "model_name": route.get("model_name"),
            "provider_id": route.get("provider_id"),
            "provider_key": route.get("provider_key"),
            "provider_name": route.get("provider_name"),
            "api_style": route.get("api_style"),
            "base_url": DiseaseDuplicateAuditService._safe_base_url(route.get("base_url")),
            "available_for_routing": route.get("available_for_routing"),
            "last_check_status": route.get("last_check_status"),
            "rate_limit_active": route.get("rate_limit_active"),
            "rate_limit_scope": route.get("rate_limit_scope"),
            "rate_limit_remaining_seconds": route.get("rate_limit_remaining_seconds"),
        }

    @staticmethod
    def _record_event(
        logs: list[dict[str, Any]] | None,
        run_id: str | None,
        event: str,
        message: str,
        level: str = "info",
        **metadata: Any,
    ) -> dict[str, Any]:
        record = {
            "timestamp": _utcnow(),
            "run_id": run_id or "unknown",
            "level": level,
            "event": event,
            "message": message,
            "metadata": metadata,
        }
        if logs is not None:
            logs.append(record)
        DiseaseDuplicateAuditService._append_audit_log(record)
        if level == "error":
            logger.error("Disease audit {event}: {message} | {metadata}", event=event, message=message, metadata=metadata)
        elif level == "warning":
            logger.warning("Disease audit {event}: {message} | {metadata}", event=event, message=message, metadata=metadata)
        else:
            logger.info("Disease audit {event}: {message} | {metadata}", event=event, message=message, metadata=metadata)
        return record

    @staticmethod
    def record_event(
        logs: list[dict[str, Any]] | None,
        run_id: str | None,
        event: str,
        message: str,
        level: str = "info",
        **metadata: Any,
    ) -> dict[str, Any]:
        return DiseaseDuplicateAuditService._record_event(logs, run_id, event, message, level=level, **metadata)

    @staticmethod
    def _append_audit_log(record: dict[str, Any]) -> None:
        try:
            DEFAULT_AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DEFAULT_AUDIT_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            logger.warning("Failed to write disease audit log: {error}", error=str(exc))

    @staticmethod
    def read_audit_logs(limit: int = 100) -> list[dict[str, Any]]:
        if not DEFAULT_AUDIT_LOG_PATH.exists():
            return []
        try:
            lines = DEFAULT_AUDIT_LOG_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        items: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                items.append(value)
        return items
