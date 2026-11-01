"""Resolve an alert's free-text ``admin_area`` to map coordinates.

The gazetteer (``configs/situation_gazetteer.json``) is built by
``scripts/build_situation_gazetteer.py`` from Natural Earth (public domain)
admin-1 areas and larger places, plus a short hand-kept supplement for areas
created after the Natural Earth snapshot. A report that names several areas
is not one point, so it resolves to nothing and the caller keeps the country.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from src.services.global_sources.catalogue import ROOT

GAZETTEER_PATH = ROOT / "configs" / "situation_gazetteer.json"

# Words that only say what kind of area it is; stripped on both sides of a match.
_GENERIC = frozenset(
    "province provinces provincia region regions state states district districts oblast county city prefecture "
    "governorate department departement territory division municipality commune urban health zone "
    "area areas autonomous sar regional town the of".split()
)
_SEPARATORS = re.compile(r"\s*(?:[,;/:()]|\s&\s|\s-\s|\band\b|\bet\b)\s*", re.IGNORECASE)
_MIN_PREFIX = 4


@dataclass(frozen=True)
class Location:
    lat: float
    lng: float
    #: ``admin1`` for a first-level area, ``place`` for a named city or town.
    precision: str
    names: tuple[str, ...] = ()
    parent: str | None = None


def normalize(text: str | None) -> str:
    """Lower-case, accent-free, generic words removed: "Équateur Province" -> "equateur"."""

    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    words = re.sub(r"[\W_]+", " ", folded).split()
    return " ".join(word for word in words if word not in _GENERIC)


@lru_cache(maxsize=1)
def _index() -> dict[tuple[str, str], list[Location]]:
    try:
        payload = json.loads(GAZETTEER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    index: dict[tuple[str, str], list[Location]] = {}
    for iso, lat, lng, kind, names, *rest in payload.get("entries", []):
        location = Location(
            lat=float(lat),
            lng=float(lng),
            precision="admin1" if kind == "a" else "place",
            names=tuple(names),
            parent=rest[0] if rest and rest[0] else None,
        )
        for name in names:
            index.setdefault((iso, name), []).append(location)
    # A first-level area beats a city of the same name.
    for rows in index.values():
        rows.sort(key=lambda row: row.precision != "admin1")
    return index


def _match(iso: str, name: str, *, shorten: bool) -> Location | None:
    """Exact match; for a single phrase, also try dropping up to two trailing words."""

    index = _index()
    words = name.split()
    longest = len(words)
    shortest = max(1, longest - 2) if shorten else longest
    for size in range(longest, shortest - 1, -1):
        candidate = " ".join(words[:size])
        if size < longest and len(candidate) < _MIN_PREFIX:
            break
        rows = index.get((iso, candidate))
        if rows:
            return rows[0]
    return None


def _inside(inner: Location, outer: Location) -> bool:
    return inner.precision == "place" and inner.parent is not None and inner.parent in outer.names


def locate(country_code: str | None, admin_area: str | None) -> Location | None:
    """Coordinates of the one place ``admin_area`` names in ``country_code``, else ``None``."""

    if not country_code or not admin_area:
        return None
    iso = country_code.upper()
    parts = [normalize(part) for part in _SEPARATORS.split(admin_area)]
    parts = [part for part in parts if part]
    if not parts:
        return None
    found = {hit for part in parts if (hit := _match(iso, part, shorten=len(parts) == 1))}
    # "Kerala State, Kozhikode district": the city sits inside the state, keep the city.
    found = {outer for outer in found if not any(_inside(inner, outer) for inner in found)}
    return next(iter(found)) if len(found) == 1 else None
