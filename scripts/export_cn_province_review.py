#!/usr/bin/env python3
"""Write local China province JSON/CSV review artifacts with the site projector.

This does not initialize publication state, acknowledge dirty marks, publish
public_disease_series, or upload anything. All reads share one DB snapshot.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

async def export(output):
    from sqlalchemy import text
    from src.core.database import get_db,dispose_database
    from src.core.database_snapshot import SNAPSHOT_ENV,export_read_snapshot
    from src.generation.site_data_export import _collect_country_export
    from src.generation.site_data_catalogue import load_standard_diseases,enrich_diseases_with_ontology
    from src.generation.site_data_queries import fetch_countries
    from src.generation.site_data_views import resolve_country_display_names
    from src.knowledge.catalogue import should_generate_public_disease_page
    from src.ontology import load_disease_ontology
    from scripts.repair_cn_province_data import write_json
    catalogue=load_standard_diseases(ROOT/'configs/standard_diseases.csv')
    diseases=[d for d in catalogue if should_generate_public_disease_page(d)]
    enrich_diseases_with_ontology(diseases,load_disease_ontology())
    by_id={d['disease_id']:d for d in diseases}
    output.mkdir(parents=True,exist_ok=True)
    receipt={'purpose':'local_review_only_not_published','generated_at':datetime.now(timezone.utc).isoformat(),'provinces':[]}
    try:
        async with export_read_snapshot() as env:
            previous=os.environ.get(SNAPSHOT_ENV);os.environ[SNAPSHOT_ENV]=env[SNAPSHOT_ENV]
            try:
                async with get_db() as db:
                    countries=[c for c in await fetch_countries(db) if c['code'].startswith('CN-')]
                names={c['code']:resolve_country_display_names(c['code'],c) for c in countries}
                for country in countries:
                    code=country['code']
                    async with get_db() as db:
                        # No readiness/seed helpers are called by this review.
                        await db.execute(text('SET TRANSACTION READ ONLY'))
                        result=await _collect_country_export(country,
                            country_name_by_code={c:v[0] for c,v in names.items()},
                            country_name_zh_by_code={c:v[1] for c,v in names.items()},
                            diseases_by_id=by_id,catalogue_ids={d['disease_id'] for d in catalogue},
                            country_briefs={},population_enabled=False,registry_tables_exist=True,session=db)
                    data=result['country_data'];data['artifact_purpose']='local_review_only_not_published'
                    path=output/(code+'.json');write_json(path,data)
                    with (output/(code+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
                        writer=csv.writer(f);writer.writerow(['province','disease_id','date','cases','deaths'])
                        for disease,series in data.get('disease_series',{}).items():
                            for i,date in enumerate(series.get('dates') or []):
                                cases=series.get('cases') or [];deaths=series.get('deaths') or []
                                writer.writerow([code,disease,date,cases[i] if i<len(cases) else None,deaths[i] if i<len(deaths) else None])
                    series=list(data.get('disease_series',{}).values())
                    item={'province':code,'disease_count':len(series),
                          'projected_death_points':sum(v is not None for s in series for v in s.get('deaths') or []),
                          'latest_date':max((v for s in series for v in s.get('dates') or []),default=None),
                          'json_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                    receipt['provinces'].append(item);print(json.dumps(item),flush=True)
                write_json(output/'receipt.json',receipt)
            finally:
                if previous is None:os.environ.pop(SNAPSHOT_ENV,None)
                else:os.environ[SNAPSHOT_ENV]=previous
    finally:await dispose_database()
    return receipt

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'data/processed/cn_province_repair_20261116/review_export')
    args=parser.parse_args();asyncio.run(export(args.output))
