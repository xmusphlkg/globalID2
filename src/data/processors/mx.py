"""Mexico DGE source-native annual holding rows; no legacy disease projection."""
from __future__ import annotations
import csv
from dataclasses import dataclass,field
from datetime import date,datetime
import io
from pathlib import Path
from sqlalchemy import text
from src.data.crawlers.mx import DEFAULT_SCOPE,ONTOLOGY_SOURCE_ID,HISTORY_START_YEAR,MexicoDGEAnnualCrawler

ROOT=Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_CSV=ROOT/'data/current/mx/mexico_dge_annual.csv'

@dataclass(frozen=True)
class MexicoDGEFetchResult:
    rows:list[dict[str,str]]
    source_latest_date:date|None
    source_csv:Path
    script_logs:list[str]
    source_audit:dict=field(default_factory=dict)

@dataclass(frozen=True)
class MexicoDGEImportResult:
    inserted_or_updated:int
    skipped_unmapped:int
    db_latest_date:date|None
    source_latest_date:date|None
    imported_new_data:bool

class MexicoDGEAnnualUpdater:
    country_code='MX'
    source_scope=DEFAULT_SCOPE
    ontology_source_id=ONTOLOGY_SOURCE_ID
    series_geography_key='country:MX:national'
    series_registered_rows_only=True
    series_registry_coverage='required'
    public_release_enabled=False
    mapping_notifications_enabled=False
    license_review_status='official_source_reuse_review_pending'
    default_output_csv=DEFAULT_OUTPUT_CSV
    source_aliases={'all','mx','mexico',DEFAULT_SCOPE}

    def __init__(self,*,output_csv=None,full_history_start_year=HISTORY_START_YEAR,refresh_recent_months=3,crawler_type=None):
        self.output_csv=Path(output_csv or self.default_output_csv)
        self.full_history_start_year=int(full_history_start_year)
        self.refresh_recent_months=int(refresh_recent_months)
        self.crawler_type=crawler_type or MexicoDGEAnnualCrawler

    @classmethod
    def normalize_source(cls,source):
        if str(source or 'all').strip().casefold() not in cls.source_aliases:raise ValueError(f'Unsupported MX source: {source!r}')

    @staticmethod
    def parse_source_csv(payload,**kwargs):return list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))

    def prepare_rows(self,rows,*,source=DEFAULT_SCOPE,start_year=None,end_year=None,months=None,source_audit=None,**options):
        self.normalize_source(source)
        start_year=self.full_history_start_year if start_year is None else int(start_year)
        selected=[r for r in rows if int(r['Year'])>=start_year and (end_year is None or int(r['Year'])<=int(end_year))]
        latest=max((date.fromisoformat(r['Date']) for r in selected),default=None)
        return MexicoDGEFetchResult(selected,latest,self.output_csv,[f'[source] MX DGE national annual source cells: {len(selected)}; latest {latest}', '[coverage] All published source diseases retained; age/sex strata and rates remain independent, never summed', '[release] Private holding observations; unresolved source tables are recorded in source_audit.json'],source_audit or {})

    def refresh_source(self,*,source=DEFAULT_SCOPE,start_year=None,end_year=None,**kwargs):
        self.normalize_source(source);crawler=self.crawler_type()
        try:return self.prepare_rows(crawler.fetch_rows(start_year=start_year or self.full_history_start_year,end_year=end_year),source=source,start_year=start_year,end_year=end_year)
        finally:crawler.session.close()

    async def get_db_latest_date(self,db):
        value=(await db.execute(text('SELECT MAX(obs.time) FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:country AND series.source_system=:source'),{'country':'MX','source':self.ontology_source_id})).scalar()
        return value.date() if isinstance(value,datetime) else value

    async def get_db_months(self,db):
        rows=(await db.execute(text('SELECT DISTINCT EXTRACT(YEAR FROM obs.time)::int, EXTRACT(MONTH FROM obs.time)::int FROM disease_series_observations obs JOIN disease_surveillance_series series ON series.series_code=obs.series_code WHERE series.country_code=:country AND series.source_system=:source'),{'country':'MX','source':self.ontology_source_id})).fetchall()
        return {(int(y),int(m)) for y,m in rows}

    async def import_rows(self,db,rows,*,db_latest_date=None,source_latest_date=None,force=False):return MexicoDGEImportResult(0,0,db_latest_date,source_latest_date,False)
