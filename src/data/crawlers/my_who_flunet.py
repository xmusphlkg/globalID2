"""Malaysia FluNet laboratory detections, with origin and denominators intact."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from numbers import Real
from urllib.parse import urlencode
from .base import BaseCrawler

DEFAULT_SCOPE = 'who_flunet_weekly'
DEFAULT_SOURCE_NAME = 'Malaysia WHO FluNet Weekly Respiratory Laboratory Surveillance'
ONTOLOGY_SOURCE_ID = 'SRC_MY_WHO_FLUNET'
LANDING_URL = 'https://www.who.int/tools/flunet'
DATA_URL = 'https://xmart-api-public.who.int/FLUMART/VIW_FNT?' + urlencode({'$filter':"COUNTRY_CODE eq 'MYS'",'$format':'json'})
DICTIONARY_URL = 'https://xmart-api-public.who.int/FLUMART/VIW_FLU_METADATA?' + urlencode({'$format':'json','$filter':"contains(DatasetName, 'FluNet')"})
HISTORY_START_YEAR = 1996


def count_value(value):
    if value is None:
        return None
    if isinstance(value,str) and (value in {'*','-','suppressed'} or value.startswith('<') and value[1:].strip().isdigit()):
        return value
    if isinstance(value,bool) or not isinstance(value,Real) or not math.isfinite(value) or value<0 or int(value)!=value:
        raise ValueError(f'Invalid WHO reported count: {value!r}')
    return str(int(value))


def detection_fields(dictionary):
    fields = {}
    for item in dictionary:
        if item.get('DatasetName') != 'FluNet' or item.get('TableName') != 'VIW_FNT':
            continue
        code, label = item['FieldName'], item['Description']
        if label.startswith('Number of ') and 'detections' in label:
            if code in fields:
                raise ValueError('Duplicate FluNet dictionary detection field')
            fields[code] = label
    if not {'INF_ALL','INF_A','INF_B','RSV','HUMAN_CORONA'}.issubset(fields):
        raise ValueError('FluNet dictionary lost reviewed detection field contracts')
    if 'not SARS-CoV-2' not in fields['HUMAN_CORONA']:
        raise ValueError('FluNet human coronavirus definition changed')
    return fields


def parse_records(records, *, fields, as_of=None):
    today = as_of or datetime.now(timezone.utc).date()
    rows, seen, audit = [], set(), {'source_records':len(records),'missing_detection_cells':0,'withheld_open_week_records':0,'source_fields':fields}
    for raw, provenance in records:
        if raw.get('COUNTRY_CODE') != 'MYS' or raw.get('ISO2') != 'MY':
            raise ValueError('WHO FluNet page contains a non-Malaysia observation')
        start = date.fromisoformat(raw['ISO_WEEKSTARTDATE'])
        iso = start.isocalendar()
        if start.weekday()!=0 or (iso.year,iso.week)!=(raw['ISO_YEAR'],raw['ISO_WEEK']) or raw['ISOYW']!=iso.year*100+iso.week:
            raise ValueError('WHO FluNet ISO week/date identity disagrees')
        if start.year < HISTORY_START_YEAR or start > today:
            raise ValueError('Unreviewed/future WHO FluNet date')
        origin = raw['ORIGIN_SOURCE']
        if origin not in {'SENTINEL','NONSENTINEL','NOTDEFINED'}:
            raise ValueError('Unknown WHO FluNet surveillance origin')
        identity = (start,origin)
        if identity in seen:
            raise ValueError('Duplicate WHO FluNet week/origin')
        seen.add(identity)
        end=start+timedelta(days=6)
        if end>=today:
            audit['withheld_open_week_records']+=1
            continue
        for field,label in fields.items():
            if field not in raw:
                raise ValueError(f'FluNet response missing dictionary field: {field}')
            value=count_value(raw[field])
            if value is None:
                audit['missing_detection_cells']+=1
            suppressed=value is not None and not value.isdigit()
            rows.append({
                'Date':start.isoformat(),'Year':str(start.year),'Month':str(start.month),'PeriodStart':start.isoformat(),'PeriodEnd':end.isoformat(),
                'SourceYear':str(iso.year),'SourceWeek':str(iso.week),'Calendar':'ISO-8601','TimeBasis':'source_iso_weekstart',
                'DiseaseCode':'__source_native__','SourceDiseaseCode':field,'RawDiseaseLabel':label,
                'Cases':'' if value is None else value,'SourceReportedValue':'' if raw[field] is None else str(raw[field]),'MissingValueStatus':'source_missing' if value is None else ('source_suppressed' if suppressed else 'reported'),
                'Measure':'laboratory_detections','Unit':'count','Frequency':'weekly','PeriodType':'weekly',
                'ReportingBasis':'who_gisrs_laboratory_surveillance','DefinitionVersion':'my_who_flunet_detections_v1',
                'Geography':'Malaysia reporting laboratory network','GeographyKey':'country:MY:national',
                'SurveillanceOrigin':origin,'PopulationScope':'reporting_laboratory_network_not_population_cases',
                'SourceInfluenzaSpecimensProcessed':raw.get('SPEC_PROCESSED_NB'),'SourceInfluenzaSpecimensReceived':raw.get('SPEC_RECEIVED_NB'),
                'SourceRSVSpecimensProcessed':raw.get('RSV_PROCESSED'),'SourceILIActivity':raw.get('ILI_ACTIVITY'),
                'SourceLabComment':raw.get('LAB_RESULT_COMMENT'),'SourceWeeklyComment':raw.get('WCR_COMMENT'),
                'SourceOtherSubtypeDetails':raw.get('AOTHER_SUBTYPE_DETAILS'),'SourceOtherVirusDetails':raw.get('OTHER_RESPVIRUS_DETAILS'),
                'SourcePreferredInfluenzaSubtype':raw.get('PSOURCE_SUBTYPE_INF'),'SourcePreferredInfluenzaPositivity':raw.get('PSOURCE_PPOS_INF'),'SourcePreferredRSV':raw.get('PSOURCE_RSV'),
                'Source':DEFAULT_SOURCE_NAME,'SourceURL':LANDING_URL,'DatasetStatus':'reported_revisable','IsProvisional':'true',
                'RevisionSemantics':'authoritative_revision','AuthoritativeRevision':'true','PublicReleaseEnabled':'false',
                'AggregationPolicy':'non_additive','Dimensions':json.dumps({'surveillance_origin':origin},sort_keys=True),**provenance,
            })
    if not rows:
        raise ValueError('No reported closed-week Malaysia FluNet detections')
    audit.update({'observations':len(rows),'first_date':min(r['Date'] for r in rows),'latest_date':max(r['Date'] for r in rows),'observed_fields':sorted({r['SourceDiseaseCode'] for r in rows}),'origin_counts':{o:sum(1 for r in rows if r['SurveillanceOrigin']==o) for o in sorted({r['SurveillanceOrigin'] for r in rows})}})
    return rows,audit


class MYWHOFluNetCrawler(BaseCrawler):
    def __init__(self):super().__init__(timeout=90,delay=0.1)
    async def crawl(self,**kwargs):raise NotImplementedError('Use staged WHO artifact capture')
    def parse(self,response):raise NotImplementedError('FluNet needs captured dictionary/page provenance')
