#!/usr/bin/env python3
"""Independently reconcile weekly CSV totals against original worksheet XML.

Uses ZIP/XML instead of the openpyxl aggregation reader. Does not verify disease
mapping or turn notification records into confirmed cases/onset incidence.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.imports.collect_co_microdatos import write_json
from src.data.crawlers.co_microdatos import archive_records, sha256_file

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def workbook_week_counts(argument):
    root, record = argument
    path = root / record["path"]
    if sha256_file(path) != record["sha256"]:
        raise ValueError("Original hash mismatch")
    counts = Counter()
    with zipfile.ZipFile(path) as book:
        # The accepted 2025 individual schema has one worksheet. Reject a new
        # layout rather than accidentally verify another worksheet's counts.
        structure = etree.fromstring(book.read("xl/workbook.xml"))
        if len(structure.find(NS + "sheets")) != 1:
            raise ValueError("Independent XML verifier requires one worksheet")
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            with book.open("xl/sharedStrings.xml") as stream:
                for _, item in etree.iterparse(stream, events=("end",), tag=NS + "si"):
                    shared.append("".join(item.itertext(NS + "t")))
                    item.clear()

        shared_formulas = {}

        def value(cell):
            formula = cell.find(NS + "f")
            if formula is not None:
                expression = formula.text
                if formula.get("t") == "shared":
                    if expression:
                        shared_formulas[formula.get("si")] = expression
                    else:
                        expression = shared_formulas.get(formula.get("si"))
                match = re.fullmatch(r'"([0-9]+)"', expression or "")
                if not match:
                    raise ValueError("Nonliteral formula in original week/year/event")
                return match[1]
            raw = cell.findtext(NS + "v")
            if cell.get("t") == "s":
                return shared[int(raw)]
            if cell.get("t") == "inlineStr":
                return "".join(cell.itertext(NS + "t"))
            return raw

        with book.open("xl/worksheets/sheet1.xml") as stream:
            columns = None
            for _, row in etree.iterparse(stream, events=("end",), tag=NS + "row"):
                if columns is None:
                    header = {re.sub(r"\d+", "", c.get("r")): value(c) for c in row}
                    columns = {name: col for col, name in header.items() if name in {"ANO", "SEMANA", "COD_EVE"}}
                    if set(columns) != {"ANO", "SEMANA", "COD_EVE"}:
                        raise ValueError("Original individual header changed")
                else:
                    cells = {re.sub(r"\d+", "", c.get("r")): c for c in row}
                    if not cells:
                        row.clear()
                        continue
                    year, week, event = (int(value(cells[columns[field]])) for field in ("ANO", "SEMANA", "COD_EVE"))
                    if year != record["year"] or str(event) != record["event_code"] or not 1 <= week <= 53:
                        raise ValueError("Original period or event mismatch")
                    counts[week] += 1
                row.clear()
                while row.getprevious() is not None:
                    del row.getparent()[0]
    return record["year"], record["event_code"], dict(counts)


def verify(archive, output, workers=4):
    audit = json.loads((output / "audit.json").read_bytes())
    csv_path = output / "weekly_notification_records.csv"
    if sha256_file(csv_path) != audit["source_csv_sha256"]:
        raise ValueError("Aggregate CSV hash mismatch")
    records = archive_records(archive, start_year=audit["requested_years"][0],
                              end_year=audit["requested_years"][1],
                              events=set(audit["selected_events"]) if audit["selected_events"] else None)
    accepted = {(a["year"], a["event_code"]): a for a in audit["workbooks"]
                if a["status"] == "aggregated_source_records"}
    stored = Counter()
    with csv_path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            dims = json.loads(row["Dimensions"])
            stored[(dims["source_epi_year"], row["SourceDiseaseCode"].removeprefix("microdatos_"),
                    dims["source_epi_week"])] += int(row["Cases"])
    expected = Counter()
    arguments = [(archive, r) for r in records if (r["year"], r["event_code"]) in accepted]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for year, event, counts in pool.map(workbook_week_counts, arguments):
            if sum(counts.values()) != accepted[(year, event)]["aggregated_records"]:
                raise ValueError("Workbook total differs from aggregate audit")
            expected.update({(year, event, week): count for week, count in counts.items()})
    result = {"verified": expected == stored and sum(stored.values()) == audit["aggregated_records"],
              "method": "independent_original_zip_xml_week_counts_vs_csv_strata_sums",
              "verified_individual_workbooks": len(arguments), "event_week_totals": len(expected),
              "notification_records": sum(expected.values()), "csv_sha256": audit["source_csv_sha256"],
              "quarantined_workbooks": audit["quarantined_workbooks"],
              "quarantines_are_not_case_counts": True, "public_release_enabled": False}
    if sha256_file(csv_path) != audit["source_csv_sha256"]:
        raise ValueError("Aggregate CSV changed during independent verification")
    write_json(output / "verification.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "data/raw/co/sivigila-microdatos")
    parser.add_argument("--output", type=Path, default=ROOT / "data/current/co/microdatos-weekly")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    result = verify(args.archive.resolve(), args.output.resolve(), args.workers)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
