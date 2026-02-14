#!/usr/bin/env python3
"""Restart migration: merge history CSVs with special handling for 201301/201302.

Usage:
  python scripts/restart_migration.py

By default reads from `data/raw/cn/history` and writes to
`data/processed/history_merged.csv`.
"""
from pathlib import Path
import argparse
import pandas as pd
import sys


def read_csv_flexible(path: Path):
    for enc in ("utf-8", "utf-8-sig", "gb18030", "latin1"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc, low_memory=False, on_bad_lines="warn")
        except Exception:
            last_exc = sys.exc_info()
    raise last_exc[1]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--src-dir", default="data/raw/cn/history", help="Source history folder")
    p.add_argument("--out", default="data/processed/history_merged.csv", help="Output CSV path")
    p.add_argument("--special", nargs="*", default=["2013 01.csv", "2013 02.csv"],
                   help="Files to treat as special (add date from filename)")
    p.add_argument("--columns", help="Comma-separated list of columns to keep (default: columns from main files)")
    return p.parse_args()


def month_from_filename(name: str):
    # Expect formats like 201301.csv or "2024 August.csv" -> try to extract yyyyMM or yyyy month
    base = Path(name).stem
    # Try YYYYMM
    if len(base) == 6 and base.isdigit():
        return f"{base[:4]}-{base[4:6]}-01"
    # Try formats like '2024 August' or '2025 April'
    parts = base.split()
    if len(parts) >= 2 and parts[0].isdigit():
        mm = parts[1]
        # if month is numeric like '01' or '1'
        if mm.isdigit():
            mmn = mm.zfill(2)
            return f"{parts[0]}-{mmn}-01"
        # map month names to numbers (English)
        months = {"january": "01", "february": "02", "march": "03", "april": "04", "may": "05",
                  "june": "06", "july": "07", "august": "08", "september": "09", "october": "10",
                  "november": "11", "december": "12"}
        m = months.get(mm.lower()[:3] + mm.lower()[3:] if len(mm) > 3 else mm.lower(), None)
        if m is None:
            # fallback try first 3 letters
            m = months.get(mm.lower()[:3])
        if m:
            return f"{parts[0]}-{m}-01"
    return None


def main():
    args = parse_args()
    src = Path(args.src_dir)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = sorted([p for p in src.glob("*.csv")])
    special_names = set(args.special)

    main_files = [p for p in files if p.name not in special_names]

    if not main_files:
        print("No main files found in", src)
        return

    print(f"Reading {len(main_files)} main files...")
    dfs = []
    for p in main_files:
        try:
            df = read_csv_flexible(p)
            df["__source_file"] = p.name
            dfs.append(df)
        except Exception as e:
            print(f"Failed reading {p}: {e}")

    # Concatenate and dedupe main files
    main_df = pd.concat(dfs, ignore_index=True, sort=False)
    before = len(main_df)
    main_df = main_df.drop_duplicates()
    after = len(main_df)
    print(f"Main rows: {before} -> deduped {after}")

    # Determine columns to keep
    if args.columns:
        keep_cols = [c.strip() for c in args.columns.split(",") if c.strip()]
    else:
        # default to columns present in the main_df (preserve order)
        keep_cols = list(main_df.columns)

    # Ensure we keep the __source_file column so provenance is clear
    if "__source_file" not in keep_cols:
        keep_cols.append("__source_file")

    # Process special files (201301/201302)
    special_df_list = []
    for name in args.special:
        p = src / name
        if not p.exists():
            print(f"Special file not found, skipping: {p}")
            continue
        try:
            df = read_csv_flexible(p)
        except Exception as e:
            print(f"Failed reading special file {p}: {e}")
            continue
        # normalize known Chinese simple format files: 病名,发病数,死亡数
        if set(["病名", "发病数", "死亡数"]).issubset(set(df.columns)):
            df = df.rename(columns={"病名": "DiseasesCN", "发病数": "Cases", "死亡数": "Deaths"})
            # add date fields expected by main schema
            dt = month_from_filename(p.name)
            if dt:
                df["Date"] = dt
                df["YearMonthDay"] = dt.replace("-", "/")
                # keep a readable month label from filename
                df["YearMonth"] = Path(p.name).stem
            else:
                df["Date"] = pd.NA
                df["YearMonthDay"] = pd.NA
                df["YearMonth"] = pd.NA
        else:
            # add date column if not present (other formats)
            if "date" not in df.columns and "Date" not in df.columns:
                dt = month_from_filename(p.name)
                if dt:
                    df["Date"] = dt
                else:
                    df["Date"] = pd.NA
        # set source and URL for known 2013 jan/feb files
        if p.name == "2013 01.csv":
            df["URL"] = "https://www.nhc.gov.cn/jkj/c100062/201302/a1dfe2b6e8114b3482986054fa3e0e3b.shtml"
            df["Source"] = "GOV Data"
        elif p.name == "2013 02.csv":
            df["URL"] = "https://www.nhc.gov.cn/jkj/c100062/201303/9fd9b24d1b244d67b57eebdce45af612.shtml"
            df["Source"] = "GOV Data"
        # fill generic English 'Diseases' column from Chinese name if missing
        if "Diseases" not in df.columns and "DiseasesCN" in df.columns:
            df["Diseases"] = df["DiseasesCN"]
        df["__source_file"] = p.name
        special_df_list.append(df)

    # Ensure all dataframes have the keep columns
    def align_cols(df, cols):
        for c in cols:
            if c not in df.columns:
                df[c] = pd.NA
        return df[cols]

    main_aligned = align_cols(main_df, keep_cols)
    specials_aligned = [align_cols(df, keep_cols) for df in special_df_list]

    final = pd.concat([main_aligned] + specials_aligned, ignore_index=True, sort=False)
    before_final = len(final)
    # Prefer deduplication on meaningful fields if present: Diseases/Date/Cases/Deaths
    preferred = ["Diseases", "Disease", "Date", "Cases", "Deaths"]
    dedupe_cols = [c for c in preferred if c in final.columns]
    if dedupe_cols:
        final = final.drop_duplicates(subset=dedupe_cols, keep="first")
    else:
        # Fallback: drop duplicates on all columns except provenance column
        fallback = [c for c in final.columns if c != "__source_file"]
        if fallback:
            final = final.drop_duplicates(subset=fallback, keep="first")
        else:
            final = final.drop_duplicates(keep="first")
    after_final = len(final)
    print(f"Final rows: {before_final} -> deduped {after_final} (dedupe cols: {dedupe_cols or fallback})")

    # Save
    final.to_csv(out, index=False)
    print(f"Saved merged CSV to {out} (columns: {len(final.columns)})")


if __name__ == "__main__":
    main()
