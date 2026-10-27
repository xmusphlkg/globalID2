#!/usr/bin/env python3
"""Verify every exported subdivision rate against its official local/year denominator."""

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.import_subdivision_population import (
    DATA,
    load_abs_population,
    load_cn_population,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population-csv", type=Path)
    args = parser.parse_args()
    latest_path = ROOT / "data/processed/official_population/latest.json"
    snapshot = args.population_csv
    if snapshot is None and latest_path.exists():
        latest = json.loads(latest_path.read_text())
        if latest.get("applied"):
            snapshot = Path(latest["snapshot"]) / "population.csv"
    rows = (
        load_cn_population(snapshot)
        if snapshot
        else (
            load_cn_population(DATA / "cn_nbs_year_end_2000_2024.csv")
            + load_abs_population(DATA / "abs_310104_mar2026.xlsx")
        )
    )
    population = {(r.country_code, r.year): r.population for r in rows}
    counts = {}
    errors = []
    population_only = []
    for code in sorted({code for code, year in population}):
        path = ROOT / "astro-site/src/data/countries" / (code.lower() + ".json")
        if not path.exists():
            if code.startswith("BR-"):
                population_only.append(code)
                continue
            errors.append([code, "missing country dataset"])
            continue
        d = json.loads(path.read_text())
        n = 0
        for disease, item in d.get("disease_series", {}).items():
            dates = item["dates"]
            cases = item.get("cases", [])
            rates = item.get("incidence_rates", [])
            sources = item.get("incidence_sources", [])
            for i, rate in enumerate(rates):
                if (
                    rate is None
                    or i >= len(sources)
                    or sources[i]
                    not in {"nbs_computed", "abs_computed", "ibge_computed"}
                ):
                    continue
                p = population.get((code, int(dates[i][:4])))
                if p is None or cases[i] is None:
                    errors.append(
                        [
                            code,
                            disease,
                            dates[i],
                            "rate without numerator or local denominator",
                        ]
                    )
                    continue
                expected = cases[i] / p * 100000
                if not math.isclose(rate, expected, rel_tol=1e-8, abs_tol=1e-8):
                    errors.append([code, disease, dates[i], rate, expected])
                n += 1
        counts[code] = n
    for code, n in counts.items():
        if not n:
            errors.append([code, "no exported rate points"])
    report = {
        "verified_rate_points": sum(counts.values()),
        "by_location": counts,
        "errors": errors,
        "population_only_locations": population_only,
        "population_snapshot": str(snapshot)
        if snapshot
        else "original CN/ABS snapshot",
    }
    (ROOT / "data/processed/subdivision_population/rate_verification.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "verified_rate_points": sum(counts.values()),
                "locations_with_rates": sum(n > 0 for n in counts.values()),
                "errors": errors[:5],
            }
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
