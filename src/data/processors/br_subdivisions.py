"""Offline SINAN residence-state aggregation shared by scheduled and manual ingestion.

Only date/geography columns are read. Unknown or conflicting residence stays
in the audit totals and is never assigned to the notifying state.
"""
from collections import Counter
from datetime import date
from pathlib import Path
import struct

from src.data.crawlers.br import DEFAULT_SOURCE_NAME

STATE_CODES = dict(
    zip(
        [
            "11",
            "12",
            "13",
            "14",
            "15",
            "16",
            "17",
            "21",
            "22",
            "23",
            "24",
            "25",
            "26",
            "27",
            "28",
            "29",
            "31",
            "32",
            "33",
            "35",
            "41",
            "42",
            "43",
            "50",
            "51",
            "52",
            "53",
        ],
        [
            "RO",
            "AC",
            "AM",
            "RR",
            "PA",
            "AP",
            "TO",
            "MA",
            "PI",
            "CE",
            "RN",
            "PB",
            "PE",
            "AL",
            "SE",
            "BA",
            "MG",
            "ES",
            "RJ",
            "SP",
            "PR",
            "SC",
            "RS",
            "MS",
            "MT",
            "GO",
            "DF",
        ],
        strict=True,
    )
)


def residence_state(record):
    value = str(record.get("SG_UF") or "").strip().removesuffix(".0")
    municipality = str(record.get("ID_MN_RESI") or "").strip().removesuffix(".0")
    if value in STATE_CODES:
        if municipality[:2] in STATE_CODES and municipality[:2] != value:
            return None  # conflicting residence fields, never choose notifying UF
        return "BR-" + STATE_CODES[value]
    if value.upper() in STATE_CODES.values():
        return "BR-" + value.upper()
    if (
        value in {"", "0", "00", "99"}
        and len(municipality) in {6, 7}
        and municipality[:2] in STATE_CODES
    ):
        return "BR-" + STATE_CODES[municipality[:2]]
    return None


def projected_dbf_records(path):
    """Read only non-identifying date/geography fields from a validated DBF."""
    wanted = {
        "SG_UF",
        "ID_MN_RESI",
        "DT_NOTIFIC",
        "DT_SIN_PRI",
        "DT_DIAG",
        "DT_ACID",
        "NU_ANO",
    }
    with path.open("rb") as handle:
        header = handle.read(32)
        if len(header) != 32:
            raise ValueError("Truncated DBF header")
        count = struct.unpack_from("<I", header, 4)[0]
        header_length, record_length = struct.unpack_from("<HH", header, 8)
        if record_length < 2 or header_length < 33:
            raise ValueError("Invalid DBF record layout")
        fields, offset = {}, 1
        for _ in range((header_length - 33) // 32):
            descriptor = handle.read(32)
            name = descriptor[:11].split(b"\0", 1)[0].decode("ascii")
            length = descriptor[16]
            if name in wanted:
                if chr(descriptor[11]) not in {"C", "N", "D", "F"}:
                    raise ValueError(f"Unsupported DBF field type: {name}")
                fields[name] = (offset, offset + length)
            offset += length
        if offset != record_length:
            raise ValueError("DBF field lengths do not match records")
        if not ({"SG_UF", "ID_MN_RESI"} & fields.keys()):
            raise LookupError("No residence geography fields")
        handle.seek(header_length)
        chunk_records = max(1, 2_000_000 // record_length)
        remaining = count
        while remaining:
            take = min(remaining, chunk_records)
            chunk = handle.read(take * record_length)
            if len(chunk) != take * record_length:
                raise ValueError("Truncated DBF records")
            for start in range(0, len(chunk), record_length):
                if chunk[start : start + 1] == b"*":
                    continue
                yield {
                    name: chunk[start + left : start + right].decode("latin1").strip()
                    for name, (left, right) in fields.items()
                }
            remaining -= take


def record_month(record, fallback_year):
    for field in ("DT_NOTIFIC", "DT_SIN_PRI", "DT_DIAG", "DT_ACID"):
        value = record.get(field, "")
        if len(value) == 8 and value.isdigit():
            try:
                parsed = date(int(value[:4]), int(value[4:6]), int(value[6:]))
                return parsed.year, parsed.month, field
            except ValueError:
                pass
    value = record.get("NU_ANO", "")
    year = (
        int(value) if str(value).isascii() and str(value).isdigit() else fallback_year
    )
    return year, 1, "year_only_fallback"


def coalesce_source_rows(rows):
    """Annual extracts can contribute to the same notification month.

    Keep one selected file per prefix/year, then add contributions by residence
    state just like national aggregation; never add final and preliminary copies.
    """
    grouped = {}
    for row in rows:
        key = (row["CountryCode"], row["DiseaseCode"], row["Date"])
        if key not in grouped:
            grouped[key] = dict(row)
            continue
        existing = grouped[key]
        old_files = set(existing["SourceFiles"].split("|"))
        if old_files & set(row["SourceFiles"].split("|")):
            raise ValueError("Duplicate source-file contribution")
        existing["Cases"] = str(int(existing["Cases"]) + int(row["Cases"]))
        for field in ("SourceFiles", "SourceURLs", "DatasetStatus"):
            existing[field] = "|".join(
                sorted(set(existing[field].split("|")) | set(row[field].split("|")))
            )
    return list(grouped.values())



EXCLUDED_STATE_PREFIXES = frozenset({"NTRA", "SDTA"})


def aggregate_residence_file(item, dbf_path: Path, *, months=None):
    """Parse a collected DBF into all 27 states with reconciled audit counts."""
    if item.prefix in EXCLUDED_STATE_PREFIXES:
        raise ValueError(f"{item.prefix} is not an individual-case residence dataset")
    counts, month_counts, unknown, date_fields = Counter(), Counter(), Counter(), Counter()
    undated = 0
    for record in projected_dbf_records(dbf_path):
        year, month, basis = record_month(record, item.year)
        if basis == "year_only_fallback":
            undated += 1
            continue
        if months is not None and (year, month) not in months:
            continue
        month_counts[(year, month)] += 1
        date_fields[basis] += 1
        state = residence_state(record)
        if state is None:
            unknown[(year, month)] += 1
        else:
            counts[(state, year, month)] += 1
    if sum(counts.values()) + sum(unknown.values()) != sum(month_counts.values()):
        raise ValueError("State + unknown residence counts do not reconcile")
    rows = []
    known_months = {(year, month) for _, year, month in counts}
    for year, month in sorted(known_months):
        for state in sorted("BR-" + code for code in STATE_CODES.values()):
            rows.append({
                "CountryCode": state,
                "GeographyKey": f"country:{state}:national",
                "Date": f"{year:04d}-{month:02d}-01",
                "Year": str(year), "Month": str(month),
                "DiseaseCode": item.prefix, "RawDiseaseLabel": item.disease_name,
                "Cases": str(counts[(state, year, month)]),
                "DatasetStatus": item.dataset_status,
                "SourceFiles": item.filename, "SourceURLs": item.url,
                "Source": DEFAULT_SOURCE_NAME,
                "GeographyBasis": "residence",
                "DateBasis": "notification_then_symptom_diagnosis_accident",
                "SourceUpdateCadence": "closed_revisable", "SourceAuthoritative": True,
            })
    return {
        "rows": rows, "assigned_count": sum(counts.values()),
        "unknown_residence_count": sum(unknown.values()),
        "dated_record_count": sum(month_counts.values()), "undated_count": undated,
        "date_fields": dict(date_fields),
    }
