#!/usr/bin/env python3
"""Register reviewed provincial death metrics without changing existing mappings.

Dry-run by default. The applied mapping release retains exactly every active
assertion and adds only the approved provincial death definitions.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(*, apply=False, receipt_path=None):
    from sqlalchemy import select, text
    from src.core.database import get_db, dispose_database
    from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
    from src.domain import DiseaseMappingAssertion as Assertion, DiseaseMappingRelease as Release, DiseaseMappingReleaseItem as Item
    from src.ontology import load_disease_ontology
    from src.services.disease_ontology_sync_service import restore_missing_registry_series
    from src.services.disease_mapping_registry_service import DiseaseMappingRegistryService
    ontology = load_disease_ontology()
    definitions = [s for s in ontology.to_dict()['source_series'] if s['source_id']=='SRC_CN_PROV_MONTHLY_REPORT' and s['measure']=='deaths']
    result={'apply':apply,'reviewed_death_series':len(definitions),'created_at':datetime.now(timezone.utc).isoformat()}
    try:
        async with get_db() as db:
            if not apply:
                await db.execute(text('SET TRANSACTION READ ONLY'))
            else:
                await acquire_country_data_mutation_lock(db,'CN')
                await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('globalid:disease_mapping_release:v1'))"))
            active=(await db.execute(select(Release).where(Release.status=='active'))).scalar_one_or_none()
            old_ids=set((await db.execute(select(Item.assertion_id).where(Item.release_id==active.id))).scalars()) if active else set()
            result.update(previous_release_id=active.id if active else None,retained_assertions=len(old_ids))
            if apply:
                restored=await restore_missing_registry_series(db,ontology,{s['id'] for s in definitions},country_code='CN')
                if len(restored)!=len(definitions):raise ValueError('Incomplete death-series registry restoration')
                service=DiseaseMappingRegistryService();new_ids=set()
                for definition in definitions:
                    discovery=await service.discover_rows(db,country_code='CN',source_id=definition['source_id'],
                        rows=[{'SourceDiseaseCode':definition['local_codes'][0],'RawDiseaseLabel':definition['local_labels'][0],
                               'DefinitionVersion':definition['definition_version'],'CaseDefinition':definition['case_definition']}],
                        notify=False,increment_occurrence=False)
                    category_id=discovery['touched_category_ids'][0]
                    assertion=await service._upsert_imported_assertion(db,category_id=category_id,target_kind='concept',
                        target_code=definition['concept_id'],relation=definition['mapping_relation'],
                        comparability=definition['comparability'],projection_policy='canonical' if definition['mapping_relation'] in {'exact','narrower'} else 'no_projection',
                        aggregation_policy=definition['aggregation_policy'],
                        evidence=[{'type':'ontology_series','series_id':definition['id'],'paired_case_series_code':definition['paired_case_series_code']}],
                        reasoning='Explicit official provincial death column; independently typed metric, never used as case counts.')
                    if assertion is None:
                        assertion=(await db.execute(select(Assertion).where(Assertion.category_id==category_id,
                            Assertion.target_code==definition['concept_id'],Assertion.assertion_status=='approved'))).scalar_one()
                    new_ids.add(assertion.id)
                ids=old_ids|new_ids
                result['new_assertions']=len(ids-old_ids)
                if ids!=old_ids:
                    assertions=list((await db.execute(select(Assertion).where(Assertion.id.in_(ids)).order_by(Assertion.assertion_key))).scalars())
                    payload=[{'key':a.assertion_key,'category_id':a.category_id,'target':[a.target_kind,a.target_code],
                              'relation':a.mapping_relation,'valid':[str(a.valid_from) if a.valid_from else None,str(a.valid_to) if a.valid_to else None]} for a in assertions]
                    checksum=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                    release=Release(release_code='DMR-CN-PROVINCE-DEATHS-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'),
                                    status='draft',checksum=checksum,description='Retain active mappings and add explicit China provincial monthly death metrics.',
                                    created_by='cn-province-repair',metadata_={'assertion_count':len(ids),'retained_assertion_ids':sorted(old_ids),'added_assertion_ids':sorted(ids-old_ids)})
                    db.add(release);await db.flush()
                    db.add_all(Item(release_id=release.id,assertion_id=i) for i in sorted(ids));await db.flush()
                    # Existing assertion IDs are retained byte-for-byte; only
                    # new death categories change interpretation.
                    actual=set((await db.execute(select(Item.assertion_id).where(Item.release_id==release.id))).scalars())
                    if actual!=ids or not old_ids<=actual:raise ValueError('Existing active mapping was lost')
                    await service.activate_release(db,release.id)
                    result['release_id']=release.id
                else:result['release_id']=active.id if active else None
                await db.commit()
        if receipt_path:
            receipt_path.parent.mkdir(parents=True,exist_ok=True)
            receipt_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(result,ensure_ascii=False))
    finally:
        await dispose_database()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--receipt',type=Path)
    a=p.parse_args();asyncio.run(run(apply=a.apply,receipt_path=a.receipt))
