"""Source-specific routing for the independently measured high-frequency feeds.

An annual notification baseline is retained beside laboratory and syndromic
feeds. Source priority selects a collector, never sums unlike observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module

from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRIES
from src.core.remaining_country_sources import WHO_COUNTRIES, SPF_TERRITORIES


@dataclass(frozen=True)
class HighFrequencySource:
    country_code: str
    scope: str
    source_id: str
    module: str
    updater_class: str
    label_en: str
    label_zh: str
    url: str
    frequency: str
    start_year: int
    measurement: str
    updater_arguments: tuple[tuple[str, str], ...] = ()


HIGH_FREQUENCY_SOURCES: dict[tuple[str, str], HighFrequencySource] = {}


def _add(code, scope, source_id, module, updater_class, label_en, label_zh,
         url, frequency, start_year, measurement, **arguments):
    HIGH_FREQUENCY_SOURCES[(code, scope)] = HighFrequencySource(
        code, scope, source_id, module, updater_class, label_en, label_zh,
        url, frequency, start_year, measurement, tuple(arguments.items()),
    )


for _code, _country in ECDC_BASELINE_COUNTRIES.items():
    if _code == "GB":
        continue  # The current EU/EEA CSV does not include the United Kingdom.
    _add(_code, "ecdc_respiratory_weekly", f"SRC_{_code}_ECDC_RESPIRATORY",
         "ecdc_respiratory", "ECDCRespiratoryUpdater",
         f"{_country['name']} ECDC Weekly Respiratory Surveillance",
         f"{_country['name_zh']} ECDC 呼吸道周监测",
         "https://github.com/EU-ECDC/Respiratory_viruses_weekly_data",
         "weekly", 2022, "laboratory_surveillance", country_code=_code)

# These countries have national annual baselines or explicitly separate
# national feeds; the WHO network is an additional laboratory population.
FLUNET_COUNTRIES = frozenset({*WHO_COUNTRIES, "MY", "AR", "CL", "MX", "ID", "CO"})
for _code in sorted(FLUNET_COUNTRIES):
    _names = WHO_COUNTRIES.get(_code)
    _en, _zh = (_names[:2] if _names else {
        "MY": ("Malaysia", "马来西亚"), "AR": ("Argentina", "阿根廷"),
        "CL": ("Chile", "智利"), "GB": ("United Kingdom", "英国"),
        "MX": ("Mexico", "墨西哥"),
        "ID": ("Indonesia", "印度尼西亚"),
        "CO": ("Colombia", "哥伦比亚"),
    }[_code])
    _add(_code, "who_flunet_weekly", f"SRC_{_code}_WHO_FLUNET",
         "who_flunet", "WHOFluNetUpdater",
         f"{_en} WHO FluNet Weekly Respiratory Laboratory Surveillance",
         f"{_zh} WHO FluNet 呼吸道实验室周监测",
         "https://www.who.int/tools/flunet", "weekly", 1996,
         "laboratory_detections", country_code=_code)

for _code, _scope, _sid, _en, _zh, _url, _year in (
    ("CZ", "cz_szu_monthly", "SRC_CZ_SZU_MONTHLY", "Czechia SZÚ Monthly Notifications",
     "捷克 SZÚ 月度疾病报告", "https://szu.gov.cz/publikace-szu/data/infekce-v-cr/", 2026),
    ("EE", "ee_terviseamet_monthly", "SRC_EE_TERVISEAMET_MONTHLY", "Estonia Terviseamet Monthly Notifications",
     "爱沙尼亚卫生局月度疾病报告", "https://www.terviseamet.ee/nakkushaigused/nakkushaiguste-statistika", 2026),
):
    _add(_code, _scope, _sid, "eu_monthly", "EUMonthlyUpdater", _en, _zh,
         _url, "monthly", _year, "case_notifications", country_code=_code)

for _scope, _frequency in (("mx_dge_weekly", "weekly"), ("mx_dge_monthly", "monthly")):
    _add("MX", _scope, f"SRC_MX_DGE_{_frequency.upper()}",
         "mx_high_frequency", "MexicoDGEHighFrequencyUpdater",
         f"Mexico DGE {_frequency.title()} Morbidity",
         f"墨西哥 DGE {'周' if _frequency == 'weekly' else '月'}度发病监测",
         ("https://www.gob.mx/salud/acciones-y-programas/direccion-general-de-epidemiologia-boletin-epidemiologico"
          if _frequency == "weekly" else "https://epidemiologia.salud.gob.mx/anuario/html/morbilidad_nacional.html"),
         _frequency, 2025 if _frequency == "weekly" else 2003,
         "case_notifications", source=_scope)

_add("CA", "ca_phac_respiratory_weekly", "SRC_CA_PHAC_RESPIRATORY",
     "ca_respiratory_weekly", "CARespiratoryWeeklyUpdater",
     "Canada PHAC Weekly Respiratory Virus Detections", "加拿大 PHAC 呼吸道病毒周检测",
     "https://health-infobase.canada.ca/respiratory-virus-surveillance/",
     "weekly", 1996, "laboratory_surveillance")

for _code, (_en, _zh, *_rest) in SPF_TERRITORIES.items():
    _add(_code, "spf_odisse_weekly", f"SRC_{_code}_SPF_ODISSE_WEEKLY",
         "spf_odisse_weekly", "SPFOdisseWeeklyUpdater",
         f"{_en} SPF ODISSÉ Weekly Syndromic Surveillance", f"{_zh} SPF ODISSÉ 综合征周监测",
         "https://odisse.santepubliquefrance.fr/explore/dataset/grippe-passages-urgences-et-actes-sos-medecin_reg/",
         "weekly", 2010, "syndromic_surveillance_rates", country_code=_code)


def sources_for_country(country_code: str) -> tuple[HighFrequencySource, ...]:
    code = str(country_code or "").upper()
    return tuple(value for (country, _), value in HIGH_FREQUENCY_SOURCES.items() if country == code)


def high_frequency_source(country_code: str, scope: str) -> HighFrequencySource | None:
    return HIGH_FREQUENCY_SOURCES.get((str(country_code).upper(), str(scope).strip().lower()))


def make_updater(country_code: str, scope: str):
    spec = high_frequency_source(country_code, scope)
    if spec is None:
        raise ValueError(f"Unsupported high-frequency source {country_code}/{scope}")
    cls = getattr(import_module(f"src.data.processors.{spec.module}"), spec.updater_class)
    updater = cls(**dict(spec.updater_arguments))
    updater.full_history_start_year = spec.start_year
    # These feeds discover categories without sending mapping review emails.
    updater.mapping_notifications_enabled = False
    return updater


def preferred_source(country_code: str) -> HighFrequencySource | None:
    """Prefer explicit incident notification tables before laboratory feeds."""
    sources = sources_for_country(country_code)
    if not sources:
        return None
    return min(sources, key=lambda source: (
        source.measurement != "case_notifications",
        source.frequency != "weekly",
        source.scope,
    ))
