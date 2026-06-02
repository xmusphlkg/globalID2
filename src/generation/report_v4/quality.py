"""Quality gates for report v4."""

from __future__ import annotations

import re
from typing import Any

from .localization import validate_report_document

FORBIDDEN_DEATH_PATTERNS = (
    re.compile(r"\bzero deaths\b", re.I),
    re.compile(r"\bno deaths\b", re.I),
    re.compile(r"无死亡"),
    re.compile(r"0\s*例?死亡"),
    re.compile(r"死亡负担为零"),
)


class ReportV4QualityGate:
    """Run rule-based checks before a v4 document is persisted."""

    def check(self, document: dict[str, Any]) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        for message in validate_report_document(document):
            issues.append({"severity": "error", "code": "locale_contract", "message": message})
        issues.extend(self._check_death_claims(document))
        score = max(0.0, 1.0 - 0.08 * len(issues))
        passed = not any(issue.get("severity") == "error" for issue in issues)
        return {
            "passed": passed,
            "overall_score": round(score, 3),
            "issues": issues,
            "schema_version": document.get("schema_version"),
        }

    def ensure_passed(self, document: dict[str, Any]) -> dict[str, Any]:
        result = self.check(document)
        if not result.get("passed"):
            messages = "; ".join(str(issue.get("message")) for issue in result.get("issues") or [])
            raise ValueError(f"Report v4 quality gate failed: {messages}")
        return result

    @staticmethod
    def _check_death_claims(document: dict[str, Any]) -> list[dict[str, Any]]:
        death_status = ((document.get("death_reporting") or {}).get("status") or "unknown")
        if death_status in {"reported_zero", "reported_positive"}:
            return []
        text_parts: list[str] = []
        for field in ("title", "summary"):
            value = document.get(field)
            if isinstance(value, dict):
                text_parts.extend(str(item) for item in value.values())
        for findings in (document.get("key_findings") or {}).values():
            if isinstance(findings, list):
                text_parts.extend(str(item) for item in findings)
        for section in document.get("sections") or []:
            body = section.get("body") if isinstance(section, dict) else {}
            if isinstance(body, dict):
                text_parts.extend(str(item) for item in body.values())

        text = "\n".join(text_parts)
        issues = []
        for pattern in FORBIDDEN_DEATH_PATTERNS:
            if pattern.search(text):
                issues.append(
                    {
                        "severity": "error",
                        "code": "death_scope",
                        "message": f"Death wording {pattern.pattern!r} is forbidden when status is {death_status}",
                    }
                )
        return issues
