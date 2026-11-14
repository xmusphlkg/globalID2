"""Stable summary fields, fingerprints and response contracts, without model calls."""
from __future__ import annotations
import hashlib
import json
import re
from typing import Any
from src.domain import LiteratureArticle


SUMMARY_FIELDS = (
    "research_question",
    "study_design",
    "population_setting",
    "main_findings",
    "public_health_relevance",
    "limitations",
    "gids_interpretation",
)



ALLOWED_EVIDENCE = {"title", "abstract", "bibliographic_metadata", "classifier_links"}



SUPPORTED_SUMMARY_LANGUAGES = ("en", "zh", "fr")



TRANSLATION_LANGUAGES = ("zh", "fr")



LANGUAGE_NAMES = {"en": "English", "zh": "Simplified Chinese", "fr": "French"}



TRANSLATION_PROVENANCE_VERSION = "translation-provenance.v1"



def canonical_summary_fingerprint(fields: dict[str, str | None]) -> str:
    """Fingerprint the English semantic contract used for a translation."""
    canonical = {field: fields.get(field) for field in SUMMARY_FIELDS}
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()



def source_fingerprint(article: LiteratureArticle) -> str:
    payload = "\n".join(
        str(value or "")
        for value in (
            article.title,
            article.doi,
            article.journal,
            article.published_at.isoformat() if article.published_at else None,
            article.abstract_text,
            article.integrity_status,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()



def _parse_json(value: str) -> dict[str, Any]:
    text = value.strip()

    def _parse_first_object(payload: str) -> Any:
        decoder = json.JSONDecoder()
        start = 0
        while start < len(payload):
            open_idx = payload.find("{", start)
            if open_idx < 0:
                break
            try:
                parsed = decoder.raw_decode(payload, open_idx)[0]
            except json.JSONDecodeError:
                start = open_idx + 1
                continue
            return parsed
        return None

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = _parse_first_object(fenced.group(1))
        if isinstance(candidate, dict):
            return candidate
        text = fenced.group(1)
    else:
        candidate = _parse_first_object(text)
        if isinstance(candidate, dict):
            return candidate
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text) if text else {}
    if not isinstance(parsed, dict):
        raise ValueError("Literature enrichment response must be a JSON object")
    return parsed



def _has_long_verbatim_overlap(source: str, output: str, *, words: int = 12) -> bool:
    source_words = re.findall(r"[a-z0-9]+", source.lower())
    output_words = re.findall(r"[a-z0-9]+", output.lower())
    if len(source_words) < words or len(output_words) < words:
        return False
    source_ngrams = {tuple(source_words[index : index + words]) for index in range(len(source_words) - words + 1)}
    return any(
        tuple(output_words[index : index + words]) in source_ngrams
        for index in range(len(output_words) - words + 1)
    )
