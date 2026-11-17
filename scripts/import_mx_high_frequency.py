#!/usr/bin/env python3
"""Capture/replay DGE monthly/weekly source facts without database writes."""
from __future__ import annotations
import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.data.crawlers.mx_high_frequency import MONTHLY_SCOPE,WEEKLY_SCOPE
from src.data.processors.mx_high_frequency import MexicoDGEHighFrequencyUpdater
from src.services.ingestion.countries.mx_high_frequency import collect,organize


class LocalWriter:
    def __init__(self,directory):self.directory=directory;self.artifacts=[]
    async def write(self,payload,*,source_url,kind,metadata=None):
        digest=hashlib.sha256(payload).hexdigest();extension="pdf" if payload.startswith(b"%PDF") else "json" if kind.endswith(("plan","ocr")) else "html"
        path=self.directory/f"{digest}.{extension}";path.write_bytes(payload)
        item=SimpleNamespace(relative_path=str(path),source_url=source_url,kind=kind,metadata_=metadata or {},sha256=digest,byte_size=len(payload))
        self.artifacts.append(item);return item


async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source",choices=[MONTHLY_SCOPE,WEEKLY_SCOPE],required=True)
    p.add_argument("--start-year",type=int)
    p.add_argument("--end-year",type=int)
    p.add_argument("--latest-reports",type=int)
    p.add_argument("--weekly-index-url")
    p.add_argument("--directory",type=Path,required=True)
    p.add_argument("--replay",action="store_true")
    p.add_argument("--force",action="store_true")
    args=p.parse_args();directory=args.directory
    if not directory.resolve().is_relative_to((ROOT/"data/raw").resolve()):p.error("Directory must be under data/raw for verified offline artifact reading")
    directory.mkdir(parents=True,exist_ok=True);manifest=directory/"artifacts_manifest.json"
    if args.replay:
        artifacts=[SimpleNamespace(**x) for x in json.loads(manifest.read_text())]
    else:
        writer=LocalWriter(directory)
        try:
            await collect(writer,source=args.source,start_year=args.start_year,end_year=args.end_year,
                latest_reports=args.latest_reports,weekly_index_url=args.weekly_index_url,force=args.force)
        finally:
            manifest.write_text(json.dumps([vars(x) for x in writer.artifacts],ensure_ascii=False,indent=2))
        artifacts=writer.artifacts
    result=organize(artifacts,updater=MexicoDGEHighFrequencyUpdater(source=args.source),source=args.source,
        start_year=args.start_year,end_year=args.end_year)
    output=ROOT/"data/current/mx"/args.source;output.mkdir(parents=True,exist_ok=True)
    with (output/"observations.csv").open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=sorted({k for r in result.rows for k in r}));w.writeheader();w.writerows(result.rows)
    (output/"source_audit.json").write_text(json.dumps(result.source_audit,ensure_ascii=False,indent=2))
    print(json.dumps({"source":args.source,"rows":len(result.rows),"source_latest_date":str(result.source_latest_date),"output":str(output),"failed_count":result.source_audit["failed_count"],"database_writes":False}))


if __name__=="__main__":asyncio.run(main())
