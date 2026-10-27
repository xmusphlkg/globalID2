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
    if text in {"fr", "fra", "fr-fr", "french", "français"}:
        return "fr"
    if text in {"zh", "cn", "zh_cn", "zh-cn", "bilingual", "zh_en", "zh-en"}:
        return "zh"
    return "en"


def localized(value: Any, locale: str, fallback: str = "") -> str:
    if isinstance(value, dict):
        direct = value.get(locale)
        if isinstance(direct, str) and direct.strip():
            return direct
        if locale == "fr":
            return fallback or "Traduction française en attente."
        default = value.get(DEFAULT_LOCALE)
        if isinstance(default, str) and default.strip():
            return default
    if isinstance(value, str):
        return value
    return fallback


def localized_list(value: Any, locale: str) -> list[str]:
    if isinstance(value, dict):
        direct = value.get(locale)
        if isinstance(direct, list):
            return [str(item) for item in direct]
        if locale == "fr" and value:
            return ["Traduction française en attente."]
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
        if locale == "fr" and "Traduction française en attente" in text:
            issues.append(f"{path}.fr contains the French pending-translation marker")
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
        issues.append("locales must contain zh, en, and fr")
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
                    if not isinstance(finding, str) or not finding.strip():
                        issues.append(f"key_findings.{locale}[{index}] is missing")
                    elif locale == "zh" and looks_english_heavy_in_zh(str(finding)):
                        issues.append(f"key_findings.{locale}[{index}] appears to contain English fallback text")
                    elif locale == "en" and looks_chinese_heavy_in_en(str(finding)):
                        issues.append(f"key_findings.{locale}[{index}] appears to contain Chinese fallback text")
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

    disease_directory = document.get("disease_directory")
    if isinstance(disease_directory, list):
        for index, item in enumerate(disease_directory):
            if not isinstance(item, dict):
                issues.append(f"disease_directory[{index}] must be an object")
                continue
            analysis_sections = item.get("analysis_sections")
            if not isinstance(analysis_sections, list) or not analysis_sections:
                issues.append(f"disease_directory[{index}].analysis_sections is missing")
                continue
            for section_index, section in enumerate(analysis_sections):
                if not isinstance(section, dict):
                    issues.append(f"disease_directory[{index}].analysis_sections[{section_index}] must be an object")
                    continue
                issues.extend(validate_localized_text(
                    section.get("title_i18n"),
                    f"disease_directory[{index}].analysis_sections[{section_index}].title_i18n",
                ))
                issues.extend(validate_localized_text(
                    section.get("content_i18n"),
                    f"disease_directory[{index}].analysis_sections[{section_index}].content_i18n",
                ))
    return [issue for issue in issues if "placeholder" not in issue]
