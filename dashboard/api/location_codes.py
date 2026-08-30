"""Shared helpers for country and ISO 3166-2 jurisdiction codes.

Surveillance-series definitions are registered at their publishing authority
(for example ``CN``), while observations may belong to a child jurisdiction
(for example ``country:CN-SH:national``).  Dashboard reads must preserve that
distinction: definitions come from the registry owner and facts are filtered by
the selected jurisdiction geography.
"""

from typing import Any, Mapping

COUNTRY_REGION_CODE_MAX_LENGTH = 10
PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN = r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$"


def is_subdivision_code(code: str | None) -> bool:
    """Return whether a public jurisdiction code is an ISO-style subdivision."""

    return "-" in str(code or "").strip().upper()


def jurisdiction_geography_key(code: str) -> str:
    """Return the canonical national-within-jurisdiction observation key."""

    normalized = str(code or "").strip().upper()
    if not normalized:
        raise ValueError("jurisdiction code is required")
    return f"country:{normalized}:national"


def registry_country_code(country: Any) -> str:
    """Resolve the registry owner for a country/subdivision model or mapping."""

    if isinstance(country, Mapping):
        code = str(country.get("code") or "").strip().upper()
        metadata = country.get("metadata") or country.get("metadata_") or {}
    else:
        code = str(getattr(country, "code", "") or "").strip().upper()
        metadata = getattr(country, "metadata_", {}) or {}
    if isinstance(metadata, Mapping):
        parent = str(metadata.get("parent_country_code") or "").strip().upper()
        if parent:
            return parent
    return code.split("-", 1)[0] if is_subdivision_code(code) else code
