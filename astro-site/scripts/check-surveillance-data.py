"""Compare browser surveillance exports with the full generated site snapshot."""

import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "src" / "data"
BROWSER = ROOT / "public" / "site-data"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    issues: list[str] = []
    counts = {"diseases": 0, "countries": 0, "series": 0, "source_only": 0}
    source_by_country: dict[str, dict[str, list[dict]]] = defaultdict(dict)
    full_signatures: dict[str, dict[tuple[str, str], tuple]] = {
        "diseases": {},
        "countries": {},
    }

    for kind, source_key, compact_key in (
        ("diseases", "country_series", "cc"),
        ("countries", "disease_series", "id"),
    ):
        paths = sorted(path for path in (FULL / kind).glob("*.json") if path.stem != "index")
        if not paths:
            issues.append(f"{kind}: no full data files")
        for path in paths:
            browser_path = BROWSER / kind / path.name
            if not browser_path.exists():
                issues.append(f"{browser_path}: missing browser export")
                continue
            snapshot = read(path)
            full = snapshot.get(source_key, {})
            compact = read(browser_path)
            dates = compact.get("dates", [])
            rows = {row[compact_key]: row for row in compact.get("series", [])}
            counts[kind] += 1
            if full.keys() != rows.keys():
                issues.append(f"{path}: series keys differ")
            for key, source in full.items():
                row = rows.get(key)
                if row is None:
                    continue
                identity = (
                    (key, snapshot["disease_id"])
                    if kind == "diseases"
                    else (snapshot["country_code"], key)
                )
                full_signatures[kind][identity] = tuple(
                    tuple(source.get(field) or [])
                    for field in ("dates", "cases", "deaths", "weekly_equiv_cases")
                )
                counts["series"] += 1
                indices = row.get("x", [])
                if any(not isinstance(index, int) or index < 0 or index >= len(dates) for index in indices):
                    issues.append(f"{path}:{key}: invalid date index")
                elif [dates[index] for index in indices] != (source.get("dates") or []):
                    issues.append(f"{path}:{key}: dates differ")
                for field, compact_field in (
                    ("cases", "c"),
                    ("deaths", "d"),
                    ("weekly_equiv_cases", "w"),
                ):
                    if (source.get(field) or []) != (row.get(compact_field) or []):
                        issues.append(f"{path}:{key}: {field} differs")
                sources = source.get("source_series") or []
                if len(sources) != len(row.get("source_series") or []):
                    issues.append(f"{path}:{key}: source metadata count differs")
                if not source.get("dates") and sources:
                    counts["source_only"] += 1
                if kind == "diseases" and sources:
                    source_by_country[key][snapshot["disease_id"]] = sources

    if full_signatures["diseases"].keys() != full_signatures["countries"].keys():
        issues.append("country and disease full snapshots have different series identities")
    for identity, signature in full_signatures["diseases"].items():
        if full_signatures["countries"].get(identity) != signature:
            issues.append(f"{identity}: country and disease views differ")

    source_count = 0
    for country, expected in source_by_country.items():
        path = BROWSER / "countries" / f"{country.lower()}-source-series.json"
        if not path.exists():
            issues.append(f"{path}: missing lazy source export")
            continue
        actual = read(path).get("series", {})
        if actual.keys() != expected.keys():
            issues.append(f"{path}: disease keys differ")
        for disease, sources in expected.items():
            if actual.get(disease) != sources:
                issues.append(f"{path}:{disease}: source observations differ")
            source_count += len(actual.get(disease, []))

    if issues:
        for issue in issues[:20]:
            print(issue)
        print(f"FAIL: {len(issues)} mismatches")
        return 1
    print(
        f"PASS: {counts['diseases']} disease files, {counts['countries']} country files, "
        f"{counts['series'] // 2} series in each projection, "
        f"{counts['source_only'] // 2} source-only entries, {source_count} lazy source series"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
