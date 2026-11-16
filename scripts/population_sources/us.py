"""Discover Census official state resident population estimates automatically.

The current Census estimates need not be exposed by the API. This adapter
reads the official download-directory catalogue, chooses each decade's latest
published state-total vintage, and reimports its revised July 1 estimates.
Only population columns are used: census-day counts and April estimate bases
are never relabelled as annual July populations. No local interpolation occurs.
"""
from __future__ import annotations

import csv
import io
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from scripts.import_subdivision_population import SubdivisionPopulation

CENSUS_DIRECTORY = "https://www2.census.gov/programs-surveys/popest/datasets/"
# Reviewed official FIPS-to-postal crosswalk. Values/names are not population
# inputs. The four island areas without annual estimates remain absent.
FIPS = dict(zip(
    "01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 72".split(),
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split(),
    strict=True,
))
CORE_STATES = set(FIPS) - {"72"}
BASIS = "july_estimated_resident_population"


def _official_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname == "census.gov" or (parsed.hostname or "").endswith(".census.gov"))


def _links(response):
    for anchor in BeautifulSoup(response.text, "html.parser").select("a[href]"):
        url = urljoin(response.url, anchor["href"])
        if _official_url(url):
            yield url


def parse_state_totals(content: bytes | str, source_url: str, *, first_year: int, last_year: int) -> list[SubdivisionPopulation]:
    """Validate a complete official state-total vintage, including geography."""
    if not _official_url(source_url):
        raise ValueError("Census population URL must be an official HTTPS URL")
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    if not {"SUMLEV", "STATE", "NAME"}.issubset(fields) or len(fields) != len(set(fields)):
        raise ValueError("Census state population schema changed")
    years = {int(match[1]): name for name in fields if (match := re.fullmatch(r"POPESTIMATE(\d{4})", name))}
    if set(years) != set(range(first_year, last_year + 1)):
        raise ValueError("Census estimate-year coverage changed")
    rows, seen, names = [], set(), set()
    geographies = {}
    for record in reader:
        level = str(record.get("SUMLEV") or "").zfill(3)
        if level in {"010", "020", "030"}:
            continue  # US/division/region totals never become state populations
        if level != "040":
            raise ValueError(f"Unexpected Census geography level: {level}")
        ident = str(record.get("STATE") or "").zfill(2)
        name = str(record.get("NAME") or "").strip()
        if ident not in FIPS or ident in seen or not name or name in names:
            raise ValueError("Census state identifiers/names changed or duplicated")
        seen.add(ident)
        names.add(name)
        for year, column in years.items():
            raw = str(record.get(column) or "").strip()
            if not re.fullmatch(r"[0-9]+", raw) or int(raw) <= 0:
                raise ValueError(f"Invalid Census resident population: {ident}/{year}")
            geographies.setdefault(year, set()).add(ident)
            rows.append(SubdivisionPopulation(
                "US-" + FIPS[ident], year, int(raw), "Census", f"{year}-07-01", source_url, BASIS,
            ))
    if not geographies or any(not CORE_STATES.issubset(areas) or areas - set(FIPS) for areas in geographies.values()):
        raise ValueError("Census returned partial state population coverage")
    return sorted(rows, key=lambda row: (row.country_code, row.year))


def parse_intercensal_state_totals(content: bytes | str, source_url: str) -> list[SubdivisionPopulation]:
    """Read published 2000–2010 intercensal totals, excluding every stratum.

    These are Census's published revised estimates, not an interpolation by us.
    Only SEX=ORIGIN=RACE=AGEGRP=0 is the all-person resident denominator.
    """
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    required = {"STATE", "NAME", "SEX", "ORIGIN", "RACE", "AGEGRP"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("Census intercensal demographic schema changed")
    fields = ["SUMLEV", "STATE", "NAME", *[f"POPESTIMATE{year}" for year in range(2000, 2011)]]
    if not set(fields[3:]).issubset(reader.fieldnames or []):
        raise ValueError("Census intercensal estimate-year coverage changed")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for record in reader:
        if not all(record[key] == "0" for key in ["SEX", "ORIGIN", "RACE", "AGEGRP"]):
            continue
        if record["STATE"].zfill(2) == "00":
            continue
        writer.writerow({"SUMLEV": "040", **{key: record[key] for key in fields[1:]}})
    return parse_state_totals(buffer.getvalue(), source_url, first_year=2000, last_year=2010)


def _fetch_directory(source, name: str, url: str):
    try:
        return source.fetch(name, url)
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return None
        raise


def fetch_us(source, start_year: int = 2000) -> list[SubdivisionPopulation]:
    """Fetch every supported published year, with latest-vintage revisions.

    A future vintage directory may appear before state totals are released. In
    that case only a 404/absence of a population file permits trying an earlier
    vintage; network, TLS, invalid CSV or partial coverage fail this source.
    """
    catalogue = source.fetch("census_vintages.html", CENSUS_DIRECTORY)
    vintages = {}
    for url in _links(catalogue):
        match = re.fullmatch(re.escape(CENSUS_DIRECTORY) + r"(\d{4})-(\d{4})/", url)
        if match:
            first, last = int(match[1]), int(match[2])
            if first >= 2010 and first % 10 == 0 and first <= last <= first + 10 and last >= start_year:
                vintages.setdefault(first, set()).add(last)
    if not vintages:
        raise ValueError("Census official vintage catalogue changed")
    chosen = []
    if start_year < 2010:
        # The final intercensal release has a demographic-table layout, unlike
        # later state totals. Discover its file in the official state directory.
        directory = CENSUS_DIRECTORY + "2000-2010/intercensal/state/"
        listing = source.fetch("census_2000_2010_intercensal.html", directory)
        urls = {url for url in _links(listing) if url.rsplit("/", 1)[-1].lower() == "st-est00int-alldata.csv"}
        if len(urls) != 1:
            raise ValueError("Census intercensal state population download changed")
        response = source.fetch("census_2000_2010_intercensal.csv", urls.pop())
        chosen.extend(parse_intercensal_state_totals(response.content, response.url))
    for first, candidates in sorted(vintages.items()):
        for last in sorted(candidates, reverse=True):
            directory = f"{CENSUS_DIRECTORY}{first}-{last}/state/totals/"
            listing = _fetch_directory(source, f"census_{first}_{last}_state_totals.html", directory)
            if listing is None:
                continue
            urls = {url for url in _links(listing) if re.fullmatch(r"nst-est" + str(last) + r"-alldata\.csv", url.rsplit("/", 1)[-1], re.I)}
            if not urls:
                continue
            if len(urls) != 1:
                raise ValueError("Census state population download link is ambiguous")
            url = urls.pop()
            response = source.fetch(f"census_{first}_{last}_state_totals.csv", url)
            chosen.extend(parse_state_totals(response.content, response.url, first_year=first, last_year=last))
            break
        else:
            raise ValueError(f"No published Census state population file for decade {first}")
    # Newer decade vintages supersede overlapping July estimates (e.g. 2020).
    merged = {(row.country_code, row.year): row for row in chosen if row.year >= start_year}
    if not merged:
        raise ValueError("Census returned no population for the requested years")
    return sorted(merged.values(), key=lambda row: (row.country_code, row.year))
