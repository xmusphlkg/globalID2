"""Country-bound WHO FluNet laboratory detections, with origin and denominators intact."""
from datetime import date, datetime, timedelta, timezone
import json
from urllib.parse import urlencode
from .base import BaseCrawler
import pycountry
from .my_who_flunet import count_value, detection_fields

DEFAULT_SCOPE = 'who_flunet_weekly'
LANDING_URL = 'https://www.who.int/tools/flunet'
BASE_URL = 'https://xmart-api-public.who.int/FLUMART/VIW_FNT'
DICTIONARY_URL = 'https://xmart-api-public.who.int/FLUMART/VIW_FLU_METADATA?' + urlencode({'$format':'json','$filter':"contains(DatasetName, 'FluNet')"})
HISTORY_START_YEAR = 1900


def country_contract(country_code):
    code = str(country_code).strip().upper()
    country = pycountry.countries.get(alpha_2=code) if len(code) == 2 else None
    if country is None:
        raise ValueError(f'WHO FluNet requires an ISO2 country/territory: {country_code!r}')
    return code, {'iso3': country.alpha_3, 'name': country.name,
                  'source_id': f'SRC_{code}_WHO_FLUNET',
                  'source_name': f'{country.name} WHO FluNet Weekly Respiratory Laboratory Surveillance'}


def data_url(country_code):
    _, contract = country_contract(country_code)
    return BASE_URL + '?' + urlencode({'$filter': f"COUNTRY_CODE eq '{contract['iso3']}'", '$format': 'json'})


def parse_records(records, *, country_code, fields, as_of=None):
    code, contract = country_contract(country_code)
    today = as_of or datetime.now(timezone.utc).date()
    rows, seen, audit = [], set(), {'source_records':len(records),'missing_detection_cells':0,'withheld_open_week_records':0,'source_fields':fields}
    for raw, provenance in records:
        if raw.get('COUNTRY_CODE') != contract['iso3'] or raw.get('ISO2') != code:
            raise ValueError(f'WHO FluNet page contains an observation outside {code}')
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
                'Measure':'laboratory_detections','Unit':'count','Frequency':'weekly','PeriodType':'weekly','ObservationKind':'interval_count',
                'ReportingBasis':'who_gisrs_laboratory_surveillance','DefinitionVersion':'my_who_flunet_detections_v1' if code == 'MY' else 'who_flunet_detections_v1',
                'Geography':contract['name'] + ' reporting laboratory network','GeographyKey':f'country:{code}:national',
                'SurveillanceOrigin':origin,'PopulationScope':'reporting_laboratory_network_not_population_cases',
                'SourceInfluenzaSpecimensProcessed':raw.get('SPEC_PROCESSED_NB'),'SourceInfluenzaSpecimensReceived':raw.get('SPEC_RECEIVED_NB'),
                'SourceRSVSpecimensProcessed':raw.get('RSV_PROCESSED'),'SourceILIActivity':raw.get('ILI_ACTIVITY'),
                'SourceLabComment':raw.get('LAB_RESULT_COMMENT'),'SourceWeeklyComment':raw.get('WCR_COMMENT'),
                'SourceOtherSubtypeDetails':raw.get('AOTHER_SUBTYPE_DETAILS'),'SourceOtherVirusDetails':raw.get('OTHER_RESPVIRUS_DETAILS'),
                'SourcePreferredInfluenzaSubtype':raw.get('PSOURCE_SUBTYPE_INF'),'SourcePreferredInfluenzaPositivity':raw.get('PSOURCE_PPOS_INF'),'SourcePreferredRSV':raw.get('PSOURCE_RSV'),
                'Source':contract['source_name'],'SourceURL':LANDING_URL,'DatasetStatus':'reported_revisable','IsProvisional':'true',
                'RevisionSemantics':'authoritative_revision','AuthoritativeRevision':'true','PublicReleaseEnabled':'true',
                'AggregationPolicy':'non_additive','Dimensions':json.dumps({'surveillance_origin':origin},sort_keys=True),**provenance,
            })
    if not rows:
        raise ValueError(f'No reported closed-week {code} FluNet detections')
    audit.update({'observations':len(rows),'first_date':min(r['Date'] for r in rows),'latest_date':max(r['Date'] for r in rows),'observed_fields':sorted({r['SourceDiseaseCode'] for r in rows}),'origin_counts':{o:sum(1 for r in rows if r['SurveillanceOrigin']==o) for o in sorted({r['SurveillanceOrigin'] for r in rows})}})
    return rows,audit


class WHOFluNetCrawler(BaseCrawler):
    def __init__(self):super().__init__(timeout=90,delay=0.1)
    async def crawl(self,**kwargs):raise NotImplementedError('Use staged WHO artifact capture')
    def parse(self,response):raise NotImplementedError('FluNet needs captured dictionary/page provenance')
