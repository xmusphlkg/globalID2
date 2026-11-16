"""Malaysia country-reported WHO GHO counts; no modelled incidence or rates."""
from datetime import datetime,timezone
import json
import re
from urllib.parse import urlencode
from .my_who_flunet import count_value
from .base import BaseCrawler

DEFAULT_SCOPE='who_gho_annual'
DEFAULT_SOURCE_NAME='Malaysia WHO GHO Annual Reported Infections'
ONTOLOGY_SOURCE_ID='SRC_MY_WHO_GHO'
LANDING_URL='https://www.who.int/data/gho'
BASE_URL='https://ghoapi.azureedge.net/api/'
HISTORY_START_YEAR=1954
# Each WHO indicator is a distinct, non-additive source series. Neonatal/total
# tetanus and case/death indicators must never merge into a disease total.
INDICATORS={
 'CHOLERA_0000000001':('Number of reported cases of cholera','case_notifications'),
 'CHOLERA_0000000002':('Number of reported deaths from cholera','death_notifications'),
 'WHS3_41':('Diphtheria - number of reported cases','case_notifications'),
 'WHS3_42':('Japanese encephalitis - number of reported cases','case_notifications'),
 'WHS3_43':('Pertussis - number of reported cases','case_notifications'),
 'WHS3_45':('Number of new leprosy cases','case_notifications'),
 'WHS3_46':('Total tetanus - number of reported cases','case_notifications'),
 'WHS3_47':('Meningitis - number of reported cases','case_notifications'),
 'WHS3_49':('Poliomyelitis - number of reported cases','case_notifications'),
 'WHS3_51':('H5N1 influenza - number of reported cases','case_notifications'),
 'WHS3_53':('Mumps - number of reported cases','case_notifications'),
 'WHS3_55':('Congenital Rubella Syndrome - number of reported cases','case_notifications'),
 'WHS3_56':('Neonatal tetanus - number of reported cases','case_notifications'),
 'WHS3_57':('Rubella - number of reported cases','case_notifications'),
 'WHS3_62':('Measles - number of reported cases','case_notifications'),
 'TB_c_newinc':('Tuberculosis - new and relapse cases','case_notifications'),
 'MALARIA_CONF_CASES':('Number of confirmed malaria cases','confirmed_case_notifications'),
 'MALARIA001':('Malaria - number of reported deaths','death_notifications'),
}


def data_url(code):
    if code not in INDICATORS:raise ValueError('Unreviewed WHO GHO indicator')
    return BASE_URL+code+'?'+urlencode({'$filter':"SpatialDim eq 'MYS'"})


METADATA_URL=BASE_URL+'Indicator?'+urlencode({'$filter':"startswith(IndicatorCode,'WHS3_') or startswith(IndicatorCode,'CHOLERA_') or IndicatorCode eq 'TB_c_newinc' or IndicatorCode eq 'MALARIA_CONF_CASES' or IndicatorCode eq 'MALARIA001'"})


def validate_indicators(metadata):
    found={}
    for row in metadata:
        code=row['IndicatorCode']
        if code not in INDICATORS:continue
        if code in found or row['IndicatorName']!=INDICATORS[code][0]:
            raise ValueError('WHO GHO indicator name/code contract changed')
        found[code]=row['IndicatorName']
    if set(found)!=set(INDICATORS):raise ValueError('WHO GHO indicator metadata is incomplete')
    return found


def parse_records(records,*,metadata,as_of=None):
    as_of=as_of or datetime.now(timezone.utc).date()
    validate_indicators(metadata)
    seen,rows=set(),[]
    for code,raw,provenance in records:
        if raw.get('IndicatorCode')!=code or raw.get('SpatialDim')!='MYS' or raw.get('SpatialDimType')!='COUNTRY' or raw.get('TimeDimType')!='YEAR':
            raise ValueError('WHO GHO indicator/country/annual contract disagrees')
        if any(raw.get(k) is not None for k in ['Dim1','Dim2','Dim3','Dim1Type','Dim2Type','Dim3Type']):
            raise ValueError('Unreviewed WHO GHO stratification; cannot treat as national total')
        year=raw['TimeDim']
        if isinstance(year,bool) or not isinstance(year,int) or not HISTORY_START_YEAR<=year<as_of.year:
            raise ValueError('WHO GHO year must be a reviewed closed calendar year')
        identity=(code,year)
        if identity in seen:raise ValueError('Duplicate WHO GHO indicator/year')
        seen.add(identity)
        if raw.get('Low') is not None or raw.get('High') is not None:
            raise ValueError('WHO GHO estimate/interval cannot become a reported count')
        value=count_value(raw.get('NumericValue'))
        status='source_suppressed' if value is not None and not value.isdigit() else 'reported'
        if value is None:
            source_text=str(raw.get('Value') or '').strip()
            if re.fullmatch(r'\d+|\d{1,3}(?:[ ,]\d{3})+',source_text):
                value=str(int(source_text.replace(' ','').replace(',','')))
            elif source_text.startswith('<') and source_text[1:].strip().isdigit() or source_text in {'*','-','suppressed'}:
                value=source_text;status='source_suppressed'
            elif source_text.casefold() in {'','none','no data','not available','null','na','n/a'}:
                value='';status='source_missing'
            else:raise ValueError('WHO GHO nonnumeric value needs explicit missing/suppression review')
        label,measure=INDICATORS[code]
        rows.append({
          'Date':f'{year}-01-01','Year':str(year),'Month':'1','PeriodStart':f'{year}-01-01','PeriodEnd':f'{year}-12-31',
          'DiseaseCode':'__source_native__','SourceDiseaseCode':code,'RawDiseaseLabel':label,'Cases':value,
          'SourceReportedValue':raw.get('Value'),'MissingValueStatus':status,'SourceRecordId':raw['Id'],
          'Frequency':'annual','PeriodType':'annual','Measure':measure,'Unit':'count','ReportingBasis':'who_country_reported_notifications',
          'DefinitionVersion':'my_who_gho_country_reported_annual_v1','Geography':'Malaysia','GeographyKey':'country:MY:national',
          'Source':DEFAULT_SOURCE_NAME,'SourceURL':LANDING_URL,'SourcePublishedAt':raw.get('Date'),'SourceDataSourceDim':raw.get('DataSourceDim'),
          'SourceTimeDimensionBegin':raw.get('TimeDimensionBegin'),'SourceTimeDimensionEnd':raw.get('TimeDimensionEnd'),'SourceComments':raw.get('Comments'),
          'DatasetStatus':'closed_revisable','IsProvisional':'false','RevisionSemantics':'authoritative_revision','AuthoritativeRevision':'true',
          'PublicReleaseEnabled':'false','AggregationPolicy':'non_additive','Dimensions':json.dumps({'who_indicator':code},sort_keys=True),**provenance,
        })
    if not rows:raise ValueError('No Malaysia WHO reported annual records')
    return rows


class MYWHOAnnualCrawler(BaseCrawler):
    def __init__(self):super().__init__(timeout=90,delay=0.1)
    async def crawl(self,**kwargs):raise NotImplementedError('Use staged WHO artifact capture')
    def parse(self,response):raise NotImplementedError('GHO needs captured indicator and page provenance')
