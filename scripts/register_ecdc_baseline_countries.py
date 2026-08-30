#!/usr/bin/env python3
"""Register every prepared reviewed ECDC annual country baseline.

The command discovers countries from mapping files containing a reviewed ECDC
source contract.  It clones the France ontology contract while replacing only
country identity, remains read-only unless ``--apply`` is supplied, and can be
re-run safely as additional country mappings are prepared.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRIES  # noqa: E402


ONTOLOGY_PATH = ROOT / "configs/disease_ontology.json"
MAPPING_DIR = ROOT / "configs/mapping"
RELEASE_VERSION = "2026.08.29.1"


def prepared_country_codes() -> tuple[str, ...]:
    """Return countries whose reviewed mapping contains its ECDC source id."""

    prepared: list[str] = []
    for code in ECDC_BASELINE_COUNTRIES:
        paths = (
            MAPPING_DIR / f"{code.casefold()}.csv",
            MAPPING_DIR / "reviewed" / f"{code.casefold()}.csv",
        )
        if any(
            path.exists()
            and f"SRC_{code}_ECDC_ATLAS" in path.read_text(encoding="utf-8-sig")
            for path in paths
        ):
            prepared.append(code)
    return tuple(prepared)


def _source_line(code: str) -> str:
    meta = ECDC_BASELINE_COUNTRIES[code]
    locale = meta["language"].split("-", 1)[0]
    labels = {
        "en": f"ECDC Surveillance Atlas — {meta['name']} annual baseline",
    }
    if locale != "en":
        labels[locale] = f"ECDC Surveillance Atlas — {meta['name_local']} annual baseline"
    payload = {
        "id": f"SRC_{code}_ECDC_ATLAS",
        "country_code": code,
        "labels": labels,
        "legacy_data_sources": ["ECDC Surveillance Atlas of Infectious Diseases"],
    }
    return "    " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _clone_lines(lines: list[str], *, prefix: str, code: str, name: str) -> list[str]:
    template = [line for line in lines if f'"id":"{prefix}_FR_ECDC_' in line]
    if len(template) != 55:
        raise ValueError(f"Expected 55 reviewed {prefix} France contracts, found {len(template)}")
    return [
        line.replace("_FR_ECDC_", f"_{code}_ECDC_")
        .replace("SRC_FR_ECDC_ATLAS", f"SRC_{code}_ECDC_ATLAS")
        .replace("country:FR:national", f"country:{code}:national")
        .replace("France annual", f"{name} annual")
        for line in template
    ]


def _insert_after_last(lines: list[str], indexes: list[int], additions: list[str]) -> None:
    if not indexes:
        raise ValueError("No insertion anchor found")
    insert_at = indexes[-1]
    lines[insert_at] = lines[insert_at].rstrip(",") + ","
    normalized = [line.rstrip(",") + "," for line in additions]
    normalized[-1] = normalized[-1].rstrip(",")
    lines[insert_at + 1:insert_at + 1] = normalized


def build_updated_text(text: str, countries: tuple[str, ...]) -> tuple[str, list[str]]:
    lines = text.splitlines()
    missing = tuple(
        code for code in countries if f'"id":"SRC_{code}_ECDC_ATLAS"' not in text
    )
    if not missing:
        return text, ["all prepared ECDC countries already registered"]

    source_indexes = [
        index for index, line in enumerate(lines)
        if '"id":"SRC_' in line and '_ECDC_ATLAS"' in line
    ]
    _insert_after_last(lines, source_indexes, [_source_line(code) for code in missing])

    for prefix in ("SER", "AV"):
        clones: list[str] = []
        for code in missing:
            clones.extend(
                _clone_lines(
                    lines,
                    prefix=prefix,
                    code=code,
                    name=ECDC_BASELINE_COUNTRIES[code]["name"],
                )
            )
        anchors = [
            index for index, line in enumerate(lines)
            if f'"id":"{prefix}_' in line and "_ECDC_" in line
        ]
        _insert_after_last(lines, anchors, clones)

    updated = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    parsed = json.loads(updated)
    parsed_version = str(parsed.get("release_version") or "")
    updated = updated.replace(
        f'"release_version": "{parsed_version}"',
        f'"release_version": "{RELEASE_VERSION}"',
        1,
    )
    if json.loads(updated).get("release_version") != RELEASE_VERSION:
        raise ValueError("Ontology release version was not updated")
    return updated, [
        f"register {len(missing)} sources, {55 * len(missing)} series, and "
        f"{55 * len(missing)} availability declarations: {', '.join(missing)}"
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    countries = prepared_country_codes()
    if not countries:
        raise SystemExit("No reviewed ECDC mapping files were found")
    original = ONTOLOGY_PATH.read_text(encoding="utf-8")
    updated, actions = build_updated_text(original, countries)
    for action in actions:
        print(("Apply" if args.apply else "Plan") + f": {action}")
    if args.apply and updated != original:
        ONTOLOGY_PATH.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
