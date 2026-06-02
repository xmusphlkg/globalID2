"""Locale handling and language-contract checks for report v4."""

from __future__ import annotations

import re
from typing import Any

from .models import DEFAULT_LOCALE, SUPPORTED_LOCALES

BILINGUAL_MARKERS = (
    "### English",
    " / Situation Brief",
    " / Executive Summary",
    " / Key Findings",
)


def normalize_language(value: Any) -> str:
    text = str(value or DEFAULT_LOCALE).strip().lower()
    if text in {"zh", "cn", "zh_cn", "zh-cn", "bilingual", "zh_en", "zh-en"}:
        return "zh"
    return "en"


def localized(value: Any, locale: str, fallback: str = "") -> str:
    if isinstance(value, dict):
        direct = value.get(locale)
        if isinstance(direct, str):
            return direct
        default = value.get(DEFAULT_LOCALE)
        if isinstance(default, str):
            return default
        for supported in SUPPORTED_LOCALES:
            candidate = value.get(supported)
            if isinstance(candidate, str):
                return candidate
    if isinstance(value, str):
        return value
    return fallback


def localized_list(value: Any, locale: str) -> list[str]:
    if isinstance(value, dict):
        direct = value.get(locale)
        if isinstance(direct, list):
            return [str(item) for item in direct]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _latin_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]", text or ""))


def _cjk_count(text: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", text or ""))


def looks_english_heavy_in_zh(text: str) -> bool:
    latin = _latin_count(text)
    cjk = _cjk_count(text)
    return latin >= 36 and latin > max(12, cjk * 2)


def looks_chinese_heavy_in_en(text: str) -> bool:
    latin = _latin_count(text)
    cjk = _cjk_count(text)
    return cjk >= 20 and cjk > max(8, latin)


def validate_localized_text(value: Any, path: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return [f"{path} must be a locale map"]
    for locale in SUPPORTED_LOCALES:
        text = value.get(locale)
        if not isinstance(text, str) or not text.strip():
            issues.append(f"{path}.{locale} is missing")
            continue
        for marker in BILINGUAL_MARKERS:
            if marker.lower() in text.lower():
                issues.append(f"{path}.{locale} contains bilingual marker {marker!r}")
        if locale == "zh" and looks_english_heavy_in_zh(text):
            issues.append(f"{path}.zh appears to contain English fallback text")
        if locale == "en" and looks_chinese_heavy_in_en(text):
            issues.append(f"{path}.en appears to contain Chinese fallback text")
    return issues


def validate_report_document(document: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if document.get("default_locale") != DEFAULT_LOCALE:
        issues.append("default_locale must be zh")
    if set(document.get("locales") or []) != set(SUPPORTED_LOCALES):
        issues.append("locales must contain zh and en")
    issues.extend(validate_localized_text(document.get("title"), "title"))
    issues.extend(validate_localized_text(document.get("summary"), "summary"))
    key_findings = document.get("key_findings")
    if not isinstance(key_findings, dict):
        issues.append("key_findings must be a locale map")
    else:
        for locale in SUPPORTED_LOCALES:
            findings = key_findings.get(locale)
            if not isinstance(findings, list) or not findings:
                issues.append(f"key_findings.{locale} is missing")
            else:
                for index, finding in enumerate(findings):
                    issues.extend(validate_localized_text({locale: str(finding), "zh" if locale == "en" else "en": "placeholder"}, f"key_findings.{locale}[{index}]"))
    sections = document.get("sections")
    if not isinstance(sections, list) or not sections:
        issues.append("sections must be a non-empty list")
    else:
        for index, section in enumerate(sections):
            if not isinstance(section, dict):
                issues.append(f"sections[{index}] must be an object")
                continue
            issues.extend(validate_localized_text(section.get("title"), f"sections[{index}].title"))
            issues.extend(validate_localized_text(section.get("body"), f"sections[{index}].body"))
    return [issue for issue in issues if "placeholder" not in issue]
