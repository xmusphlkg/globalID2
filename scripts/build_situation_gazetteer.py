#!/usr/bin/env python3
"""Build ``configs/situation_gazetteer.json`` from Natural Earth (public domain).

Inputs (GeoJSON, https://github.com/nvkelso/natural-earth-vector/tree/master/geojson):

* ``ne_10m_admin_1_states_provinces.geojson``
* ``ne_10m_populated_places_simple.geojson``

Usage::

    python scripts/build_situation_gazetteer.py ADMIN1.geojson PLACES.geojson

Entry shape: ``[iso2, lat, lng, "a"|"p", [normalized names], parent_name?]``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.situation_notices.gazetteer import GAZETTEER_PATH, normalize  # noqa: E402

#: Areas created after the Natural Earth snapshot (DR Congo's 2015 split into
#: 26 provinces). Coordinates are the provincial capital, which is where an
#: outbreak report is most likely to be anchored.
SUPPLEMENT: list[tuple[str, float, float, list[str]]] = [
    ("CD", 1.566, 30.252, ["Ituri"]),
    ("CD", -1.659, 29.221, ["Nord-Kivu", "North Kivu"]),
    ("CD", -2.508, 28.861, ["Sud-Kivu", "South Kivu"]),
    ("CD", 0.515, 25.191, ["Tshopo"]),
    ("CD", 2.774, 27.616, ["Haut-Uele", "Haut-Uélé"]),
    ("CD", 2.809, 24.730, ["Bas-Uele", "Bas-Uélé"]),
    ("CD", 3.257, 19.774, ["Sud-Ubangi", "South Ubangi"]),
    ("CD", 4.283, 21.000, ["Nord-Ubangi", "North Ubangi"]),
    ("CD", 2.150, 21.517, ["Mongala"]),
    ("CD", 0.049, 18.260, ["Equateur", "Équateur"]),
    ("CD", -0.217, 20.867, ["Tshuapa"]),
    ("CD", -1.950, 18.267, ["Mai-Ndombe"]),
    ("CD", -3.317, 17.383, ["Kwilu"]),
    ("CD", -4.833, 16.950, ["Kwango"]),
    ("CD", -4.322, 15.322, ["Kinshasa"]),
    ("CD", -5.817, 13.467, ["Kongo Central", "Kongo-Central"]),
    ("CD", -6.417, 20.800, ["Kasai", "Kasaï"]),
    ("CD", -5.896, 22.417, ["Kasai-Central", "Kasaï-Central"]),
    ("CD", -6.150, 23.600, ["Kasai-Oriental", "Kasaï-Oriental"]),
    ("CD", -3.483, 23.433, ["Sankuru"]),
    ("CD", -6.133, 24.483, ["Lomami"]),
    ("CD", -2.950, 25.917, ["Maniema"]),
    ("CD", -5.933, 29.200, ["Tanganyika"]),
    ("CD", -8.733, 25.000, ["Haut-Lomami"]),
    ("CD", -10.717, 25.467, ["Lualaba"]),
    ("CD", -11.667, 27.483, ["Haut-Katanga"]),
]

#: Spelling variants and places Natural Earth files under another country code
#: (Hong Kong and Macau are CN in WHO reports but HK/MO in Natural Earth).
ALIASES: list[tuple[str, float, float, str, list[str]]] = [
    ("MR", 17.934, -14.606, "a", ["Traza"]),
    ("MA", 30.673, -7.715, "a", ["Souss-Massa"]),
    ("CN", 22.307, 114.183, "p", ["Hong Kong"]),
    ("CN", 22.198, 113.544, "p", ["Macau", "Macao"]),
    ("RW", -2.484, 28.907, "p", ["Rusizi"]),
    ("RW", -1.679, 29.259, "p", ["Rubavu"]),
    ("AL", 42.398, 20.158, "p", ["Tropoje"]),
]

#: Natural Earth marks some territories and disputed areas with these.
_NO_ISO = {"", "-1", "-99"}
_ALT_FIELDS = ("name", "name_en", "name_fr", "gn_name", "woe_name", "name_local")


def _names(properties: dict) -> list[str]:
    raw: list[str] = [str(properties.get(field) or "") for field in _ALT_FIELDS]
    raw += str(properties.get("name_alt") or "").split("|")
    seen: dict[str, None] = {}
    for item in raw:
        key = normalize(item)
        if key:
            seen[key] = None
    return list(seen)


def _rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("features", [])


def build(admin1: Path, places: Path) -> dict:
    entries: list[list] = []
    for feature in _rows(admin1):
        props = feature.get("properties") or {}
        iso = str(props.get("iso_a2") or "")
        if iso in _NO_ISO or props.get("latitude") is None:
            continue
        names = _names(props)
        if names:
            entries.append([iso, round(props["latitude"], 3), round(props["longitude"], 3), "a", names])
    for iso, lat, lng, names in SUPPLEMENT:
        keys = list(dict.fromkeys(key for key in map(normalize, names) if key))
        entries.append([iso, lat, lng, "a", keys])
    for iso, lat, lng, kind, names in ALIASES:
        keys = list(dict.fromkeys(key for key in map(normalize, names) if key))
        entries.append([iso, lat, lng, kind, keys] + ([""] if kind == "p" else []))
    for feature in _rows(places):
        props = feature.get("properties") or {}
        iso = str(props.get("iso_a2") or "")
        if iso in _NO_ISO or props.get("latitude") is None:
            continue
        names = list(dict.fromkeys(key for key in (normalize(props.get("name")), normalize(props.get("nameascii"))) if key))
        if names:
            entries.append(
                [iso, round(props["latitude"], 3), round(props["longitude"], 3), "p", names, normalize(props.get("adm1name"))]
            )
    return {
        "source": "Natural Earth 10m admin-1 and populated places (public domain) plus a hand-kept DR Congo supplement",
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("admin1", type=Path)
    parser.add_argument("places", type=Path)
    parser.add_argument("--output", type=Path, default=GAZETTEER_PATH)
    args = parser.parse_args()
    payload = build(args.admin1, args.places)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(payload['entries'])} entries -> {args.output} ({args.output.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
