#!/usr/bin/env python3
"""Inspect saved province collection evidence; never fetch or write the database."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.cn_province_health import build_health_report, render_health_markdown


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/processed/cn_province_repair_20261116",
                        help="Batch directory containing provinces/ and optional approved image_review/ artifacts")
    parser.add_argument("--output-dir", type=Path, help="Local health report directory (default: INPUT/health)")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Assessment date YYYY-MM-DD (default: current Asia/Shanghai date)")
    parser.add_argument("--stale-months", type=int, default=3, help="Operational stale-month budget; not an official publication commitment")
    parser.add_argument("--window-months", type=int, default=12, help="Closed-month window for evidence gap checks")
    parser.add_argument("--reference-months", type=int, default=6, help="Maximum table age for disease-set decrease comparisons")
    args = parser.parse_args(argv)
    try:
        report = build_health_report(args.input, as_of=args.as_of, stale_months=args.stale_months,
                                     window_months=args.window_months, reference_months=args.reference_months)
    except ValueError as exc:
        parser.error(str(exc))
    output = args.output_dir or args.input / "health"
    output.mkdir(parents=True, exist_ok=True)
    for name, content in (("cn_province_health.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
                          ("cn_province_health.md", render_health_markdown(report))):
        path = output / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    print(json.dumps({"output_dir": str(output), **report["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
