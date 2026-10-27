#!/usr/bin/env python3
"""Refresh official NBS, ABS and IBGE populations; default is fetch/validate only.

Use --apply for an idempotent database update and --export to refresh site data.
Published observations only; absent years are never interpolated or carried forward.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import re
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.import_subdivision_population import (
    SubdivisionPopulation,
    apply_rows,
    load_abs_population,
    validate_rows,
)

NBS_BASE = "https://data.stats.gov.cn/dg/website/publicrelease/web/external/"
NBS_PAGE = "https://data.stats.gov.cn/dg/website/page.html#/pc/national/fsYearData"
IBGE_BASE = "https://apisidra.ibge.gov.br/values/"
ABS_PAGE = "https://www.abs.gov.au/statistics/people/population/national-state-and-territory-population"
CN_CODES = dict(
    zip(
        [
            "11",
            "12",
            "13",
            "14",
            "15",
            "21",
            "22",
            "23",
            "31",
            "32",
            "33",
            "34",
            "35",
            "36",
            "37",
            "41",
            "42",
            "43",
            "44",
            "45",
            "46",
            "50",
            "51",
            "52",
            "53",
            "54",
            "61",
            "62",
            "63",
            "64",
            "65",
        ],
        [
            "BJ",
            "TJ",
            "HE",
            "SX",
            "NM",
            "LN",
            "JL",
            "HL",
            "SH",
            "JS",
            "ZJ",
            "AH",
            "FJ",
            "JX",
            "SD",
            "HA",
            "HB",
            "HN",
            "GD",
            "GX",
            "HI",
            "CQ",
            "SC",
            "GZ",
            "YN",
            "XZ",
            "SN",
            "GS",
            "QH",
            "NX",
            "XJ",
        ],
        strict=True,
    )
)


class PublicSource:
    def __init__(self, output: Path):
        self.output = output
        self.output.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "GlobalID/2.0 official-population-refresh"
        self.sources = {}

    def fetch(self, name: str, url: str, *, body=None, params=None):
        response = self.session.request(
            "POST" if body is not None else "GET",
            url,
            json=body,
            params=params,
            timeout=(15, 90),
        )
        response.raise_for_status()
        if not response.content:
            raise ValueError(f"Empty source: {name}")
        self.sources[name] = {
            "url": response.url,
            "sha256": hashlib.sha256(response.content).hexdigest(),
            "request_body": body,
        }
        (self.output / name).write_bytes(response.content)
        return response

    def json(self, name: str, endpoint: str, **kwargs):
        data = self.fetch(name, NBS_BASE + endpoint, **kwargs).json()
        if data.get("success") is not True:
            raise ValueError(f"NBS source failed: {name}")
        return data["data"]


def positive_integer(value, multiplier=1) -> int:
    number = Decimal(str(value)) * multiplier
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError(f"Invalid population: {value!r}")
    return int(number)


def parse_nbs(data, years: set[int], expected_codes: set[str], national=False):
    rows = []
    observed = {}
    for period in data:
        if not re.fullmatch(r"\d{4}YY", period["code"]):
            raise ValueError("Unexpected NBS population period")
        year = int(period["code"][:4])
        if year not in years:
            raise ValueError("NBS returned an unrequested year")
        for item in period["values"]:
            if (
                item.get("du_name") != "万人"
                or item.get("kj1")
                or item.get("kj2")
                or item.get("kj3")
            ):
                raise ValueError("NBS population unit/dimension changed")
            area = item.get("areaCode", item.get("da"))
            code = "CN" if national else "CN-" + CN_CODES[area[:2]]
            if national and area != "000000000000":
                raise ValueError("National NBS population must use national geography")
            if code not in expected_codes:
                raise ValueError(f"Unexpected NBS geography: {code}")
            value = item.get("value")
            if value is None or str(value).strip() in {"", "-", "--", "…"}:
                continue
            rows.append(
                SubdivisionPopulation(
                    code,
                    year,
                    positive_integer(value, 10000),
                    "NBS",
                    f"{year}-12-31",
                    NBS_PAGE.replace("fsYearData", "yearData")
                    if national
                    else NBS_PAGE,
                    "year_end_resident_population",
                )
            )
            observed.setdefault(year, set()).add(code)
    if not rows:
        raise ValueError("NBS returned no population observations")
    for year, codes in observed.items():
        if codes != expected_codes:
            raise ValueError(
                f"Partial NBS population year {year}: {sorted(expected_codes - codes)}"
            )
    return validate_rows(rows)


def fetch_nbs(source: PublicSource, start: int, end: int):
    provinces = source.json("nbs_provinces.json", "getAllProvince")
    provinces = [p for p in provinces if p["value"][:2] not in {"71", "81", "82"}]
    if {p["value"][:2] for p in provinces} != set(CN_CODES):
        raise ValueError("NBS province registry changed")
    rows = []
    for national, tree_code in [(False, "6"), (True, "3")]:
        prefix = "nbs_national" if national else "nbs_provinces"
        pid = None
        root_id = None
        for level, title in enumerate([None, "人口", "总人口"]):
            entries = source.json(
                f"{prefix}_tree_{level}.json",
                "new/queryIndexTreeAsync",
                params={"code": tree_code, **({"pid": pid} if pid else {})},
            )
            matches = (
                entries if title is None else [e for e in entries if e["name"] == title]
            )
            if len(matches) != 1:
                raise ValueError(f"NBS catalogue changed: {title}")
            pid = matches[0]["_id"]
            if level == 1:
                root_id = pid
        indicators = source.json(
            f"{prefix}_indicators.json", "new/queryIndicatorsByCid", params={"cid": pid}
        )
        wanted = "年末总人口" if national else "年末常住人口"
        total = [
            r
            for r in indicators["list"]
            if r["i_showname"].strip().startswith(wanted + " (")
        ]
        if len(total) != 1 or total[0]["du_name"] != "万人":
            raise ValueError("NBS total population indicator changed")
        for first in range(start, end + 1, 10):
            years = set(range(first, min(first + 9, end) + 1))
            body = {
                "cid": pid,
                "rootId": root_id,
                "indicatorIds": [total[0]["_id"]],
                "daCatalogId": "",
                "das": [{"text": "全国", "value": "000000000000"}]
                if national
                else provinces,
                "dts": [f"{year}YY" for year in sorted(years)],
                "showType": 1 if national else 3,
            }
            data = source.json(
                f"{prefix}_{first}_{max(years)}.json", "stream/esData", body=body
            )
            rows.extend(
                parse_nbs(
                    data,
                    years,
                    {"CN"} if national else {"CN-" + v for v in CN_CODES.values()},
                    national,
                )
            )
    return validate_rows(rows)


def parse_ibge(data, states, *, census=False):
    rows = []
    by_year = {}
    for item in data[1:]:
        if item.get("MC") != "45" or item.get("D2C") != ("93" if census else "9324"):
            raise ValueError("IBGE population unit/variable changed")
        if item["NC"] == "1" and item["D1C"] == "1":
            code = "BR"
        elif item["NC"] == "3" and item["D1C"] in states:
            code = "BR-" + states[item["D1C"]]["sigla"]
        else:
            raise ValueError("IBGE population geography changed")
        if any(
            key.endswith("C")
            and key.startswith("D")
            and int(key[1:-1]) > 3
            and value != "0"
            for key, value in item.items()
        ):
            raise ValueError("IBGE population is not the demographic total")
        year = int(item["D3C"])
        if str(item["V"]).strip() in {"", "-", "...", "..", "X"}:
            continue
        rows.append(
            SubdivisionPopulation(
                code,
                year,
                positive_integer(item["V"]),
                "IBGE",
                f"{year}-08-01" if census else f"{year}-07-01",
                "https://sidra.ibge.gov.br/tabela/"
                + ("4714" if year == 2022 else "202")
                if census
                else "https://sidra.ibge.gov.br/tabela/6579",
                "census_resident_population"
                if census
                else "july_estimated_resident_population",
            )
        )
        by_year.setdefault(year, set()).add(code)
    expected = {"BR"} | {"BR-" + s["sigla"] for s in states.values()}
    if not rows or any(codes != expected for codes in by_year.values()):
        raise ValueError("IBGE returned partial national/state population coverage")
    return validate_rows(rows)


def fetch_ibge(source: PublicSource):
    states = source.fetch(
        "ibge_states.json",
        "https://servicodados.ibge.gov.br/api/v1/localidades/estados",
    ).json()
    if len(states) != 27 or len({s["sigla"] for s in states}) != 27:
        raise ValueError("IBGE state registry changed")
    states = {str(s["id"]): s for s in states}
    rows = []
    for name, query, census in [
        ("estimates", "t/6579/n1/all/n3/all/v/9324/p/all", False),
        ("census_2022", "t/4714/n1/all/n3/all/v/93/p/2022", True),
        ("census_2000_2010", "t/202/n1/all/n3/all/v/93/p/2000,2010/c2/0/c1/0", True),
    ]:
        rows.extend(
            parse_ibge(
                source.fetch(f"ibge_{name}.json", IBGE_BASE + query).json(),
                states,
                census=census,
            )
        )
    return validate_rows(rows), states


def fetch_abs(source: PublicSource):
    page = source.fetch("abs_latest.html", ABS_PAGE)
    links = BeautifulSoup(page.text, "html.parser").find_all("a", href=True)
    quarters = {"mar": 1, "jun": 2, "sep": 3, "dec": 4}
    releases = []
    for link in links:
        match = re.search(
            r"/national-state-and-territory-population/(mar|jun|sep|dec)-(\d{4})$",
            link["href"],
        )
        if match:
            releases.append(
                (int(match[2]), quarters[match[1]], urljoin(page.url, link["href"]))
            )
    if not releases:
        raise ValueError("ABS published release listing changed")
    page = source.fetch("abs_release.html", max(releases)[2])
    links = BeautifulSoup(page.text, "html.parser").find_all("a", href=True)
    workbooks = {
        urljoin(page.url, a["href"])
        for a in links
        if a["href"].lower().endswith("/310104.xlsx")
    }
    if len(workbooks) != 1:
        raise ValueError("ABS latest population workbook link changed")
    url = workbooks.pop()
    source.fetch("abs_310104.xlsx", url)
    return load_abs_population(source.output / "abs_310104.xlsx", url)


async def register_brazil_states(states):
    from sqlalchemy import select

    from src.core.database import dispose_database, get_db
    from src.domain.country import Country

    try:
        async with get_db() as db:
            existing = set((await db.execute(select(Country.code))).scalars())
            for ident, state in states.items():
                code = "BR-" + state["sigla"]
                if code in existing:
                    continue
                db.add(
                    Country(
                        code=code,
                        name=state["nome"] + "（巴西）",
                        name_en=state["nome"] + ", Brazil",
                        name_local=state["nome"],
                        language="pt-BR",
                        timezone={
                            "AC": "America/Rio_Branco",
                            "AM": "America/Manaus",
                            "RO": "America/Porto_Velho",
                            "RR": "America/Boa_Vista",
                            "MT": "America/Cuiaba",
                            "MS": "America/Campo_Grande",
                            "PA": "America/Belem",
                            "AP": "America/Belem",
                        }.get(state["sigla"], "America/Sao_Paulo"),
                        is_active=False,
                        crawler_config={},
                        parser_config={},
                        disease_mapping_rules={},
                        report_config={},
                        metadata_={
                            "location_type": "subdivision",
                            "parent_country_code": "BR",
                            "iso_subdivision_code": code,
                            "ibge_geography_code": ident,
                            "population_only": True,
                        },
                        notes="Official IBGE population available; disease ingestion not enabled.",
                    )
                )
    finally:
        await dispose_database()


async def active_population_locations(codes):
    from sqlalchemy import select

    from src.core.database import dispose_database, get_db
    from src.domain.country import Country

    try:
        async with get_db() as db:
            return set(
                (
                    await db.execute(
                        select(Country.code).where(
                            Country.is_active.is_(True), Country.code.in_(codes)
                        )
                    )
                ).scalars()
            )
    finally:
        await dispose_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--export", action="store_true")
    parser.add_argument(
        "--sources", nargs="+", choices=["CN", "AU", "BR"], default=["CN", "AU", "BR"]
    )
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/processed/official_population"
    )
    args = parser.parse_args()
    if args.export and not args.apply:
        parser.error("--export requires --apply")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if not 2000 <= args.start_year < today.year:
        parser.error("Choose a historical start year from 2000")
    # Keep prior successful snapshots intact even if today's source/schema fails.
    run = args.output / datetime.now(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y%m%dT%H%M%S"
    )
    source = PublicSource(run / "raw")
    rows, states = [], None
    if "CN" in args.sources:
        rows.extend(fetch_nbs(source, args.start_year, today.year - 1))
        print("NBS national and 31 provinces fetched", flush=True)
    if "AU" in args.sources:
        rows.extend(fetch_abs(source))
        print("ABS latest official workbook fetched", flush=True)
    if "BR" in args.sources:
        br_rows, states = fetch_ibge(source)
        rows.extend(br_rows)
        print("IBGE national and 27 states fetched", flush=True)
    rows = validate_rows(rows)
    csv_buffer = io.StringIO()
    fields = [
        "country_code",
        "year",
        "population",
        "source",
        "reference_date",
        "source_url",
        "basis",
    ]
    writer = csv.DictWriter(csv_buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: getattr(row, key) for key in fields})
    content = csv_buffer.getvalue()
    (run / "population.csv").write_text(content, encoding="utf-8")
    checksum = hashlib.sha256(content.encode()).hexdigest()
    if args.apply:
        if states:
            asyncio.run(register_brazil_states(states))
        asyncio.run(apply_rows(rows, {row.source: checksum for row in rows}))
    coverage = {}
    for row in rows:
        coverage.setdefault(row.country_code, []).append(row.year)
    report = {
        "applied": args.apply,
        "checked_at": today.isoformat(),
        "rows": len(rows),
        "locations": len(coverage),
        "coverage": {
            code: {
                "first_year": min(years),
                "last_year": max(years),
                "missing_years": sorted(
                    set(range(min(years), max(years) + 1)) - set(years)
                ),
            }
            for code, years in coverage.items()
        },
        "sources": source.sources,
        "input_sha256": checksum,
        "snapshot": str(run),
        "policy": "exact geography, same calendar year, no interpolation or carry-forward",
    }
    (run / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = "latest.json" if args.apply else "latest_plan.json"
    temporary = args.output / (manifest + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(args.output / manifest)
    print(
        json.dumps(
            {
                "applied": args.apply,
                "rows": len(rows),
                "locations": len(coverage),
                "snapshot": str(run),
            }
        ),
        flush=True,
    )
    if args.export:
        args_export = [sys.executable, str(ROOT / "scripts/generate_site_data.py")]
        # Include state rates once actual disease ingestion has activated them.
        for code in sorted(asyncio.run(active_population_locations(list(coverage)))):
            args_export += ["--incremental-country", code]
        subprocess.run(args_export, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
