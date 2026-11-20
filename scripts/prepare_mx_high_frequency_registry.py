#!/usr/bin/env python3
"""Produce independent review patches from captured DGE observations, no DB writes."""
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.data.crawlers.mx_high_frequency import SOURCE_IDS,SOURCE_LABELS
from src.services.mx_high_frequency_review import mx_source_label_icd_conflict


def folded(value):
    value=''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',re.sub(r'[§*•]','',value)).strip().casefold()


def icd_key(value):
    return re.sub(r'\s+','',value).casefold()


def main():
    # Reuse only already reviewed, exact current-ICD count-category targets.
    annual=list(csv.DictReader((ROOT/'configs/mapping/reviewed/mx.csv').open(encoding='utf-8-sig')))
    targets={}
    for item in annual:
        if (item['definition_version']=='mx_dge_annual_icd10_v1'
            and item['local_code'].endswith('__all__all__cases')
            and item['target_kind']=='concept' and item['mapping_relation']=='exact'
            and item['projection_policy']=='canonical'):
            targets.setdefault(folded(item['local_name']),set()).add(item['target_code'])
    monthly_rows=list(csv.DictReader((ROOT/'data/current/mx/mx_dge_monthly/observations.csv').open(encoding='utf-8-sig')))
    expected_icds={}
    for row in monthly_rows:
        if row['Sex']=='all':expected_icds.setdefault(folded(row['RawDiseaseLabel']),set()).add(icd_key(row['SourceICDCode']))
    expected_icds[folded('Rabia humana')]={icd_key('A82.0, A82.1')}
    reviewed=json.loads((ROOT/'configs/mx_high_frequency_concept_review.json').read_text())
    supplementary={}
    for rule in reviewed['rules']:
        for label in rule['source_labels']:
            for icd in rule['source_icd_codes']:
                supplementary[(folded(label),icd_key(icd))]=rule
    ontology={'schema_version':1,'sources':[],'source_series':[],'availability':[]}
    mappings={'schema_version':1,'reviewed_on':'2026-11-17','rows':[]}
    counts={}
    for scope,source_id in SOURCE_IDS.items():
        ontology['sources'].append({'id':source_id,'country_code':'MX','labels':{'en':SOURCE_LABELS[scope]},'legacy_data_sources':[SOURCE_LABELS[scope]]})
        path=ROOT/'data/current/mx'/scope/'observations.csv'
        observations=list(csv.DictReader(path.open(encoding='utf-8-sig')))
        categories={};category_labels={}
        for row in observations:
            key=(row['SourceDiseaseCode'],row['DefinitionVersion'])
            categories.setdefault(key,row)
            category_labels.setdefault(key,set()).add(row['RawDiseaseLabel'])
        counts[scope]={'observations':len(observations),'categories':len(categories),'canonical_categories':0}
        for (code,version),row in sorted(categories.items()):
            candidates=targets.get(folded(row['RawDiseaseLabel']),set())
            icd_matches=icd_key(row['SourceICDCode']) in expected_icds.get(folded(row['RawDiseaseLabel']),set())
            target=next(iter(candidates)) if len(candidates)==1 and icd_matches else None
            reviewed_rule=supplementary.get((folded(row['RawDiseaseLabel']),icd_key(row['SourceICDCode'])))
            if reviewed_rule:target=reviewed_rule['target_code']
            source_conflict=mx_source_label_icd_conflict(row['RawDiseaseLabel'],row['SourceICDCode'])
            if source_conflict:
                target=None
                reviewed_rule=None
            dims=json.loads(row['Dimensions'])
            incident=(row['Measure']=='case_notifications' and row['Sex']=='all'
                and dims.get('source_case_definition')!='estimated_source_formula')
            canonical=bool(target and incident)
            if canonical:counts[scope]['canonical_categories']+=1
            relation=reviewed_rule['mapping_relation'] if reviewed_rule else 'exact' if target else 'unmapped'
            policy='canonical' if canonical else 'no_projection'
            token=hashlib.sha256((source_id+'|'+code+'|'+version).encode()).hexdigest()[:20]
            series_id='SER_MX_HF_'+token
            notes=((reviewed_rule['review_basis']+' Original ICD: '+row['SourceICDCode']+'. ') if reviewed_rule else
                'Exact source-label target and original ICD agreement reused from unambiguous reviewed ICD-10 national all-sex count category. '
                if target else 'No unambiguous reviewed current-ICD count-category target for the exact source label. ')
            if source_conflict:
                notes=('Unresolved original source label/ICD conflict: printed '+row['SourceICDCode']+
                    ', expected '+source_conflict['expected_icd_code']+' for the printed label. '
                    'No official correction has been verified; preserve original code and prohibit canonical projection. ')
            notes+=('Native monthly new notifications or source-verified table-specific weekly notifications. '
                'Cumulative, sex-specific and estimated measurements cannot supply incident case curves. '
                'Preserve source calendar, case definition, ICD label, explicit zeros and missingness; never sum states, strata, weekly/monthly/annual or reporting systems.')
            mapping={'country_code':'MX','disease_id':target or '', 'local_name':row['RawDiseaseLabel'],
                'local_code':code,'category':'Reviewed DGE high-frequency source category','aliases':'|'.join(sorted(category_labels[(code,version)]-{row['RawDiseaseLabel']})),'notes':notes,
                'data_source':'','source_id':source_id,'series_id':series_id,'mapping_scope':'source_category_dimension',
                'target_kind':'concept' if target else 'group','target_code':target or 'G_UNMAPPED_SOURCE_CATEGORIES',
                'mapping_relation':relation,'comparability':'conditional' if target else 'not_comparable',
                'projection_policy':policy,'aggregation_policy':'non_additive','definition_version':version}
            mappings['rows'].append(mapping)
            series={'id':series_id,'source_id':source_id,('concept_id' if target else 'group_id'):target or 'G_UNMAPPED_SOURCE_CATEGORIES',
                'local_codes':[code],'local_labels':sorted(category_labels[(code,version)]), 'geography_key':'country:MX:national',
                'frequency':row['Frequency'],'measure':row['Measure'],'reporting_basis':row['ReportingBasis'],
                'time_basis':'source cumulative snapshot; accumulation start unverified' if row['PeriodType']=='cumulative' else 'calendar month' if row['Frequency']=='monthly' else 'source-verified Sunday-Saturday table epidemiological week',
                'unit':'count','definition_version':version,'mapping_relation':relation,
                'comparability':mapping['comparability'],'aggregation_policy':'non_additive',
                'projection_policy':policy if target else 'source_observations_only','missing_value_policy':'missing_is_unknown',
                'case_definition':notes,'status':'active','rollup_policy':'no_auto_rollup'}
            ontology['source_series'].append(series)
            ontology['availability'].append({'id':'AV_MX_HF_'+token,'source_id':source_id,
                'target_ref':{'kind':mapping['target_kind'],'id':mapping['target_code']},'series_id':series_id,
                'status':'upstream_available_ingestion_pending','reason_code':'official_originals_captured_offline_organization_verified',
                'missing_value_policy':'missing_is_unknown'})
    ontology['summary']=counts
    ontology['application_note']='Merge this independent patch via the shared ontology owner. Evidence covers only the captured monthly editions and source-verified weekly table periods. Unresolved source cells and label/ICD conflicts remain explicit in each source audit. Availability describes these explicit series, never all discovered archive editions or reports.'
    (ROOT/'configs/high_frequency_mexico_ontology.json').write_text(json.dumps(ontology,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'configs/high_frequency_mexico_reviewed_mappings.json').write_text(json.dumps(mappings,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(counts))


if __name__=='__main__':main()
