#!/usr/bin/env python3
"""Print and validate countries defined in the standard country library."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.country_library import (
    get_country_bootstrap_config,
    get_country_profile,
    get_standard_country_codes,
    validate_standard_country_registry,
)


def _fmt(v: str, width: int) -> str:
    text = v if v is not None else ""
    if len(text) <= width:
        return text.ljust(width)
    return text[: width - 1] + "…"


def main() -> int:
    codes = get_standard_country_codes()

    print("=== Standard Library Countries ===")
    print(f"count={len(codes)}")
    print()
    print(
        " | ".join(
            [
                _fmt("CODE", 4),
                _fmt("NAME", 18),
                _fmt("NAME_LOCAL", 16),
                _fmt("LANG", 8),
                _fmt("TIMEZONE", 22),
                _fmt("SOURCE_TYPE", 12),
            ]
        )
    )
    print("-" * 96)

    for code in codes:
        profile = get_country_profile(code)
        cfg = get_country_bootstrap_config(code)
        source_type = str(cfg.get("data_source_type", ""))
        print(
            " | ".join(
                [
                    _fmt(profile.code, 4),
                    _fmt(profile.name_en, 18),
                    _fmt(profile.name_local, 16),
                    _fmt(profile.language, 8),
                    _fmt(profile.timezone, 22),
                    _fmt(source_type, 12),
                ]
            )
        )

    print()
    print("=== Validation ===")
    warnings = validate_standard_country_registry()
    if not warnings:
        print("OK: no structural issues found")
        return 0

    for w in warnings:
        print(f"WARN: {w}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
