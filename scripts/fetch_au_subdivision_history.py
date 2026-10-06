#!/usr/bin/env python3
"""Fetch all official NINDSS state/month/disease rows in annual query batches.

Archives public responses without auth tokens. Validate schema and truncation
before decoding; preserve privacy-suppressed counts as missing. The generated
monthly archives can be imported with scripts/update_au_subdivisions.py.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_year_payload(crawler, year: int):
    payload = crawler._build_location_payload(str(year), "Qtr 1", "January", "Dengue")
    command = payload["queries"][0]["Query"]["Commands"][0][
        "SemanticQueryDataShapeCommand"
    ]
    query = command["Query"]
    aliases = {item["Entity"]: item["Name"] for item in query["From"]}
    fact = aliases["DELTALOAD_DATAMART NOTIFIABLE_EVENT_FACT"]
    disease = aliases["DELTALOAD_DATAMART DISEASE_DIM"]

    def column(alias, prop, name):
        return {
            "Column": {
                "Expression": {"SourceRef": {"Source": alias}},
                "Property": prop,
            },
            "Name": name,
        }

    location, measure = query["Select"]
    query["Select"] = [
        column(fact, "DIAGNOSIS_YEAR_HIERARCHY", "year"),
        column(fact, "DIAGNOSIS_MONTHNAME", "month"),
        {**location, "Name": "state"},
        column(disease, "DISEASE NAME", "disease"),
        {**measure, "Name": "cases"},
    ]
    query["Where"] = [
        query["Where"][0],
        query["Where"][3],
        query["Where"][4],
        {
            "Condition": {
                "In": {
                    "Expressions": [
                        {
                            "Column": column(fact, "DIAGNOSIS_YEAR_HIERARCHY", "year")[
                                "Column"
                            ]
                        }
                    ],
                    "Values": [[{"Literal": {"Value": f"'{year}'"}}]],
                }
            }
        },
    ]
    command["Binding"] = {
        "Primary": {"Groupings": [{"Projections": list(range(5))}]},
        "DataReduction": {"DataVolume": 4, "Primary": {"Window": {"Count": 20000}}},
    }
    return payload


def decode_year_rows(raw, year: int):
    from scripts.update_au_subdivisions import parse_state_count
    from src.data.crawlers.au import normalize_au_state_code
    from src.data.crawlers.powerbi_public import decode_dsr_v2

    body = copy.deepcopy(raw)
    data = body["results"][0]["result"]["data"]
    names = {item.get("Name") for item in data.get("descriptor", {}).get("Select", [])}
    if names != {"year", "month", "state", "disease", "cases"}:
        raise ValueError("NINDSS annual response schema changed")
    for dataset in data["dsr"]["DS"]:
        if dataset.get("RT") or dataset.get("RestartTokens"):
            raise ValueError("NINDSS annual query was truncated")
        # DM0 is a subtotal encoded as named cells, not compact C rows. Only
        # decode DM1 detail rows to avoid interpreting the subtotal as a state.
        dataset["PH"] = [part for part in dataset.get("PH", []) if "DM1" in part]
    source_rows = decode_dsr_v2(body)
    if len(source_rows) >= 20000:
        raise ValueError("NINDSS response reached its reduction limit")
    month_numbers = {
        date(2000, month, 1).strftime("%B"): month for month in range(1, 13)
    }
    output = []
    keys = set()
    for row in source_rows:
        if not row.get("disease"):
            continue
        if str(row.get("year")) != str(year):
            raise ValueError("NINDSS returned a different year")
        month = month_numbers.get(row.get("month"))
        code = normalize_au_state_code(row.get("state"))
        if month is None or code is None:
            raise ValueError("NINDSS returned an unknown month or state")
        key = (month, code, row["disease"])
        if key in keys:
            raise ValueError(f"Duplicate NINDSS state count: {key}")
        keys.add(key)
        parsed = parse_state_count(row.get("cases"))
        # Broad grouping can return numeric small counts even when the monthly
        # disease view masks them. Retain that view's disclosure threshold.
        source_value = (
            "<5" if parsed is not None and 0 < parsed < 5 else row.get("cases")
        )
        if source_value == "<5":
            parsed = None
        output.append(
            {
                "year": year,
                "month": month,
                "state": code,
                "disease": row["disease"],
                "cases": parsed,
                "source_value": source_value,
            }
        )
    if not output:
        raise ValueError(f"No state data for {year}")
    return output


def write_monthly_archives(rows, root: Path, today: date):
    grouped = {}
    for row in rows:
        if date(row["year"], row["month"], 1) >= today.replace(day=1):
            continue
        key = (row["year"], row["month"], row["disease"])
        grouped.setdefault(key, {})[row["state"]] = row["source_value"]
    for (year, month, disease), counts in grouped.items():
        path = root / str(year) / f"{month:02d}"
        path.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", disease).strip("_") + ".json"
        (path / filename).write_text(
            json.dumps(
                {
                    "disease": disease,
                    "year": year,
                    "month": month,
                    "parsed_counts": counts,
                    "source_url": "https://nindss.health.gov.au/pbi-dashboard/",
                    "query_grain": "state_month_disease",
                    "suppressed_values": "preserved_as_source_strings",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    return len(grouped)


def fetch_history(crawler, years, output: Path, workers=4):
    output.mkdir(parents=True, exist_ok=True)

    def fetch(year):
        path = output / f"{year}.json"
        if path.exists():
            raw = json.loads(path.read_text())
        else:
            _, raw = crawler._execute_payload(
                build_year_payload(crawler, year), timeout=180
            )
            if raw is None:
                raise RuntimeError(f"Official query failed for {year}")
        rows = decode_year_rows(raw, year)
        # Save only a validated response so a partial file cannot be reused.
        path.write_text(json.dumps(raw) + "\n")
        monthly = write_monthly_archives(
            rows, output / "monthly", datetime.now(ZoneInfo("Australia/Sydney")).date()
        )
        return {
            "year": year,
            "source_rows": len(rows),
            "monthly_disease_files": monthly,
            "suppressed_or_missing": sum(row["cases"] is None for row in rows),
        }

    completed, failures = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, year): year for year in years}
        for future in as_completed(futures):
            try:
                result = future.result()
                completed.append(result)
                print(json.dumps(result), flush=True)
            except Exception as exc:  # noqa: BLE001 -- collect each failed annual query in the report
                failures.append({"year": futures[future], "error": str(exc)})
                print(json.dumps(failures[-1]), flush=True)
    report = {
        "completed": sorted(completed, key=lambda row: row["year"]),
        "failures": failures,
    }
    (output / "fetch_report.json").write_text(json.dumps(report, indent=2) + "\n")
    if failures:
        raise RuntimeError(
            f"{len(failures)} annual queries failed; see fetch_report.json"
        )
    return report


def main():
    from src.core.logging import get_logger
    from src.data.crawlers.au import AustraliaNINDSSCrawler

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2000)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 7))
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/raw/au_state_history"
    )
    args = parser.parse_args()
    if (
        not 1990
        <= args.start_year
        <= args.end_year
        <= datetime.now(ZoneInfo("Australia/Sydney")).date().year
    ):
        parser.error("Choose observed years from 1990 to the current year")
    # Existing crawler diagnostics include token prefixes; keep credentials out
    # of command output while capturing the public dashboard connection.
    get_logger(__name__).disable("src.data.crawlers.au")
    crawler = AustraliaNINDSSCrawler()
    if not crawler._load_config():
        raise RuntimeError("Official NINDSS dashboard connection unavailable")
    fetch_history(
        crawler, range(args.start_year, args.end_year + 1), args.output, args.workers
    )


if __name__ == "__main__":
    main()
