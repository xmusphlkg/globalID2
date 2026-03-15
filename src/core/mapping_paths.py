"""Mapping file path helpers."""

from __future__ import annotations

from pathlib import Path


def mapping_dir(root: Path) -> Path:
    return root / "configs" / "mapping"


def mapping_file_name(country_code: str) -> str:
    return f"{country_code.strip().lower()}.csv"


def expected_mapping_file(root: Path, country_code: str) -> Path:
    return mapping_dir(root) / mapping_file_name(country_code)


def legacy_mapping_file(root: Path, country_code: str) -> Path:
    return root / "configs" / country_code.strip().lower() / "disease_mapping.csv"


def resolve_mapping_file(root: Path, country_code: str, allow_legacy: bool = True) -> Path:
    expected = expected_mapping_file(root, country_code)
    if expected.exists() or not allow_legacy:
        return expected

    legacy = legacy_mapping_file(root, country_code)
    if legacy.exists():
        return legacy
    return expected


def available_mapping_codes(root: Path) -> list[str]:
    """List mapping codes discovered under configs/mapping/*.csv."""
    d = mapping_dir(root)
    if not d.exists():
        return []

    codes: list[str] = []
    for p in sorted(d.glob("*.csv")):
        if p.is_file():
            codes.append(p.stem.upper())
    return codes
