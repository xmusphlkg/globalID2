"""Shared taxonomy helpers for the international authority adapters.

Every adapter funnels its records through the helpers here so that a WHO Disease
Outbreak News item and an ECDC weekly threat report land on the same keys the
rest of GIDS already understands: ``disease_id`` (``D050``), ISO 3166-1 alpha-2
country codes, and a UTC publication date.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .country_zh import COUNTRY_ZH_NAMES

ROOT = Path(__file__).resolve().parents[3]
DISEASE_CSV = ROOT / "configs" / "standard_diseases.csv"
SITE_DATA = ROOT / "astro-site" / "src" / "data"
DISEASE_INDEX = SITE_DATA / "diseases" / "index.json"
COUNTRY_INDEX = SITE_DATA / "meta.json"

#: Per-source provenance and redistribution posture.  ``redistribution`` drives
#: whether an adapter keeps an agency-authored abstract next to the metadata.
#: Sources whose redistribution terms are unverified stay metadata-only.
SOURCE_REGISTRY: dict[str, dict[str, Any]] = {
    "who_don": {
        "source_id": "who_don",
        "label": {"en": "WHO Disease Outbreak News", "zh": "世卫组织疾病暴发新闻"},
        "agency": {"en": "World Health Organization", "zh": "世界卫生组织"},
        "homepage": "https://www.who.int/emergencies/disease-outbreak-news",
        "endpoint": "https://www.who.int/api/hubs/diseaseoutbreaknews",
        "cadence": {"en": "irregular (as events are verified)", "zh": "不定期（随事件核实发布）"},
        "redistribution": "metadata_and_extract",
        "license": "WHO terms of use; short attributed extracts with a link to the original",
        "license_zh": "世卫组织使用条款；仅保留简短摘录并附原文链接",
    },
    "ecdc_cdtr": {
        "source_id": "ecdc_cdtr",
        "label": {"en": "ECDC Communicable Disease Threats Report", "zh": "欧洲疾控中心传染病威胁周报"},
        "agency": {"en": "European Centre for Disease Prevention and Control", "zh": "欧洲疾病预防控制中心"},
        "homepage": "https://www.ecdc.europa.eu/en/publications-and-data/monitoring/weekly-threats-reports",
        "endpoint": "https://www.ecdc.europa.eu/en/taxonomy/term/1505/feed",
        "cadence": {"en": "weekly", "zh": "每周"},
        "redistribution": "metadata_and_extract",
        "license": "CC BY 4.0 with attribution to ECDC",
        "license_zh": "CC BY 4.0，需注明来源为 ECDC",
    },
    "who_wer": {
        "source_id": "who_wer",
        "label": {"en": "WHO Weekly Epidemiological Record", "zh": "世卫组织每周流行病学记录"},
        "agency": {"en": "World Health Organization", "zh": "世界卫生组织"},
        "homepage": "https://www.who.int/publications/journals/weekly-epidemiological-record",
        # The landing page lists issue numbers without dates, so the adapter
        # reads the dated article records from the WHO publications hub.
        "endpoint": "https://www.who.int/api/hubs/publications",
        "cadence": {"en": "weekly", "zh": "每周"},
        # Redistribution of WER article content is not yet confirmed, so this
        # adapter stays title/week/link only until the terms are cleared.
        "redistribution": "metadata_only",
        "license": "Redistribution terms pending confirmation; metadata only",
        "license_zh": "再分发条款待核实；仅收录标题、周次与链接",
    },
    "who_wpro": {
        "source_id": "who_wpro",
        "label": {"en": "WHO Western Pacific surveillance updates", "zh": "世卫组织西太平洋区域监测更新"},
        "agency": {"en": "WHO Regional Office for the Western Pacific", "zh": "世卫组织西太平洋区域办事处"},
        "homepage": "https://www.who.int/westernpacific/emergencies/surveillance",
        "endpoint": "https://www.who.int/westernpacific/emergencies/surveillance",
        "cadence": {"en": "weekly to monthly", "zh": "每周至每月"},
        "redistribution": "metadata_only",
        "license": "Redistribution terms pending confirmation; metadata only",
        "license_zh": "再分发条款待核实；仅收录标题与链接",
    },
    "africa_cdc": {
        "source_id": "africa_cdc",
        "label": {"en": "Africa CDC outbreak statements and weekly reports", "zh": "非洲疾控中心疫情声明与周报"},
        "agency": {"en": "Africa Centres for Disease Control and Prevention", "zh": "非洲疾病预防控制中心"},
        "homepage": "https://africacdc.org/document-tag/ebs-weekly-report/",
        # The tag archive prints no dates, so the adapter reads the WordPress
        # REST API (news-item statements + wpdmpro weekly report downloads).
        "endpoint": "https://africacdc.org/wp-json/wp/v2",
        "cadence": {"en": "weekly reports; statements as events evolve", "zh": "每周报告；随事件发布声明"},
        "redistribution": "metadata_only",
        "license": "Redistribution terms pending confirmation; metadata only",
        "license_zh": "再分发条款待核实；仅收录标题与链接",
    },
    "paho": {
        "source_id": "paho",
        "label": {"en": "PAHO epidemiological alerts and updates", "zh": "泛美卫生组织流行病学警报与更新"},
        "agency": {"en": "Pan American Health Organization", "zh": "泛美卫生组织"},
        "homepage": "https://www.paho.org/en/epidemiological-alerts-and-updates",
        # www.paho.org returns 403 to unattended clients; PAHO's IRIS
        # repository serves the same documents through the DSpace API.
        "endpoint": "https://iris.paho.org/server/api/discover/search/objects",
        "cadence": {"en": "weekly to irregular", "zh": "每周至不定期"},
        "redistribution": "metadata_only",
        "license": "Redistribution terms pending confirmation; metadata only",
        "license_zh": "再分发条款待核实；仅收录标题与链接",
    },
}


#: Extra phrases used when reading agency headlines, keyed by the catalogue's
#: own English name so the mapping never depends on hard-coded ``disease_id``
#: values that shift as the standard disease list grows.
EXTRA_TOPIC_ALIASES: dict[str, list[str]] = {
    "cholera": ["cholera outbreak"],
    "covid-19": ["covid", "sars-cov-2"],
    # No bare "sars-cov" alias: it is a prefix of SARS-CoV-2 and would tag
    # every COVID-19 bulletin as SARS.
    "sars": ["severe acute respiratory syndrome"],
    "h5n1": ["avian influenza a h5n1", "influenza a h5n1"],
    "h7n9": ["avian influenza a h7n9", "influenza a h7n9"],
    "novel influenza a": ["avian influenza a h9n2", "influenza a h9n2", "novel influenza a"],
    "influenza": ["seasonal influenza", "influenza-like illness"],
    "mers": [
        "middle east respiratory syndrome coronavirus",
        "middle east respiratory syndrome",
        "mers-cov",
    ],
    "ebola": [
        "ebola virus disease",
        "ebola haemorrhagic fever",
        "ebola disease",
        "bundibugyo virus disease",
        "sudan virus disease",
    ],
    "monkeypox": ["mpox"],
    "lassa": ["lassa fever"],
    "zika virus disease": ["zika virus", "zika"],
    "dengue": ["dengue fever", "severe dengue"],
    "yellow fever": ["yellow fever virus"],
    "measles": ["measles outbreak"],
    "diphtheria": ["diphtheria cases"],
    "poliomyelitis": ["poliovirus", "polio"],
    "tuberculosis": ["multidrug-resistant tuberculosis"],
    "malaria": ["falciparum malaria"],
    "west nile virus": ["west nile fever", "west nile virus infection"],
    "crimean-congo hemorrhagic fever": [
        "crimean-congo haemorrhagic fever",
        "crimean congo haemorrhagic fever",
    ],
    # WHO headlines say "Hantavirus outbreak" without naming the syndrome, so
    # the bare word lands on the umbrella concept, never on HFRS/HPS.
    "hantavirus infection": ["hantavirus", "hantavirus disease"],
    "respiratory syncytial virus infection (rsv)": [
        "respiratory syncytial virus",
        "rsv",
    ],
}

_WHITESPACE = re.compile(r"[^a-z0-9]+")


def _normalize(value: str) -> str:
    return " " + _WHITESPACE.sub(" ", str(value).lower()).strip() + " "


@lru_cache(maxsize=1)
def disease_catalogue() -> list[dict[str, Any]]:
    """Disease rows with English/Chinese names, slug, and match aliases."""

    slugs: dict[str, str] = {}
    if DISEASE_INDEX.exists():
        for row in json.loads(DISEASE_INDEX.read_text(encoding="utf-8")):
            disease_id = str(row.get("disease_id") or "").strip()
            if disease_id:
                slugs[disease_id] = str(row.get("slug") or "")

    rows: list[dict[str, Any]] = []
    if not DISEASE_CSV.exists():
        return rows
    with DISEASE_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            disease_id = str(row.get("disease_id") or "").strip()
            if not disease_id:
                continue
            if "deprecated" in f"{row.get('description') or ''} {row.get('standard_name_zh') or ''}".lower():
                # Deprecated duplicate IDs would only add noise to the timelines.
                continue
            name_en = str(row.get("standard_name_en") or "").strip()
            aliases = list(EXTRA_TOPIC_ALIASES.get(name_en.lower(), []))
            if name_en and name_en.lower() not in aliases:
                aliases.append(name_en.lower())
            rows.append(
                {
                    "disease_id": disease_id,
                    "name_en": name_en,
                    "name_zh": str(row.get("standard_name_zh") or "").strip() or None,
                    "slug": slugs.get(disease_id) or "",
                    "aliases": [alias for alias in aliases if alias],
                }
            )
    rows.sort(key=lambda row: len(row["name_en"]), reverse=True)
    return rows


@lru_cache(maxsize=1)
def country_catalogue() -> dict[str, dict[str, Any]]:
    """ISO alpha-2 -> display names, with Chinese labels from the site metadata."""

    names: dict[str, dict[str, Any]] = {}
    try:
        import pycountry
    except Exception:  # pragma: no cover - pycountry ships with the runtime deps
        pycountry = None
    if pycountry is not None:
        for country in pycountry.countries:
            names[country.alpha_2] = {
                "code": country.alpha_2,
                "name_en": country.name,
                "name_zh": None,
                "aliases": sorted(
                    {
                        country.name.lower(),
                        *(
                            str(getattr(country, attribute)).lower()
                            for attribute in ("official_name", "common_name")
                            if getattr(country, attribute, None)
                        ),
                    },
                    key=len,
                    reverse=True,
                ),
            }

    if COUNTRY_INDEX.exists():
        payload = json.loads(COUNTRY_INDEX.read_text(encoding="utf-8"))
        for row in payload.get("countries") or []:
            code = str(row.get("code") or "").strip().upper()
            if len(code) != 2:
                continue
            entry = names.setdefault(
                code,
                {
                    "code": code,
                    "name_en": str(row.get("name_en") or row.get("name") or code),
                    "name_zh": None,
                    "aliases": [],
                },
            )
            zh = str(row.get("name_zh") or "").strip()
            if zh:
                entry["name_zh"] = zh
            english = str(row.get("name_en") or row.get("name") or "").strip()
            if english and english.lower() not in entry["aliases"]:
                entry["aliases"].append(english.lower())
            entry["aliases"].sort(key=len, reverse=True)

    # The site metadata only covers jurisdictions with published series; the
    # curated table supplies standard Chinese short forms for the rest so a
    # notice naming Kenya still renders a Chinese place name.
    for code, entry in names.items():
        if not entry.get("name_zh"):
            entry["name_zh"] = COUNTRY_ZH_NAMES.get(code)
    return names


COUNTRY_DISPLAY_OVERRIDES = {
    "CD": {"en": "Democratic Republic of the Congo", "zh": "刚果民主共和国"},
    "CG": {"en": "Republic of the Congo", "zh": "刚果共和国"},
    "GB": {"en": "United Kingdom", "zh": "英国"},
    "US": {"en": "United States", "zh": "美国"},
}

COUNTRY_ALIASES: dict[str, list[str]] = {
    "CD": ["democratic republic of the congo", "dr congo", "drc"],
    "CG": ["republic of the congo"],
    "GB": ["united kingdom", "united kingdom of great britain", "uk"],
    "US": ["united states", "united states of america", "usa"],
    "CI": ["cote d'ivoire", "côte d’ivoire", "ivory coast"],
    "VN": ["viet nam", "vietnam"],
    "RU": ["russian federation", "russia"],
    "IR": ["iran islamic republic", "iran"],
    "SY": ["syrian arab republic", "syria"],
    "TW": ["taiwan"],
    "VE": ["venezuela bolivarian republic", "venezuela"],
    "BO": ["bolivia plurinational state", "bolivia"],
    "TZ": ["tanzania united republic", "tanzania"],
    "MD": ["moldova republic", "moldova"],
    "LA": ["lao people s democratic republic", "laos"],
    "KP": ["korea democratic people s republic", "north korea"],
    "KR": ["korea republic", "south korea"],
}


@lru_cache(maxsize=1)
def _iso3_to_alpha2() -> dict[str, str]:
    mapping: dict[str, str] = {}
    try:
        import pycountry
    except Exception:  # pragma: no cover
        return mapping
    for country in pycountry.countries:
        mapping[country.alpha_3] = country.alpha_2
    return mapping


def match_diseases(text: str) -> list[dict[str, Any]]:
    """Return every catalogue disease explicitly named in ``text``.

    Only whole-phrase matches count, so a generic ``influenza`` mention never
    silently claims an avian influenza A(H5N1) event.
    """

    normalized = _normalize(text)
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for row in disease_catalogue():
        for alias in row["aliases"]:
            candidate = _normalize(alias).strip()
            if len(candidate) >= 3:
                candidates.append((len(candidate), candidate, row))
    # Longest phrases claim their span first and blank it out, so a short name
    # never matches inside a longer one: "SARS" inside "SARS-CoV-2", or
    # "influenza" inside "avian influenza A(H5N1)".
    candidates.sort(key=lambda item: item[0], reverse=True)
    matches: list[tuple[int, dict[str, Any]]] = []
    for length, candidate, row in candidates:
        needle = f" {candidate} "
        if needle in normalized:
            matches.append((length, row))
            normalized = normalized.replace(needle, " " * len(needle))
    ordered = [row for _, row in matches]
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in ordered:
        if row["disease_id"] in seen:
            continue
        seen.add(row["disease_id"])
        unique.append(row)
    if any(row["disease_id"] == "D038" for row in unique) and any(
        row["disease_id"] in {"D014", "D015", "D016"} for row in unique
    ):
        unique = [row for row in unique if row["disease_id"] != "D038"]
    return unique


def match_countries(text: str) -> list[dict[str, str]]:
    """Return ISO alpha-2 countries explicitly named in ``text``."""

    normalized = _normalize(text)
    catalogue = country_catalogue()
    spans: list[tuple[int, int, str]] = []
    for code, entry in catalogue.items():
        curated = set(COUNTRY_ALIASES.get(code, []))
        for alias in list(entry["aliases"]) + list(curated):
            needle = _normalize(alias).strip()
            # Short catalogue names are too ambiguous to match on their own, but
            # curated abbreviations ("DRC", "UK", "USA") are deliberate.
            if not needle or (len(needle) < 4 and alias not in curated):
                continue
            start = normalized.find(f" {needle} ")
            if start >= 0:
                spans.append((start, len(needle), code))
                break
    # Resolve the longest phrase first so "Ireland" does not double-count with
    # "United Kingdom of Great Britain and Northern Ireland".
    spans.sort(key=lambda item: (-item[1], item[0]))
    selected: list[tuple[int, int]] = []
    codes: list[str] = []
    for start, length, code in spans:
        end = start + length
        if any(start >= chosen_start and end <= chosen_end for chosen_start, chosen_end in selected):
            continue
        selected.append((start, end))
        codes.append(code)
    rows: list[dict[str, str]] = []
    for code in sorted(set(codes)):
        entry = catalogue[code]
        override = COUNTRY_DISPLAY_OVERRIDES.get(code) or {}
        rows.append(
            {
                "code": code,
                "name_en": override.get("en") or entry["name_en"],
                "name_zh": override.get("zh") or entry.get("name_zh"),
            }
        )
    return rows


def iso_alpha2(code: str | None) -> str | None:
    if not code:
        return None
    text = str(code).strip().upper()
    if len(text) == 2:
        return text
    if len(text) == 3:
        return _iso3_to_alpha2().get(text)
    return None


def country_row(code: str) -> dict[str, str]:
    catalogue = country_catalogue()
    entry = catalogue.get(code)
    override = COUNTRY_DISPLAY_OVERRIDES.get(code) or {}
    if entry is None:
        return {"code": code, "name_en": override.get("en") or code, "name_zh": override.get("zh")}
    return {
        "code": code,
        "name_en": override.get("en") or entry["name_en"],
        "name_zh": override.get("zh") or entry.get("name_zh"),
    }


def disease_row(disease_id: str | None) -> dict[str, Any] | None:
    if not disease_id:
        return None
    for row in disease_catalogue():
        if row["disease_id"] == disease_id:
            return row
    return None


def iso_week(value: date) -> dict[str, Any]:
    year, week, _ = value.isocalendar()
    return {"iso_year": year, "iso_week": week, "iso_week_key": f"{year}-W{week:02d}"}


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%d %B %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def compose_chinese_title(
    diseases: Iterable[dict[str, Any]],
    countries: Iterable[dict[str, str]],
    source_id: str,
    published: date | None,
) -> str | None:
    """Compose a readable Chinese headline from mapped terms.

    The result is a structured label built from the GIDS Chinese disease and
    country vocabulary rather than a literal translation, so the original
    English headline stays authoritative and is always kept alongside it.
    """

    registry = SOURCE_REGISTRY.get(source_id, {})
    agency = (registry.get("label") or {}).get("zh")
    disease_names = [row["name_zh"] for row in diseases if row.get("name_zh")]
    place_names = [row["name_zh"] or row["name_en"] for row in countries]
    if not disease_names and not place_names:
        return None
    head = "、".join(disease_names) if disease_names else None
    tail = "、".join(place_names) if place_names else None
    label = " · ".join(part for part in (head, tail) if part)
    if published and agency:
        label = f"{label} · {published.isoformat()} {agency}"
    return label
