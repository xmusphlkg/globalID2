"""
Crawl Service

Encapsulates all business logic for data crawling + processing,
decoupling it from the CLI layer in main.py.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import get_database, get_logger
from src.core.country_library import get_country_bootstrap_config
from src.core.country_library import SUBDIVISION_REGISTRY
from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRY_CODES
from src.core.south_asia_sources import SOUTH_ASIA_SOURCES, SOUTH_ASIA_ALL_SOURCES, south_asia_source
from src.core.remaining_country_sources import WHO_COUNTRIES, SPF_TERRITORIES, EXTRA_SOURCE_CONTRACTS, source_contract
from src.core.disease_mutation_lock import acquire_country_data_mutation_lock
from src.core.task_manager import task_manager
from src.data.crawlers.au import AU_STATE_SUBDIVISIONS
from src.data.storage import (
    SeriesObservationQualityError,
    SeriesObservationQualityPolicy,
    SeriesObservationSaveResult,
    SeriesObservationStore,
)
from src.domain import CrawlRun, Task
from src.services.ingestion import series_rows as _series_rows

logger = get_logger(__name__)


@dataclass
class CrawlResult:
    new_reports: int
    processed_reports: int
    total_records: int
    crawl_run_id: Optional[int]


@dataclass(frozen=True)
class _PipelineSpec:
    handler_name: str
    updater_name: Optional[str] = None


class CrawlService:
    """Orchestrates the three-phase crawl → parse → store pipeline."""

    _PIPELINES: Dict[str, _PipelineSpec] = {
        'CL': _PipelineSpec('_execute_cl_surveillance', 'ChileENOHistoricalUpdater'),
        'CO': _PipelineSpec('_execute_co_surveillance', 'ColombiaINSSivigilaUpdater'),
        'MW': _PipelineSpec('_execute_mw_surveillance', 'MalawiPHIMIDSRUpdater'),
        'ZA': _PipelineSpec('_execute_za_surveillance', 'SouthAfricaNICDNMCUpdater'),
        'ID': _PipelineSpec('_execute_id_surveillance', 'IndonesiaKemkesProfileUpdater'),
        'MX': _PipelineSpec('_execute_mx_surveillance', 'MexicoDGEAnnualUpdater'),
        'NG': _PipelineSpec("_execute_ng_surveillance", 'NigeriaNCDCUpdater'),
        'KH': _PipelineSpec("_execute_kh_surveillance", 'CambodiaCamEWARNWeeklyUpdater'),
        'AR': _PipelineSpec("_execute_ar_surveillance", 'ArgentinaBENUpdater'),
        'PE': _PipelineSpec("_execute_pe_surveillance", 'PeruCDCWeeklyUpdater'),
        "TH": _PipelineSpec("_execute_th_dds_weekly", "ThailandDDSWeeklyUpdater"),
        "MY": _PipelineSpec("_execute_my_annual", "MYAnnualUpdater"),
        "NL": _PipelineSpec("_execute_nl_annual", "NLNotifiableAnnualUpdater"),
        "CN": _PipelineSpec("_execute_cn_cdc"),
        "US": _PipelineSpec("_execute_us_weekly", "USWeeklyUpdater"),
        "JP": _PipelineSpec("_execute_jp_weekly", "JPWeeklyUpdater"),
        "AU": _PipelineSpec("_execute_au_monthly", "AUMonthlyUpdater"),
        "CA": _PipelineSpec("_execute_ca_national_annual", "CanadaCNDSSAnnualUpdater"),
        "NZ": _PipelineSpec("_execute_nz_monthly", "NZMonthlyUpdater"),
        "TW": _PipelineSpec("_execute_tw_monthly", "TWMonthlyUpdater"),
        "HK": _PipelineSpec("_execute_hk_monthly", "HKMonthlyUpdater"),
        "FI": _PipelineSpec("_execute_fi_monthly", "FIMonthlyUpdater"),
        "IE": _PipelineSpec("_execute_ie_weekly", "IEWeeklyUpdater"),
        "NO": _PipelineSpec("_execute_no_monthly", "NOMonthlyUpdater"),
        "SE": _PipelineSpec("_execute_se_monthly", "SEMonthlyUpdater"),
        "AT": _PipelineSpec("_execute_at_monthly", "ATMonthlyUpdater"),
        "DE": _PipelineSpec("_execute_de_weekly", "DEWeeklyUpdater"),
        "SG": _PipelineSpec("_execute_sg_weekly", "SGWeeklyUpdater"),
        "FR": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "ES": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "IT": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "PT": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "PL": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "CZ": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "GR": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "RO": _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        "CA-ON": _PipelineSpec(
            "_execute_ca_on_monthly", "CAOntarioMonthlyUpdater"
        ),
        "BR": _PipelineSpec("_execute_br_monthly", "BRMonthlyUpdater"),
        "KR": _PipelineSpec("_execute_kr_monthly", "KRMonthlyUpdater"),
        "CH": _PipelineSpec("_execute_ch_monthly", "CHMonthlyUpdater"),
        "IS": _PipelineSpec("_execute_is_mixed", "ISMultiFrequencyUpdater"),
    }
    for _code, _spec in SOUTH_ASIA_SOURCES.items():
        _PIPELINES[_code] = _PipelineSpec("_execute_south_asia", _spec["updater"])
    for _code in WHO_COUNTRIES:
        _PIPELINES[_code] = _PipelineSpec("_execute_who_country_annual", "WHOAnnualUpdater")
    for _code in SPF_TERRITORIES:
        _PIPELINES[_code] = _PipelineSpec("_execute_spf_overseas_annual", "SPFOverseasUpdater")
    for _ecdc_country_code in ECDC_BASELINE_COUNTRY_CODES:
        _PIPELINES.setdefault(
            _ecdc_country_code,
            _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater"),
        )
    for _au_subdivision_code in AU_STATE_SUBDIVISIONS:
        _PIPELINES.setdefault(
            _au_subdivision_code,
            _PipelineSpec("_execute_au_monthly", "AUMonthlyUpdater"),
        )
    for _subdivision_code, _subdivision in SUBDIVISION_REGISTRY.items():
        _parent_code = str(_subdivision.get("parent_country_code") or "").upper()
        if _parent_code not in {"JP", "SE", "DE", "US", "CH", "NZ", "BR"}:
            continue
        _parent_pipeline = _PIPELINES.get(_parent_code)
        if _parent_pipeline is not None:
            _PIPELINES[_subdivision_code] = (
                _PipelineSpec("_execute_nz_dhb_annual", "NZDHBAnnualUpdater")
                if _parent_code == "NZ"
                else _parent_pipeline
            )
    from src.data.crawlers.cn_province_adapters import province_adapter_registry as _province_registry
    for _province_code in _province_registry():
        _PIPELINES[_province_code] = _PipelineSpec("_execute_cn_province", "CNProvinceUpdater")
    from src.data.processors.br_subdivisions import STATE_CODES as _br_states
    for _state in _br_states.values():
        _PIPELINES[f"BR-{_state}"] = _PipelineSpec("_execute_br_monthly", "BRMonthlyUpdater")
    _SERIES_SOURCE_IDS: Dict[str, str | Dict[str, str]] = {
        'CL': 'SRC_CL_ENO',
        'CO': 'SRC_CO_INS_SIVIGILA',
        'MW': 'SRC_MW_PHIM_IDSR',
        'ZA': 'SRC_ZA_NICD_NMC',
        'ID': 'SRC_ID_KEMKES_PROFILE',
        'MX': 'SRC_MX_DGE_ANNUAL',
        'NG': 'SRC_NG_NCDC',
        'KH': 'SRC_KH_CAMEWARN',
        'AR': 'SRC_AR_BEN',
        'PE': 'SRC_PE_CDC',
        "TH": "SRC_TH_DOE506",
        "MY": "SRC_MY_MOH_STD",
        "NL": "SRC_NL_RIVM_NOTIFIABLE",
        "US": {
            "US CDC NNDSS": "SRC_US_NNDSS",
            "US CDC NHSS": "SRC_US_NHSS",
        },
        "JP": "SRC_JP_NIID",
        "AU": "SRC_AU_NINDSS",
        "CA": "SRC_CA_PHAC_CNDSS",
        "NZ": "SRC_NZ_PHS",
        "TW": "SRC_TW_NIDSS",
        "HK": "SRC_HK_CHP",
        "FI": "SRC_FI_THL_TTR",
        "IE": "SRC_IE_HPSC_NDH",
        "NO": "SRC_NO_FHI_MSIS",
        "SE": "SRC_SE_FOHM_SMINET",
        "AT": "SRC_AT_AGES_RADAR",
        "DE": "SRC_DE_RKI_SURVSTAT",
        "SG": {
            "Singapore data.gov.sg Weekly Infectious Diseases Bulletin (2012-2022)": "SRC_SG_DATA_GOV_WIDB",
            "Singapore CDA Weekly Infectious Diseases Bulletin": "SRC_SG_CDA_WIDB",
        },
        "FR": "SRC_FR_ECDC_ATLAS",
        "ES": "SRC_ES_ECDC_ATLAS",
        "IT": "SRC_IT_ECDC_ATLAS",
        "PT": "SRC_PT_ECDC_ATLAS",
        "PL": "SRC_PL_ECDC_ATLAS",
        "CZ": "SRC_CZ_ECDC_ATLAS",
        "GR": "SRC_GR_ECDC_ATLAS",
        "RO": "SRC_RO_ECDC_ATLAS",
        "KR": "SRC_KR_KDCA",
        "BR": "SRC_BR_SINAN",
        "CH": "SRC_CH_FOPH_IDD",
        "CA-ON": "SRC_CA_ON_PHO_IDTO",
        "IS": {
            "Iceland Directorate of Health Annual Dashboard": "SRC_IS_DOH_ANNUAL",
            "Iceland Directorate of Health STI Dashboard": "SRC_IS_DOH_STI",
            "Iceland Directorate of Health Respiratory Dashboard": "SRC_IS_DOH_RESPIRATORY",
            "Iceland Directorate of Health historical registry annual": "SRC_IS_DOH_HISTORY",
            "Iceland Directorate of Health disease-specific monthly workbooks": "SRC_IS_DOH_HISTORY",
            "Iceland Directorate of Health legacy ICD monthly reports": "SRC_IS_DOH_LEGACY_ICD",
            "Iceland Directorate of Health Historical Registry": "SRC_IS_DOH_HISTORY",
            "Iceland Directorate of Health Legacy ICD Monthly": "SRC_IS_DOH_LEGACY_ICD",
        },
    }
    for _ecdc_country_code in ECDC_BASELINE_COUNTRY_CODES:
        _SERIES_SOURCE_IDS.setdefault(
            _ecdc_country_code,
            f"SRC_{_ecdc_country_code}_ECDC_ATLAS",
        )

    for _code in (*WHO_COUNTRIES, *SPF_TERRITORIES):
        _SERIES_SOURCE_IDS[_code] = source_contract(_code)["source_id"]

    for _code, _spec in SOUTH_ASIA_SOURCES.items():
        _SERIES_SOURCE_IDS[_code] = _spec["source_id"]

    @classmethod
    def supported_country_codes(cls) -> List[str]:
        """Return country codes with implemented crawl pipelines."""
        return list(cls._PIPELINES.keys())

    @classmethod
    def supported_country_text(cls) -> str:
        """Human-readable supported country list for validation errors."""
        return ", ".join(cls.supported_country_codes())

    @staticmethod
    def _make_updater(updater_name: str, country_code: Optional[str] = None):
        for spec in SOUTH_ASIA_ALL_SOURCES.values():
            if updater_name == spec["updater"]:
                from importlib import import_module
                return getattr(import_module(f"src.data.processors.{spec['module']}"), updater_name)()
        if updater_name.startswith("high_frequency:"):
            from src.core.high_frequency_sources import make_updater
            return make_updater(str(country_code), updater_name.split(":", 1)[1])
        if updater_name == "CostaRicaNotificationsUpdater":
            from src.data.processors.cr_notifications import CostaRicaNotificationsUpdater
            return CostaRicaNotificationsUpdater()
        if updater_name == "WPRODengueUpdater":
            from src.data.processors.wpro_dengue import WPRODengueUpdater
            return WPRODengueUpdater(country_code)
        if updater_name == "WHOAnnualUpdater":
            from src.data.processors.who_annual import WHOAnnualUpdater
            return WHOAnnualUpdater(country_code)
        if updater_name == "SPFOverseasUpdater":
            from src.data.processors.spf_overseas import SPFOverseasUpdater
            return SPFOverseasUpdater(country_code)
        if updater_name in {"MYWHOFluNetUpdater", "MYWHOAnnualUpdater"}:
            from importlib import import_module
            module = "my_who_flunet" if updater_name == "MYWHOFluNetUpdater" else "my_who_annual"
            return getattr(import_module(f"src.data.processors.{module}"), updater_name)()
        if updater_name == "MalaysiaIDengueDailyUpdater":
            from src.data.processors.my_dengue import MalaysiaIDengueDailyUpdater
            return MalaysiaIDengueDailyUpdater()
        if updater_name in {"NLVirologicalWeeklyUpdater", "NLRespiratoryWeeklyUpdater"}:
            from importlib import import_module
            return getattr(import_module("src.data.processors.nl_weekly"), updater_name)()
        if updater_name == "ThailandDDSTableauUpdater":
            from src.data.processors.th_tableau import ThailandDDSTableauUpdater
            return ThailandDDSTableauUpdater()
        if updater_name == "ThailandIDTHHistoryUpdater":
            from src.data.processors.th_id_th import ThailandIDTHHistoryUpdater
            return ThailandIDTHHistoryUpdater()
        if updater_name == "ThailandDDSWeeklyUpdater":
            from src.data.processors.th_dds import ThailandDDSWeeklyUpdater
            return ThailandDDSWeeklyUpdater()
        priority_modules = {'SouthAfricaNICDNMCUpdater': 'za', 'IndonesiaKemkesProfileUpdater': 'id', 'MexicoDGEAnnualUpdater': 'mx', 'NigeriaNCDCUpdater': 'ng', 'CambodiaCamEWARNWeeklyUpdater': 'kh', 'ArgentinaBENUpdater': 'ar', 'ArgentinaHIVAnnualUpdater': 'ar_hiv', 'PeruCDCWeeklyUpdater': 'pe', 'ChileENOHistoricalUpdater': 'cl', 'ColombiaINSSivigilaUpdater': 'co', 'ColombiaINSSivigilaCurrentUpdater': 'co_current', 'ColombiaINSBESUpdater': 'co_bes', 'ColombiaINSMicrodataUpdater': 'co_microdatos', 'MalawiPHIMIDSRUpdater': 'mw'}
        if updater_name in priority_modules:
            from importlib import import_module
            return getattr(import_module(f"src.data.processors.{priority_modules[updater_name]}"), updater_name)()
        if updater_name == "ThailandDOE506MonthlyUpdater":
            from src.data.processors.th import ThailandDOE506MonthlyUpdater
            return ThailandDOE506MonthlyUpdater()
        if updater_name in {"MYAnnualUpdater", "NLNotifiableAnnualUpdater"}:
            from importlib import import_module
            module = "my_notifiable" if updater_name == "MYAnnualUpdater" else "nl_notifiable"
            return getattr(import_module(f"src.data.processors.{module}"), updater_name)()
        if updater_name == "CNProvinceUpdater":
            from src.data.processors.cn_provinces import CNProvinceUpdater
            return CNProvinceUpdater(country_code=str(country_code).upper())
        from src.data.processors import (
            AUMonthlyUpdater,
            ATMonthlyUpdater,
            BRMonthlyUpdater,
            CAOntarioMonthlyUpdater,
            CanadaCNDSSAnnualUpdater,
            CHMonthlyUpdater,
            FIMonthlyUpdater,
            HKMonthlyUpdater,
            IEWeeklyUpdater,
            ISMultiFrequencyUpdater,
            JPWeeklyUpdater,
            KRMonthlyUpdater,
            NOMonthlyUpdater,
            NZDHBAnnualUpdater,
            NZMonthlyUpdater,
            SEMonthlyUpdater,
            DEWeeklyUpdater,
            SGWeeklyUpdater,
            ECDCAnnualUpdater,
            TWMonthlyUpdater,
            USWeeklyUpdater,
        )

        updaters = {
            "AUMonthlyUpdater": AUMonthlyUpdater,
            "ATMonthlyUpdater": ATMonthlyUpdater,
            "BRMonthlyUpdater": BRMonthlyUpdater,
            "CAOntarioMonthlyUpdater": CAOntarioMonthlyUpdater,
            "CanadaCNDSSAnnualUpdater": CanadaCNDSSAnnualUpdater,
            "CHMonthlyUpdater": CHMonthlyUpdater,
            "FIMonthlyUpdater": FIMonthlyUpdater,
            "HKMonthlyUpdater": HKMonthlyUpdater,
            "IEWeeklyUpdater": IEWeeklyUpdater,
            "ISMultiFrequencyUpdater": ISMultiFrequencyUpdater,
            "JPWeeklyUpdater": JPWeeklyUpdater,
            "KRMonthlyUpdater": KRMonthlyUpdater,
            "NOMonthlyUpdater": NOMonthlyUpdater,
            "NZDHBAnnualUpdater": NZDHBAnnualUpdater,
            "NZMonthlyUpdater": NZMonthlyUpdater,
            "SEMonthlyUpdater": SEMonthlyUpdater,
            "DEWeeklyUpdater": DEWeeklyUpdater,
            "SGWeeklyUpdater": SGWeeklyUpdater,
            "ECDCAnnualUpdater": ECDCAnnualUpdater,
            "TWMonthlyUpdater": TWMonthlyUpdater,
            "USWeeklyUpdater": USWeeklyUpdater,
        }
        updater_type = updaters[updater_name]
        if updater_name == "ECDCAnnualUpdater":
            return updater_type(country_code or "FR")
        if updater_name == "AUMonthlyUpdater" and country_code:
            return updater_type(country_code=country_code)
        if updater_name == "BRMonthlyUpdater" and str(country_code or "").startswith("BR-"):
            from pathlib import Path
            return updater_type(country_code=country_code, output_csv=Path("data/processed/br/subdivisions") / f"{country_code}.csv")
        subdivision = SUBDIVISION_REGISTRY.get(str(country_code or "").upper())
        if subdivision and updater_name in {
            "JPWeeklyUpdater", "SEMonthlyUpdater", "DEWeeklyUpdater",
            "USWeeklyUpdater", "CHMonthlyUpdater", "NZMonthlyUpdater",
            "NZDHBAnnualUpdater", "BRMonthlyUpdater",
        }:
            return updater_type(country_code=str(country_code).upper())
        return updater_type()

    @staticmethod
    async def _save_series_rows(db, updater, rows, *, series_rows=None,
                               _bounded_batch=False, _jp_holding_profiles=None,
                               authoritative_window_start=None):
        return await _series_rows.save_series_rows(db, updater, rows, series_rows=series_rows,
            _bounded_batch=_bounded_batch, _jp_holding_profiles=_jp_holding_profiles,
            authoritative_window_start=authoritative_window_start,
            store_type=SeriesObservationStore, save_batch=CrawlService._save_series_rows,
            quality_policy_for=CrawlService._series_quality_policy,
            source_ids=CrawlService._SERIES_SOURCE_IDS,
            mutation_lock=acquire_country_data_mutation_lock, logger=logger)

    @staticmethod
    async def _import_rows_with_series(
        db,
        updater,
        rows,
        *,
        series_rows=None,
        db_latest_date,
        source_latest_date,
        force: bool,
        publication_results: list | None = None,
        authoritative_window_start=None,
    ):
        """Persist legacy projections and lossless source-series facts atomically."""

        # The country partition covers the complete dual-write transaction;
        # maintenance is excluded by its shared outer gate.
        await acquire_country_data_mutation_lock(
            db, str(getattr(updater, "series_country_code", "") or updater.country_code)
        )
        import_result = await updater.import_rows(
            db,
            rows,
            db_latest_date=db_latest_date,
            source_latest_date=source_latest_date,
            force=force,
        )
        series_result = await CrawlService._save_series_rows(
            db,
            updater,
            rows,
            series_rows=series_rows,
            **({"authoritative_window_start": authoritative_window_start}
               if authoritative_window_start is not None else {}),
        )
        # Public curves still attach legacy supplemental metrics. A complete
        # case-series comparison cannot prove that deaths/recoveries or rates
        # did not change on the legacy side (including clearing a prior value).
        supplemental_keys = {
            "Deaths", "deaths", "Recoveries", "recoveries",
            "MortalityRate", "mortality_rate", "RecoveryRate", "recovery_rate",
        }
        if (
            int(getattr(import_result, "inserted_or_updated", 0) or 0) > 0
            and any(supplemental_keys.intersection(row) for row in rows)
            and isinstance(series_result, SeriesObservationSaveResult)
        ):
            series_result = replace(series_result, publication_changes=None)
        if publication_results is not None:
            publication_results.append(series_result)
        # Source-native integrations can intentionally have no legacy disease
        # projection.  Surface their successfully persisted fact count to the
        # task/control-plane result instead of incorrectly reporting zero.
        if (
            int(getattr(import_result, "inserted_or_updated", 0) or 0) == 0
            and (int(getattr(series_result, "upserted", 0) or 0)
                 + int(getattr(series_result, "deleted", 0) or 0)) > 0
        ):
            try:
                return replace(
                    import_result,
                    inserted_or_updated=series_result.upserted,
                    imported_new_data=True,
                )
            except TypeError:
                # Compatibility test doubles need not be dataclasses.
                setattr(import_result, "inserted_or_updated", series_result.upserted)
                setattr(import_result, "imported_new_data", True)
        return import_result

    @staticmethod
    def _series_quality_policy(updater) -> SeriesObservationQualityPolicy:
        return _series_rows.series_quality_policy(updater,
            source_ids=CrawlService._SERIES_SOURCE_IDS,
            country_config=get_country_bootstrap_config)

    async def execute(
        self,
        task: Task,
        country_code: str,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        include_current_month: Optional[bool] = None,
        revision_window_months: Optional[int] = None,
    ) -> CrawlResult:
        """
        Run the full crawl pipeline and return a summary.

        Progress is reported via task_manager (0 → 100 %).
        Raises on unrecoverable errors (caller handles via task_lifecycle).
        """
        normalized_country = (country_code or "").strip().upper()
        normalized_source = str(source or "all").strip().casefold()
        if normalized_source == "all":
            preferred = get_country_bootstrap_config(normalized_country).get("crawler_config", {}).get("preferred_source")
            if preferred:
                source = normalized_source = str(preferred)
        if normalized_country in {"AR", "TH", *SOUTH_ASIA_SOURCES}:
            from src.core.source_scopes import canonicalize_task_source
            source = canonicalize_task_source(source, country_code=normalized_country)
            normalized_source = source
        from src.core.high_frequency_sources import high_frequency_source
        high_frequency = high_frequency_source(normalized_country, normalized_source)
        if (normalized_country, normalized_source) in SOUTH_ASIA_ALL_SOURCES:
            spec = south_asia_source(normalized_country, normalized_source)
            pipeline = _PipelineSpec("_execute_south_asia", spec["updater"])
        elif high_frequency is not None:
            pipeline = _PipelineSpec("_execute_high_frequency", f"high_frequency:{normalized_source}")
        elif (normalized_country, normalized_source) in EXTRA_SOURCE_CONTRACTS:
            extra = EXTRA_SOURCE_CONTRACTS[(normalized_country, normalized_source)]
            pipeline = _PipelineSpec("_execute_remaining_extra", extra["updater"])
        elif normalized_country == "AR" and normalized_source == "ar_moh_hiv_annual":
            pipeline = _PipelineSpec("_execute_ar_hiv_annual", "ArgentinaHIVAnnualUpdater")
        elif normalized_country == "CO" and normalized_source == "co_ins_sivigila_current":
            pipeline = _PipelineSpec("_execute_co_current_surveillance", "ColombiaINSSivigilaCurrentUpdater")
        elif normalized_country == "CO" and normalized_source == "co_ins_bes_weekly":
            pipeline = _PipelineSpec("_execute_co_bes_surveillance", "ColombiaINSBESUpdater")
        elif normalized_country == "CO" and normalized_source == "co_ins_sivigila_microdatos":
            pipeline = _PipelineSpec("_execute_co_microdatos", "ColombiaINSMicrodataUpdater")
        elif normalized_country == "MY" and normalized_source == "who_flunet_weekly":
            pipeline = _PipelineSpec("_execute_my_who_flunet", "MYWHOFluNetUpdater")
        elif normalized_country == "MY" and normalized_source == "who_gho_annual":
            pipeline = _PipelineSpec("_execute_my_who_annual", "MYWHOAnnualUpdater")
        elif normalized_country == "MY" and normalized_source == "my_idengue_daily":
            pipeline = _PipelineSpec("_execute_my_dengue", "MalaysiaIDengueDailyUpdater")
        elif normalized_country == "NL" and normalized_source == "rivm_virological_weekly":
            pipeline = _PipelineSpec("_execute_nl_weekly", "NLVirologicalWeeklyUpdater")
        elif normalized_country == "NL" and normalized_source == "rivm_respiratory_weekly":
            pipeline = _PipelineSpec("_execute_nl_weekly", "NLRespiratoryWeeklyUpdater")
        elif normalized_country == "TH" and normalized_source == "th_dds_tableau":
            pipeline = _PipelineSpec("_execute_th_tableau", "ThailandDDSTableauUpdater")
        elif normalized_country == "TH" and normalized_source == "th_id_th_history":
            pipeline = _PipelineSpec("_execute_th_id_th_history", "ThailandIDTHHistoryUpdater")
        elif normalized_country == "TH" and normalized_source in {"th_doe506_monthly", "doe506", "th_506"}:
            pipeline = _PipelineSpec("_execute_th_monthly", "ThailandDOE506MonthlyUpdater")
        elif (
            normalized_country in ECDC_BASELINE_COUNTRY_CODES
            and normalized_source in {"ecdc", "atlas", "ecdc_atlas_annual"}
        ):
            # Countries with a higher-frequency national pipeline can also
            # refresh the independent ECDC annual fallback explicitly.
            pipeline = _PipelineSpec("_execute_ecdc_annual", "ECDCAnnualUpdater")
        else:
            pipeline = self._PIPELINES.get(normalized_country)
        if pipeline is None:
            raise ValueError(
                f"Unsupported country: {normalized_country or country_code}. "
                f"Available: {self.supported_country_text()}"
            )

        implementation = (task.input_data or {}).get("pipeline_impl")
        if implementation not in {None, "v2"}:
            raise ValueError(f"Unsupported pipeline implementation: {implementation}")
        if pipeline.handler_name != "_execute_cn_cdc":
            updater = self._make_updater(
                pipeline.updater_name or "", normalized_country
            )
            self._configure_monthly_runtime_policy(
                updater,
                country_code=normalized_country,
                source=source,
                include_current_month=include_current_month,
                revision_window_months=revision_window_months,
            )
            if normalized_country == "BR" or normalized_country.startswith("BR-"):
                start_year = (
                    task.input_data.get("start_year")
                    if isinstance(task.input_data, dict)
                    else None
                )
                result = await self._execute_br_monthly(
                    task=task,
                    source=source,
                    force=force,
                    process=process,
                    save_raw=save_raw,
                    fill_missing=fill_missing,
                    start_year=start_year,
                    updater=updater,
                )
            else:
                handler = getattr(self, pipeline.handler_name)
                result = await handler(
                    task=task,
                    source=source,
                    force=force,
                    process=process,
                    save_raw=save_raw,
                    fill_missing=fill_missing,
                    updater=updater,
                )
        else:
            result = await self._execute_cn_cdc(
                task=task,
                country_code=normalized_country,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
            )
        return result

    async def fail_current_run(
        self,
        *,
        country_code: str,
        source: str,
        started_after: datetime,
        error: BaseException,
        status: str = "failed",
    ) -> Optional[int]:
        """Finalize the run opened by a failed task invocation.

        Pipelines create their audit row before external I/O.  This bounded
        lookup is safe because crawl tasks are serialized per country and the
        timestamp fence prevents an older orphan from being rewritten when the
        current run failed before it could create its own audit row.
        """

        if status not in {"failed", "cancelled"}:
            raise ValueError(f"Unsupported CrawlRun terminal status: {status}")
        normalized_country = str(country_code or "").strip().upper()
        normalized_source = str(source or "all").strip().lower()
        fence = started_after
        if fence.tzinfo is None:
            fence = fence.replace(tzinfo=timezone.utc)
        else:
            fence = fence.astimezone(timezone.utc)
        async with get_database() as db:
            run = (
                await db.execute(
                    select(CrawlRun)
                    .where(
                        CrawlRun.country_code == normalized_country,
                        CrawlRun.source == normalized_source,
                        CrawlRun.status == "running",
                        CrawlRun.started_at >= fence,
                    )
                    .order_by(CrawlRun.started_at.desc())
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if run is None:
                return None
            run.status = status
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = str(error)[-12000:]
            metadata = dict(run.metadata_ or {})
            metadata["failed_closed"] = status == "failed"
            metadata["terminal_status"] = status
            run.metadata_ = metadata
            run_id = run.id
            await db.commit()
        return run_id

    @staticmethod
    def _configure_monthly_runtime_policy(
        updater,
        *,
        country_code: str,
        include_current_month: Optional[bool],
        revision_window_months: Optional[int],
        source: str = "",
    ) -> None:
        """Apply task/control-plane month policy to a compatible updater."""

        config = get_country_bootstrap_config(country_code)
        crawler_config = (
            config.get("crawler_config", {}) if isinstance(config, dict) else {}
        )
        source_policies = crawler_config.get("source_policies", {})
        source_policy = (
            source_policies.get(str(source or "").strip().casefold(), {})
            if isinstance(source_policies, dict)
            else {}
        )
        if not isinstance(source_policy, dict):
            source_policy = {}
        effective_config = {**crawler_config, **source_policy}
        if include_current_month is None:
            raw_current = effective_config.get("default_include_current_month", False)
            if isinstance(raw_current, str):
                effective_current = raw_current.strip().casefold() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
            else:
                effective_current = bool(raw_current)
        else:
            effective_current = bool(include_current_month)

        weekly_window = hasattr(updater, "refresh_recent_weeks")
        configured_window = (
            effective_config.get(
                "default_revision_window",
                effective_config.get("refresh_recent_weeks", 12),
            )
            if weekly_window
            else effective_config.get(
                "default_revision_window",
                effective_config.get("refresh_recent_months", 3),
            )
        )
        try:
            effective_window = int(
                revision_window_months
                if revision_window_months is not None
                else configured_window
            )
        except (TypeError, ValueError):
            effective_window = 12 if weekly_window else 3
        effective_window = max(1, min(52 if weekly_window else 24, effective_window))

        if hasattr(updater, "include_current_month"):
            updater.include_current_month = effective_current
        if hasattr(updater, "refresh_recent_months"):
            updater.refresh_recent_months = effective_window
        if hasattr(updater, "refresh_recent_weeks"):
            updater.refresh_recent_weeks = effective_window

    async def _execute_cn_province(self, *, task, source, force, process, save_raw, fill_missing, updater):
        from src.services.ingestion.countries import cn_province
        from src.services.ingestion.runner import run_surveillance
        known_case_months, recently_attempted_months = (set(), set())
        bounded_backfill = bool(fill_missing and source == "cn_province_monthly_report"
                                and not force and (task.input_data or {}).get("start_year") is None)
        if bounded_backfill:
            known_case_months, recently_attempted_months = await self._cn_province_backfill_state(task, updater.country_code)
        options = {
            "country_code": updater.country_code,
            "force": bool(force),
            "fill_missing": bounded_backfill,
            "start_year": (task.input_data or {}).get("start_year"),
            "recent_months": updater.refresh_recent_months,
            "known_case_months": sorted(known_case_months),
            "recently_attempted_months": sorted(recently_attempted_months),
        }
        return await run_surveillance(
            self, task=task, updater=updater, connector=cn_province,
            source=source, force=force, process=process,
            strategy=cn_province.ProvinceCommitStrategy(), connector_options=options,
        )

    @staticmethod
    async def _cn_province_backfill_state(task, country_code):
        """Read covered months and recent attempted gaps before opening source HTTP."""
        from datetime import timedelta
        from sqlalchemy import text
        from src.core.database import get_db
        from src.domain import SourceArtifact

        known, attempted = set(), set()
        cutoff = datetime.now(timezone.utc) - timedelta(days=180)
        async with get_db() as db:
            result = await db.execute(text("""
                SELECT DISTINCT to_char(o.time AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM')
                FROM disease_series_observations o
                JOIN disease_surveillance_series s USING (series_code)
                WHERE o.geography_key = :geography_key AND o.value IS NOT NULL
                  AND s.source_system = 'SRC_CN_PROV_MONTHLY_REPORT'
                  AND s.metric_type = 'case_notifications'
                  AND o.time >= '2022-01-01'
            """), {"geography_key": f"country:{country_code}:national"})
            known.update(str(value) for value in result.scalars())
            tasks = (await db.execute(select(Task).where(
                Task.country_id == task.country_id,
                Task.created_at >= cutoff,
            ).order_by(Task.created_at.desc()))).scalars().all()
            monthly_tasks = [previous for previous in tasks
                             if isinstance(previous.input_data, dict)
                             and previous.input_data.get("source") == "cn_province_monthly_report"]
            artifacts = (await db.execute(select(SourceArtifact).join(
                Task, Task.task_uuid == SourceArtifact.task_uuid,
            ).where(
                Task.country_id == task.country_id,
                SourceArtifact.created_at >= cutoff,
                SourceArtifact.kind == "province_plan",
            ))).scalars().all()
        for previous in monthly_tasks:
            audit = (previous.metadata_ or {}).get("province_ingestion") or {}
            backfill = (audit.get("source_audit") or {}).get("history_backfill") or {}
            attempted.update(str(month) for month in backfill.get("selected_months") or [])
        for artifact in artifacts:
            backfill = (artifact.metadata_ or {}).get("history_backfill") or {}
            attempted.update(str(month) for month in backfill.get("selected_months") or [])
        return known, attempted

    async def _execute_cn_cdc(
        self,
        *,
        task: Task,
        country_code: str,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
    ) -> CrawlResult:
        """Run the source through durable collection stages."""
        from src.services.ingestion.countries import cn
        from src.services.ingestion.runner import run_surveillance
        return await cn.run(self, task=task, source=source, force=force, process=process, fill_missing=fill_missing)


    async def _execute_us_weekly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the source through durable collection stages."""
        from src.services.ingestion.countries import us
        from src.services.ingestion.runner import run_surveillance
        task_input = task.input_data or {}
        scheduled = bool(task_input.get("scheduled_trigger")) and not bool(task_input.get("manual_trigger"))
        # Old queued tasks may still carry fill_missing=True after a schedule
        # edit. Routine checks must not silently turn into historical replays.
        effective_fill_missing = fill_missing and not (scheduled and not force)
        async with get_database() as db:
            options = await us.plan_collection(
                db, country_code=updater.country_code, source=source,
                force=force, fill_missing=effective_fill_missing,
            )
        logger.info("US collection plan | country={} source={} scheduled={} force={} fill_missing={} plan={}",
                    updater.country_code, source, scheduled, force, effective_fill_missing, options)
        return await run_surveillance(self, task=task, updater=updater, connector=us, source=source, force=force, process=process, connector_options={
            **options, "subdivision": updater.country_code != "US", "task_uuid": task.task_uuid, "force": force,
        })


    async def _execute_jp_weekly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the source through durable collection stages."""
        from src.services.ingestion.countries import jp
        from src.services.ingestion.runner import run_surveillance
        config = get_country_bootstrap_config("JP").get("crawler_config", {})
        recent_weeks = max(1, int(config.get("incremental_cache_weeks", 12)))
        return await run_surveillance(self, task=task, updater=updater, connector=jp, source=source, force=force, process=process, connector_options={"output_csv": updater.output_csv, "force": force, "fill_missing": fill_missing, "incremental_cache_weeks": recent_weeks})


    async def _execute_configured_monthly(self, country_code: str, **kwargs) -> CrawlResult:
        """Dispatch explicit monthly source contracts to the three-stage runner."""
        from importlib import import_module
        from src.services.crawl_pipelines.monthly import CONFIGS, _months_to_fetch
        from src.services.ingestion.runner import run_surveillance

        modules = {"AU": "au", "NZ": "nz", "TW": "tw", "HK": "hk", "FI": "fi", "CA-ON": "ca_on", "SE": "se", "AT": "at"}
        if country_code not in modules:
            raise ValueError(f"No staged monthly contract for {country_code}")
        config = CONFIGS[country_code]
        if kwargs["fill_missing"] and not config.supports_fill_missing:
            raise ValueError(f"{country_code} publishes a complete snapshot; fill_missing is not supported")
        updater, task = kwargs["updater"], kwargs["task"]
        task_input = task.input_data or {}
        if country_code == "TW":
            now = datetime.now()
            current = (now.year, now.month)
            recent = updater._default_recent_months(as_of=now.date())
            scheduled = bool(task_input.get("scheduled_trigger")) and not bool(task_input.get("manual_trigger"))
            if kwargs["fill_missing"] and not scheduled:
                raw_start = task_input.get("start_year")
                if raw_start is not None:
                    try:
                        start_year = int(raw_start)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("TW start_year must be a valid year") from exc
                    if not config.start_year <= start_year <= now.year:
                        raise ValueError(f"TW start_year must be between {config.start_year} and {now.year}")
                    config = replace(config, start_year=start_year)
                planned = await _months_to_fetch(config, updater, fill_missing=True, force=kwargs["force"], get_database=get_database)
                historical = sorted({month for month in planned if month not in recent and (updater.include_current_month or month < current)})
                batch = historical[:12]
                months = sorted(set(recent) | set(batch))
                if len(historical) > len(batch):
                    logger.warning("TW manual historical backfill limited to %s months; %s country-level missing months remain unverified", len(batch), len(historical) - len(batch))
            else:
                months = recent
                if scheduled and kwargs["fill_missing"]:
                    logger.warning("TW scheduled refresh covers only the recent revision window; historical gaps remain unverified and require an explicit backfill")
        else:
            months = await _months_to_fetch(config, updater, fill_missing=kwargs["fill_missing"], force=kwargs["force"], get_database=get_database)
        options = {"months": months, "output_csv": updater.output_csv}
        if country_code == "AU":
            options.update(country_code=updater.country_code, include_current_month=updater.include_current_month, refresh_recent_months=updater.refresh_recent_months)
        elif country_code == "AT":
            options.update(force=kwargs["force"], refresh_recent_months=updater.refresh_recent_months)
        elif country_code == "FI":
            options.update(include_current_month=updater.include_current_month, refresh_recent_months=updater.refresh_recent_months, backfill_history=bool((task.input_data or {}).get("backfill_history")))
        elif country_code == "SE":
            options.update(country_code=updater.country_code, include_current_month=updater.include_current_month, force=kwargs["force"], refresh_recent_months=updater.refresh_recent_months)
        elif country_code == "CA-ON":
            options.update(force=kwargs["force"], **updater.pipeline_refresh_kwargs(task))
        connector = import_module("src.services.ingestion.countries." + modules[country_code])
        return await run_surveillance(self, task=task, updater=updater, connector=connector, source=kwargs["source"], force=kwargs["force"], process=kwargs["process"], connector_options=options)

    async def _execute_au_monthly(self, **kwargs) -> CrawlResult:
        return await self._execute_configured_monthly("AU", **kwargs)

    async def _execute_nz_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the New Zealand monthly pipeline."""
        return await self._execute_configured_monthly(
            "NZ",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_nz_dhb_annual(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run independent annual DHB observations through durable stages."""
        from src.services.ingestion.countries import nz_dhb
        from src.services.ingestion.runner import run_surveillance
        return await run_surveillance(self, task=task, updater=updater, connector=nz_dhb, source=source, force=force, process=process, connector_options={"country_code": updater.country_code, "output_csv": updater.output_csv})


    async def _execute_tw_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Taiwan, China monthly pipeline."""
        return await self._execute_configured_monthly(
            "TW",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_hk_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Hong Kong monthly pipeline."""
        return await self._execute_configured_monthly(
            "HK",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_fi_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Finland THL national monthly pipeline."""
        return await self._execute_configured_monthly(
            "FI",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_ie_weekly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Dispatch Ireland's current, annual, or archived-weekly source."""
        if str(source or "").strip().casefold() == "hpsc_weekly_archive":
            from src.data.processors import IEWeeklyArchiveUpdater
            from src.services.crawl_pipelines.ie_weekly_archive import (
                execute_ie_weekly_archive_pipeline,
            )

            return await execute_ie_weekly_archive_pipeline(
                self,
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=IEWeeklyArchiveUpdater(),
                get_database=get_database,
                task_manager=task_manager,
                crawl_run_type=CrawlRun,
                result_type=CrawlResult,
                logger=logger,
            )

        if str(source or "").strip().casefold() == "hpsc_annual":
            from src.data.processors import IEAnnualUpdater
            from src.services.crawl_pipelines.ie_annual import (
                execute_ie_annual_pipeline,
            )

            return await execute_ie_annual_pipeline(
                self,
                task=task,
                source=source,
                force=force,
                process=process,
                save_raw=save_raw,
                fill_missing=fill_missing,
                updater=IEAnnualUpdater(),
                get_database=get_database,
                task_manager=task_manager,
                crawl_run_type=CrawlRun,
                result_type=CrawlResult,
                logger=logger,
            )

        from src.services.crawl_pipelines.ie import execute_ie_pipeline

        return await execute_ie_pipeline(
            self,
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=CrawlRun,
            result_type=CrawlResult,
            logger=logger,
        )

    async def _execute_no_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Norway FHI MSIS national monthly pipeline."""
        from src.services.crawl_pipelines.no import execute_no_pipeline

        return await execute_no_pipeline(
            self,
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
            get_database=get_database,
            task_manager=task_manager,
            crawl_run_type=CrawlRun,
            result_type=CrawlResult,
            logger=logger,
        )

    async def _execute_se_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the Sweden SmiNet internal monthly pipeline."""
        return await self._execute_configured_monthly(
            "SE",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_at_monthly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        return await self._execute_configured_monthly(
            "AT", task=task, source=source, force=force, process=process,
            save_raw=save_raw, fill_missing=fill_missing, updater=updater,
        )

    async def _execute_de_weekly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import de
        today = datetime.now(timezone.utc).date()
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, min(today.year, int(raw_start or updater.full_history_start_year)))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        if force:
            weeks = updater.history_weeks(today=today, start_year=start_year)
        elif fill_missing:
            async with get_database() as db:
                existing = await updater.get_db_week_dates(db)
            weeks = sorted(set(updater.history_weeks(today=today, start_year=start_year)).difference(existing) | set(updater._recent_weeks(today)))
        else:
            weeks = updater._recent_weeks(today)
        return await run_surveillance(
            self, task=task, updater=updater, connector=de, source=source,
            force=force, process=process,
            connector_options={"country_code": updater.country_code, "weeks": weeks, "force": force, "export_url_template": updater.export_url_template, "history_csv": updater.output_csv},
        )

    async def _execute_sg_weekly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import sg
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, min(datetime.now(timezone.utc).year, int(raw_start or updater.full_history_start_year)))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        return await run_surveillance(
            self, task=task, updater=updater, connector=sg, source=source,
            force=force, process=process,
            connector_options={"force": force, "fill_missing": fill_missing, "start_year": start_year, "history_csv": updater.output_csv},
        )

    async def _execute_th_tableau(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import th_tableau
        from src.services.ingestion.countries.th_refresh import runtime_options
        if fill_missing:
            raise ValueError("TH Tableau imports explicit closed source periods; fill_missing is not supported")
        options = task.input_data or {}
        return await run_surveillance(
            self, task=task, updater=updater, connector=th_tableau, source=source,
            force=force, process=process,
            connector_options=await runtime_options(updater, options, default_basis="report"),
        )

    async def _execute_th_id_th_history(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import th_id_th
        if fill_missing:
            raise ValueError("ID_TH source gaps remain unknown; fill_missing is not supported")
        options = task.input_data or {}
        return await run_surveillance(
            self, task=task, updater=updater, connector=th_id_th, source=source,
            force=force, process=process,
            connector_options={key: options[key] for key in
                ("start_year", "end_year", "archive_root") if options.get(key) is not None},
        )

    async def _execute_th_dds_weekly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import th_dds
        from src.services.ingestion.countries.th_refresh import runtime_options
        if fill_missing:
            raise ValueError("TH DDS exposes source-selected closed weeks; fill_missing is not supported")
        options = task.input_data or {}
        return await run_surveillance(
            self, task=task, updater=updater, connector=th_dds, source=source,
            force=force, process=process,
            connector_options=await runtime_options(updater, options, default_basis="onset"),
        )

    async def _execute_th_monthly(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import th
        from src.data.crawlers.th import DEFAULT_START_YEAR, DEFAULT_END_YEAR, DEFAULT_MAX_REPORTS
        if fill_missing:
            raise ValueError("TH refreshes validated annual reports; fill_missing is not supported")
        options = task.input_data or {}
        return await run_surveillance(
            self, task=task, updater=updater, connector=th, source=source,
            force=force, process=process,
            connector_options={
                "start_year": options.get("start_year") or DEFAULT_START_YEAR,
                "end_year": options.get("end_year") or DEFAULT_END_YEAR,
                "disease_codes": options.get("disease_codes"),
                "mode": options.get("mode"),
                "backfill_history": bool(options.get("backfill_history", False)),
                "report_offset": options.get("report_offset", 0),
                "max_reports": options.get("max_reports", DEFAULT_MAX_REPORTS),
                "resume_plan_hash": options.get("resume_plan_hash"),
                "recent_years": options.get("recent_years", 3),
            },
        )

    async def _execute_south_asia(self, **kwargs) -> CrawlResult:
        spec = south_asia_source(kwargs["updater"].country_code, kwargs["source"])
        return await self._execute_priority_surveillance(spec["module"], **kwargs)

    async def _execute_priority_surveillance(
        self, module: str, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        """Capture full discovered historical archives before source-native storage."""
        from importlib import import_module
        from src.services.ingestion.runner import run_surveillance
        if fill_missing:
            raise ValueError("Official report archives require full refresh; fill_missing is unsupported")
        supplied = task.input_data or {}
        start_year = supplied.get("start_year") or updater.full_history_start_year
        # Automation checks live catalogues over a bounded revision window;
        # explicit historical requests and forced full replays retain their scope.
        from src.core.source_channels import source_channel
        channel = source_channel(updater.country_code, source)
        managed_update = supplied.get("scheduled_trigger") or supplied.get("automation_job_id")
        if channel and managed_update and not supplied.get("start_year") and not force:
            years = max(1, min(24, int(supplied.get("revision_window_months") or channel["refresh_years"])))
            start_year = max(updater.full_history_start_year, datetime.now(timezone.utc).year - years + 1)
        if not updater.full_history_start_year <= int(start_year) <= datetime.now(timezone.utc).year:
            raise ValueError("start_year is outside the official historical archive range")
        options = {"start_year": int(start_year), "force": force}
        if module in {spec["module"] for spec in SOUTH_ASIA_ALL_SOURCES.values()}:
            for name in ("max_reports", "max_pages", "max_directory_pages"):
                if name in supplied:
                    options[name] = int(supplied[name])
        if module in {"in_idsp", "pk_idsr", "np_sormas", "lk_wer"}:
            for name in ("max_reports", "weeks", "max_index_pages"):
                if name in supplied:
                    options[name] = supplied[name]
        if module in {"kh", "pe", "id"}:
            from pathlib import Path
            cache_name = {"kh": "camewarn-parses", "pe": "bulletin-parses", "id": "kemkes-parses"}[module]
            options["parse_cache_directory"] = Path("data/cache") / module / cache_name
            if module == "pe":
                # The resumable historical supervisor has its own active .part
                # files. Routine updates must never append to those transfers
                # or race its parser cache's temporary-file replacement.
                options["download_cache_directory"] = Path("data/cache/pe/routine-downloads")
                options["parse_cache_directory"] = Path("data/cache/pe/routine-parses")
        if supplied.get("end_year") is not None:
            options["end_year"] = int(supplied["end_year"])
        return await run_surveillance(
            self, task=task, updater=updater,
            connector=import_module(f"src.services.ingestion.countries.{module}"),
            source=source, force=force, process=process, connector_options=options,
        )

    async def _execute_ng_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance("ng", **kwargs)

    async def _execute_cl_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance('cl', **kwargs)

    async def _execute_co_surveillance(self, **kwargs) -> CrawlResult:
        from src.services.ingestion import co_update_gate
        return await self._execute_co_bounded_surveillance(co_update_gate, **kwargs)

    async def _execute_co_current_surveillance(self, **kwargs) -> CrawlResult:
        from src.services.ingestion import co_current_refresh
        return await self._execute_co_bounded_surveillance(co_current_refresh, **kwargs)

    async def _execute_co_bes_surveillance(self, **kwargs) -> CrawlResult:
        from src.services.ingestion import co_bes_refresh
        supplied = kwargs['task'].input_data or {}
        current_year = datetime.now(ZoneInfo('America/Bogota')).year
        options = {'start_year': int(supplied['start_year']) if supplied.get('start_year') is not None
                   else max(2025, current_year - 1),
                   'end_year': int(supplied['end_year']) if supplied.get('end_year') is not None
                   else current_year,
                   'download_workers': 8, 'parse_workers': 4}
        return await self._execute_co_bounded_surveillance(
            co_bes_refresh, store_rows=True, connector_options=options, **kwargs)

    async def _execute_co_microdatos(self, **kwargs) -> CrawlResult:
        from src.services.ingestion import co_microdatos_refresh
        supplied = kwargs['task'].input_data or {}
        last_closed_year = datetime.now(ZoneInfo('America/Bogota')).year - 1
        options = {'start_year': int(supplied.get('start_year') or last_closed_year),
                   'end_year': int(supplied.get('end_year') or last_closed_year)}
        # Store classification strata as private native facts. Publication
        # review remains independent; these are never a live BES fallback.
        return await self._execute_co_bounded_surveillance(
            co_microdatos_refresh, store_rows=True, connector_options=options, **kwargs)

    async def _execute_co_bounded_surveillance(
        self, connector, *, store_rows=False, connector_options=None, **kwargs
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance

        if kwargs.get('fill_missing'):
            raise ValueError('CO SIVIGILA requires a complete version-bound source controller')
        try:
            return await run_surveillance(
                self, task=kwargs['task'], updater=kwargs['updater'], connector=connector,
                source=kwargs['source'], force=kwargs['force'],
                process=kwargs['process'] if store_rows else False,
                connector_options={**(connector_options or {}), 'force': kwargs['force']},
            )
        except BaseException as exc:
            # The shared runner records the failed stage, but only completes its
            # CrawlRun on success. Keep a failed metadata gate out of "running".
            try:
                prefix = f"data/raw/co/{kwargs['task'].task_uuid}/%"
                async with get_database() as db:
                    run_id = (await db.execute(select(CrawlRun.id).where(
                        CrawlRun.raw_dir.like(prefix), CrawlRun.status == 'running'
                    ).order_by(CrawlRun.id.desc()).limit(1))).scalar_one_or_none()
                if run_id is not None:
                    await self._finish_crawl_run(run_id, 0, 0, 0, status='failed', error=str(exc))
            except Exception as cleanup_error:
                logger.warning('Could not mark failed CO metadata gate CrawlRun: {}', cleanup_error)
            raise

    async def _execute_mw_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance('mw', **kwargs)

    async def _execute_za_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance('za', **kwargs)

    async def _execute_id_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance('id', **kwargs)

    async def _execute_mx_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance('mx', **kwargs)

    async def _execute_kh_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance("kh", **kwargs)

    async def _execute_ar_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance("ar", **kwargs)

    async def _execute_ar_hiv_annual(self, **kwargs) -> CrawlResult:
        # Replay the full published table for revisions, including years older
        # than a routine recent-year window. BEN keeps its separate pipeline.
        return await self._execute_notifiable_annual("ar_hiv", **kwargs)

    async def _execute_pe_surveillance(self, **kwargs) -> CrawlResult:
        return await self._execute_priority_surveillance("pe", **kwargs)

    async def _execute_notifiable_annual(
        self, module: str, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        """Refresh complete official annual notification tables."""
        from importlib import import_module
        from src.services.ingestion.runner import run_surveillance
        if fill_missing:
            raise ValueError(f"{updater.country_code} refreshes the complete annual table; fill_missing is not supported")
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = int(raw_start) if raw_start is not None else updater.full_history_start_year
        except (TypeError, ValueError) as exc:
            raise ValueError("start_year must be a valid year") from exc
        if not updater.full_history_start_year <= start_year <= datetime.now(timezone.utc).year:
            raise ValueError("start_year is outside the supported historical range")
        return await run_surveillance(
            self, task=task, updater=updater,
            connector=import_module(f"src.services.ingestion.countries.{module}"),
            source=source, force=force, process=process,
            connector_options={"output_csv": updater.output_csv, "start_year": start_year, "force": force},
        )

    async def _execute_my_annual(self, **kwargs) -> CrawlResult:
        return await self._execute_notifiable_annual("my_notifiable", **kwargs)

    async def _execute_nl_annual(self, **kwargs) -> CrawlResult:
        return await self._execute_notifiable_annual("nl_notifiable", **kwargs)

    async def _execute_current_surveillance(
        self, module: str, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        """Refresh explicit daily/weekly source facts without synthesizing gaps."""
        from importlib import import_module
        from src.services.ingestion.runner import run_surveillance
        if fill_missing:
            raise ValueError("Current surveillance sources do not support fill_missing")
        inputs = task.input_data or {}
        return await run_surveillance(
            self, task=task, updater=updater,
            connector=import_module(f"src.services.ingestion.countries.{module}"),
            source=source, force=force, process=process,
            connector_options={key: inputs[key] for key in ("start_year",)
                               if inputs.get(key) is not None},
        )

    async def _execute_high_frequency(
        self, *, task, source, force, process, save_raw, fill_missing, updater,
    ) -> CrawlResult:
        """Run registered high-frequency feeds through the ordinary durable stages."""
        from importlib import import_module
        from src.core.high_frequency_sources import high_frequency_source
        from src.services.ingestion.runner import run_surveillance
        if fill_missing:
            raise ValueError("Source-native high-frequency feeds do not synthesize missing periods")
        spec = high_frequency_source(updater.country_code, source)
        if spec is None:
            raise ValueError("High-frequency source does not belong to this country")
        inputs = task.input_data or {}
        options = {"country_code": updater.country_code, "force": bool(force)}
        years = int(inputs.get("revision_window_months") or 3)
        if not 1 <= years <= 24:
            raise ValueError("High-frequency revision window must be 1–24 calendar years")
        options["start_year"] = (
            spec.start_year if force
            else max(spec.start_year, datetime.now(timezone.utc).year - years + 1)
        )
        for name in ("start_year", "end_year", "max_reports", "max_pdfs", "cache_ttl_days"):
            if inputs.get(name) is not None:
                options[name] = inputs[name]
        strategy = None
        if updater.country_code == "CO" and source == "who_flunet_weekly":
            from src.services.ingestion.co_flunet_commit import ColombiaFluNetCommitStrategy
            options.setdefault("start_year", 2025)
            strategy = ColombiaFluNetCommitStrategy(options["start_year"])
        return await run_surveillance(
            self, task=task, updater=updater,
            connector=import_module(f"src.services.ingestion.countries.{spec.module}"),
            source=source, force=force, process=process, connector_options=options, strategy=strategy,
        )

    async def _execute_my_dengue(self, **kwargs) -> CrawlResult:
        return await self._execute_current_surveillance("my_dengue", **kwargs)

    async def _execute_nl_weekly(self, **kwargs) -> CrawlResult:
        return await self._execute_current_surveillance("nl_weekly", **kwargs)

    async def _execute_my_who_flunet(self, **kwargs) -> CrawlResult:
        return await self._execute_current_surveillance("my_who_flunet", **kwargs)

    async def _execute_my_who_annual(self, **kwargs) -> CrawlResult:
        return await self._execute_current_surveillance("my_who_annual", **kwargs)

    async def _execute_remaining_annual(self, module, *, task, source, force, process,
                                        save_raw, fill_missing, updater):
        from importlib import import_module
        from src.core.source_scopes import canonicalize_task_source
        from src.services.ingestion.runner import run_surveillance
        if fill_missing:
            raise ValueError("Annual reported sources do not support fill_missing")
        options = {"country_code": updater.country_code}
        if (updater.country_code, source) in EXTRA_SOURCE_CONTRACTS:
            options["force"] = force
        if (task.input_data or {}).get("start_year") is not None:
            options["start_year"] = int(task.input_data["start_year"])
        return await run_surveillance(
            self, task=task, updater=updater,
            connector=import_module(f"src.services.ingestion.countries.{module}"),
            source=canonicalize_task_source(source, country_code=updater.country_code),
            force=force, process=process, connector_options=options,
        )

    async def _execute_remaining_extra(self, **kwargs):
        contract = EXTRA_SOURCE_CONTRACTS[(kwargs["updater"].country_code, kwargs["source"])]
        return await self._execute_remaining_annual(contract["module"], **kwargs)

    async def _execute_who_country_annual(self, **kwargs):
        return await self._execute_remaining_annual("who_annual", **kwargs)

    async def _execute_spf_overseas_annual(self, **kwargs):
        return await self._execute_remaining_annual("spf_overseas", **kwargs)

    async def _execute_ecdc_annual(
        self, *, task: Task, source: str, force: bool, process: bool,
        save_raw: bool, fill_missing: bool, updater,
    ) -> CrawlResult:
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import ecdc
        from src.services.ingestion.strategies import EcdcWindowCommitStrategy
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, int(raw_start or updater.full_history_start_year))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        return await run_surveillance(
            self, task=task, updater=updater, connector=ecdc, source=source,
            force=force, process=process, strategy=EcdcWindowCommitStrategy(),
            strategy_options={"start_year": start_year},
            connector_options={"country_code": updater.country_code, "start_year": start_year, "history_csv": updater.output_csv},
        )

    async def _execute_ca_on_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run Ontario through the shared country/region monthly pipeline."""
        return await self._execute_configured_monthly(
            "CA-ON",
            task=task,
            source=source,
            force=force,
            process=process,
            save_raw=save_raw,
            fill_missing=fill_missing,
            updater=updater,
        )

    async def _execute_ca_national_annual(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run the national Canadian CNDSS annual authoritative snapshot."""
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import ca_national
        from src.services.ingestion.strategies import CanadaCndssWindowCommitStrategy
        raw_start = (task.input_data or {}).get("start_year")
        try:
            start_year = max(updater.full_history_start_year, int(raw_start or updater.full_history_start_year))
        except (TypeError, ValueError):
            start_year = updater.full_history_start_year
        return await run_surveillance(
            self, task=task, updater=updater, connector=ca_national, source=source,
            force=force, process=process, strategy=CanadaCndssWindowCommitStrategy(),
            strategy_options={"start_year": start_year}, connector_options={"start_year": start_year},
        )

    async def _execute_br_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        start_year: Optional[int] = None,
        updater,
    ) -> CrawlResult:
        """Run source collection, offline organization and atomic commit."""
        from src.services.ingestion.countries import br
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.planning import history_start_year, plan_months
        boundary = history_start_year(task, updater, start_year)
        months = await plan_months(task, updater, force=force, fill_missing=fill_missing, start_year=boundary)
        months = updater._resolve_requested_months(months)
        options = {"months": months, "history_csv": updater.output_csv, "start_year": boundary, "country_code": updater.country_code, "force": force}
        return await run_surveillance(self, task=task, updater=updater, connector=br, source=source, force=force, process=process, connector_options=options)




    async def _execute_kr_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run source collection, offline organization and atomic commit."""
        from src.services.ingestion.countries import kr
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.planning import history_start_year, plan_months
        boundary = history_start_year(task, updater)
        months = await plan_months(task, updater, force=force, fill_missing=fill_missing, start_year=boundary)
        months = updater._resolve_requested_months(months)
        options = {"months": months, "history_csv": updater.output_csv, "source_file": (task.input_data or {}).get("source_file"), "source_dir": (task.input_data or {}).get("source_dir")}
        return await run_surveillance(self, task=task, updater=updater, connector=kr, source=source, force=force, process=process, connector_options=options)



    async def _execute_ch_monthly(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run source collection, offline organization and atomic commit."""
        from src.services.ingestion.countries import ch
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.planning import history_start_year, plan_months
        boundary = history_start_year(task, updater)
        months = await plan_months(task, updater, force=False, fill_missing=fill_missing and not force, start_year=boundary)
        options = {"country_code": updater.country_code, "history_csv": updater.output_csv, "months": months, "force": force, "start_year": boundary if force else None}
        return await run_surveillance(self, task=task, updater=updater, connector=ch, source=source, force=force, process=process, connector_options=options)



    async def _execute_is_mixed(
        self,
        *,
        task: Task,
        source: str,
        force: bool,
        process: bool,
        save_raw: bool,
        fill_missing: bool,
        updater,
    ) -> CrawlResult:
        """Run Iceland's mixed annual/monthly/weekly surveillance pipeline."""
        from src.core.source_scopes import validate_iceland_crawl_options
        from src.services.ingestion.runner import run_surveillance
        from src.services.ingestion.countries import is_ as connector
        normalized = connector.normalize_native_source(source)
        start_year = (task.input_data or {}).get("start_year")
        validate_iceland_crawl_options(country_code="IS", source=normalized, fill_missing=fill_missing, start_year=start_year)
        strategy = connector.IcelandCurrentCommitStrategy()
        if normalized in connector.HISTORY_SOURCES:
            from src.data.processors.is_history import IcelandHistoryProcessor, HISTORY_SOURCE_NAME, HISTORY_SOURCE_ID, LEGACY_SOURCE_NAME, LEGACY_SOURCE_ID
            updater = IcelandHistoryProcessor()
            updater.ontology_source_id = {HISTORY_SOURCE_NAME: HISTORY_SOURCE_ID, LEGACY_SOURCE_NAME: LEGACY_SOURCE_ID}
            updater.series_geography_key = "country:IS:national"
            strategy = connector.IcelandHistoryCommitStrategy()
        return await run_surveillance(
            self, task=task, updater=updater, connector=connector,
            source=normalized, force=force, process=process, strategy=strategy,
            connector_options={"fill_missing": fill_missing, "start_year": start_year, "history_csv": getattr(updater, "output_csv", None)},
        )

    @staticmethod
    def _source_distribution(results) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in results:
            meta = item.metadata or {}
            source = str(meta.get("source") or "Unknown")
            counts[source] = counts.get(source, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: kv[0]))

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _add_raw_archive_entry(
        self,
        task_uuid: str,
        *,
        save_raw: bool,
        raw_dir: Path,
    ) -> None:
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="info",
            title="Raw Archive",
            content=(
                f"Save raw enabled: {'Yes' if save_raw else 'No'}\n"
                + (f"Archive path: {raw_dir}" if save_raw else "Archive path: disabled")
            ),
            content_type="text",
        )

    async def _finish_crawl_run(
        self,
        run_id: Optional[int],
        new_reports: int,
        processed: int,
        records: int,
        status: str = "completed",
        error: Optional[str] = None,
    ) -> None:
        if run_id is None:
            return
        try:
            async with get_database() as db:
                run = await db.get(CrawlRun, run_id)
                if run:
                    run.status = status
                    run.finished_at = datetime.now(timezone.utc)
                    run.new_reports = new_reports
                    run.processed_reports = processed
                    run.total_records = records
                    if error:
                        run.error_message = error
                    await db.commit()
        except Exception as e:
            logger.warning(f"Could not update CrawlRun {run_id}: {e}")
