"""DGE high-frequency source rows; the shared source-series store owns writes."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .mx import MexicoDGEAnnualUpdater, MexicoDGEFetchResult
from src.data.crawlers.mx_high_frequency import (
    HISTORY_START_YEAR, MONTHLY_SCOPE, SOURCE_IDS, WEEKLY_SCOPE,
    MexicoDGEHighFrequencyCrawler, deduplicate_rows, normalize_scope,
)


class MexicoDGEHighFrequencyUpdater(MexicoDGEAnnualUpdater):
    country_code = "MX"
    series_registered_rows_only = True
    series_registry_coverage = "required"
    public_release_enabled = True
    mapping_notifications_enabled = False
    license_review_status = "operator_authorized_attributed_source_release"

    def __init__(self, *, source=MONTHLY_SCOPE, country_code="MX", output_csv=None,
                 full_history_start_year=HISTORY_START_YEAR, refresh_recent_months=3,
                 crawler_type=None):
        if str(country_code).upper() != "MX":
            raise ValueError("DGE high-frequency updater is Mexico-specific")
        self.source_scope = normalize_scope(source)
        self.ontology_source_id = SOURCE_IDS[self.source_scope]
        self.default_output_csv = Path(__file__).resolve().parents[3] / "data/current/mx" / f"{self.source_scope}.csv"
        super().__init__(output_csv=output_csv,full_history_start_year=full_history_start_year,
                         refresh_recent_months=refresh_recent_months,
                         crawler_type=crawler_type or MexicoDGEHighFrequencyCrawler)

    @staticmethod
    def normalize_source(source):
        return normalize_scope(source)

    def prepare_rows(self, rows, *, source=None, start_year=None, end_year=None,
                     months=None, source_audit=None, **options):
        scope=normalize_scope(source or self.source_scope)
        if scope!=self.source_scope:
            raise ValueError("DGE updater source identity cannot change within a run")
        first=int(start_year) if start_year is not None else self.full_history_start_year
        chosen=[r for r in rows if int(r["Year"])>=first and (end_year is None or int(r["Year"])<=int(end_year))]
        chosen,conflicts=deduplicate_rows(chosen)
        if not chosen:raise ValueError("No supported DGE high-frequency source observations")
        audit=dict(source_audit or {})
        if conflicts:
            audit.setdefault("failures",[]).extend(conflicts)
            audit["coverage_complete"]=False
        latest=max(date.fromisoformat(r["Date"]) for r in chosen)
        return MexicoDGEFetchResult(chosen,latest,self.output_csv,
            [f"[source] MX {scope}: {len(chosen)} source-native observations; latest {latest}",
             "[contract] National totals only; monthly periods and each weekly table cutoff preserved; cumulative measures never treated as incident cases",
             "[release] Operator-authorized source series; standard-disease projection requires separately reviewed mappings"],audit)

    def refresh_source(self, *, source=None, start_year=None, end_year=None, force=False, **options):
        scope=normalize_scope(source or self.source_scope)
        crawler=self.crawler_type(source=scope)
        try:
            rows=crawler.fetch_rows(start_year=start_year,end_year=end_year,force=force,
                **{k:v for k,v in options.items() if k in {"weekly_index_url","latest_reports"}})
            return self.prepare_rows(rows,source=scope,start_year=start_year,end_year=end_year,source_audit=crawler.last_audit)
        finally:crawler.close()
