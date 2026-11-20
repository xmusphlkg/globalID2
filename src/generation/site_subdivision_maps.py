"""Versioned province maps with explicit, exact-year official denominators.

The reviewed import profiles below admit complete all-population notification
periods. They do not admit sentinel rates, cumulative counts or stratified series.
Population is always read from population_records, never inferred from a rate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text

from src.core.country_library import get_country_bootstrap_config
from src.generation.site_data_writer import write_compact_json, remove_stale_json_files

ROOT = Path(__file__).resolve().parents[2]
BOUNDARIES = ROOT / "external-data/subdivision-boundaries"
PROFILES = {
    "AU": {"population_source": "ABS", "basis": {
        "en": "NINDSS notifications; laboratory-confirmed influenza where specified by the source.",
        "zh": "NINDSS 通知病例；流感为来源指定的实验室确诊病例。",
        "fr": "Notifications NINDSS ; grippe confirmée en laboratoire selon la source.",
    }},
    "BR": {"population_source": "IBGE", "basis": {
        "en": "SINAN notifications by residence state and notification month; not necessarily confirmed infections.",
        "zh": "SINAN 按居住州、通知月份统计的通知病例，不等同于确诊感染。",
        "fr": "Notifications SINAN par État de résidence et mois de notification, pas nécessairement des infections confirmées.",
    }},
    "CN": {"population_source": "NBS", "basis": {
        "en": "Provincial statutory infectious-disease reports; reporting definitions can differ by disease.",
        "zh": "省级法定传染病报告病例；病例定义依病种而异。",
        "fr": "Déclarations provinciales des maladies à déclaration obligatoire ; définitions propres à chaque maladie.",
    }},
    "US": {"population_source": "Census", "source_system": "SRC_US_NNDSS", "basis": {
        "en": "CDC NNDSS weekly notifications by reporting jurisdiction; provisional source reports, not cumulative YTD counts.",
        "zh": "CDC NNDSS 按报告辖区统计的周度通知病例；暂定报告数，不是年内累计数。",
        "fr": "Notifications hebdomadaires provisoires du CDC NNDSS par juridiction, sans cumul annuel.",
    }},
    "JP": {"population_source": "StatisticsJapan", "source_system": "SRC_JP_NIID", "basis": {
        "en": "IDWR all-case weekly notifications by prefecture. Sentinel and hospital-surveillance series are excluded.",
        "zh": "IDWR 都道府县周度全数报告病例；不纳入定点哨点及医院监测序列。",
        "fr": "Déclarations exhaustives hebdomadaires IDWR par préfecture, hors surveillance sentinelle ou hospitalière.",
    }},
    "SE": {"population_source": "SCB", "source_system": "SRC_SE_FOHM_SMINET", "basis": {
        "en": "FHM SmiNet monthly notifications by county; revised registry counts.",
        "zh": "FHM SmiNet 各地区月度通知病例，保留登记修订。",
        "fr": "Notifications mensuelles révisées du registre FHM SmiNet par comté.",
    }},
}

# These adapters count different events in time. A disease map chooses one
# adapter across provinces; the country chart's priority overlay is unsuitable.
CN_SOURCES = {
    "SRC_CN_PROV_DATACENTER": {
        "definitions": {"CN_PROVINCE_DATACENTER_ONSET_V1"},
        "reporting_basis": "national_notifiable_disease_surveillance_by_onset",
        "time_basis": "month of onset",
        "basis": {
            "en": "China CDC data centre provincial counts by month of onset; conditional comparability between provinces.",
            "zh": "中国疾控数据中心按发病月统计的省级病例数；省际可比性有条件。",
            "fr": "Décomptes provinciaux du centre de données du CDC chinois par mois de début des symptômes ; comparabilité conditionnelle.",
        },
    },
    "SRC_CN_PROV_MONTHLY_REPORT": {
        "definitions": {"CN_PROVINCE_MONTHLY_REPORT_V1", "CN_PROVINCE_MONTHLY_NOVEL_INFLUENZA_2025_09",
                        "CN_PROVINCE_MONTHLY_CHIKUNGUNYA_2026_04", "CN_PROVINCE_MONTHLY_SFTS_2026_04"},
        "reporting_basis": "provincial_statutory_infectious_disease_monthly_report",
        "time_basis": "publisher reporting month",
        "basis": {
            "en": "Official provincial monthly reports by publisher reporting month; reporting definitions may differ between provinces.",
            "zh": "各省官方月报按发布方报告月统计；省际报告口径可能不同。",
            "fr": "Rapports mensuels provinciaux officiels selon le mois de déclaration ; définitions pouvant varier entre provinces.",
        },
    },
}


def source_series_candidates(entry: dict, country_code: str, location_code: str, *, as_of: date | None = None) -> dict[str, dict]:
    """Read one registered source curve, never an overlay's projected values."""
    if entry.get("data_layer") == "legacy_fallback":
        return {}
    cadence = entry.get("period_granularity")
    if cadence not in {"weekly", "monthly", "quarterly", "annual"}:
        return {}
    candidates = defaultdict(list)
    for source in entry.get("source_series") or []:
        system = source.get("source_system")
        contract = CN_SOURCES.get(system) if country_code == "CN" else None
        if not source.get("series_code"):
            continue
        if (source.get("metric_type") not in {"cases", "case_notifications", "case_count", "case_count_reported", "confirmed_cases", "reported_cases"} or source.get("unit") not in {"count", "cases", "persons"}
                or source.get("temporal_granularity") != cadence
                or source.get("mapping_relation") != "exact"
                or source.get("geography_key") != f"country:{location_code}:national"
                or source.get("dimension_key") != "all" or source.get("dimensions") != {}
                or (contract and (source.get("definition_version") not in contract["definitions"]
                    or cadence != "monthly"
                    or source.get("reporting_basis") != contract["reporting_basis"]
                    or source.get("time_basis") != contract["time_basis"]))
                or source.get("comparability") not in {"conditional", "comparable"}
                or source.get("comparability_break") or source.get("series_is_active") is False):
            continue
        # Do not guess a quality status for a missing/misaligned source array.
        dates, values, quality = (source.get(key) or [] for key in ("dates", "values", "point_quality_statuses"))
        if (not dates or len(dates) != len(values) or len(dates) != len(quality)
                or any(not isinstance(status, str) or not status for status in quality)):
            continue
        projected = {**source, "period_granularity": cadence, "cases": values,
                     "quality_statuses": quality, "selected_series_codes": [source["series_code"]],
                     "source_series": [source], "data_layer": "series_registry"}
        result = reported_series(projected, country_code, as_of=as_of, location_code=location_code)
        if result:
            # The snapshot's primary URL can belong to the other adapter.
            result["source_url"] = source.get("source_url")
            group = "|".join(str(source.get(key) or "") for key in ("source_system", "definition_version", "reporting_basis", "time_basis", "metric_type"))
            result["source_system"] = system
            result["source_group"] = group
            candidates[group].append(result)
    # Ambiguous parallel definitions cannot silently overwrite one another.
    return {group: results[0] for group, results in candidates.items() if len(results) == 1}


def parent_code(country: dict) -> str | None:
    metadata = country.get("metadata") or {}
    explicit = country.get("parent_code") or (metadata.get("parent_country_code") if isinstance(metadata, dict) else None)
    # Bootstrap is the authoritative crosswalk, not a guess based on a hyphen.
    return explicit or get_country_bootstrap_config(str(country.get("code") or country.get("country_code") or "")).get("parent_country_code")


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalise_population(row: dict) -> dict | None:
    code, year = str(row.get("country_code") or ""), int(row.get("year") or 0)
    profile = PROFILES.get(parent_code({"code": code}))
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if not profile or row.get("source") != profile["population_source"]:
        return None  # In particular, national WPP never supplies a subdivision.
    population = row.get("population")
    reference = str(metadata.get("reference_date") or "")
    source_url = str(metadata.get("source_url") or "")
    basis = metadata.get("population_basis")
    try:
        same_year = date.fromisoformat(reference).year == year
    except ValueError:
        same_year = False
    if not finite(population) or population <= 0 or not same_year or not basis or not source_url.startswith("https://"):
        return None
    official_domains = {"ABS": ("abs.gov.au",), "NBS": ("stats.gov.cn",), "IBGE": ("ibge.gov.br",), "Census": ("census.gov",), "StatisticsJapan": ("stat.go.jp", "e-stat.go.jp"), "SCB": ("scb.se",)}[row["source"]]
    hostname = urlparse(source_url).hostname or ""
    if not any(hostname == domain or hostname.endswith("." + domain) for domain in official_domains):
        return None
    allowed_basis = {
        "NBS": {"year_end_resident_population"},
        "ABS": {"june_estimated_resident_population", "latest_observed_quarter_same_year"},
        "IBGE": {"july_estimated_resident_population", "census_resident_population"},
        "Census": {"july_estimated_resident_population"},
        "StatisticsJapan": {"october_estimated_resident_population", "october_official_census_rebased_resident_population"},
        "SCB": {"year_end_resident_population", "year_end_resident_population_ckm"},
    }
    if basis not in allowed_basis[row["source"]]:
        return None
    if metadata.get("location_code", code) != code:
        return None
    result = {
        "location_code": code, "year": year, "value": population, "source": row["source"],
        "reference_date": reference, "basis": basis, "source_url": source_url,
        "method": "same_calendar_year_no_interpolation",
    }
    result["version"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


async def fetch_map_populations(session) -> list[dict]:
    result = await session.execute(text("""
        SELECT c.code AS country_code, p.year, p.population, p.source, p.metadata
        FROM population_records p JOIN countries c ON c.id = p.country_id
        WHERE c.is_active = true ORDER BY c.code, p.year
    """))
    return [dict(row._mapping) for row in result]


def source_period(day: date, cadence: str, country_code: str) -> dict | None:
    """Resolve source dates using the reviewed adapters' original calendars.

    US processor dates are MMWR Saturday ends; JP dates are IDWR ISO Sunday
    ends. Other weekly series need an explicit calendar review before admission.
    The reporting year is the source week year, including boundary-spanning weeks.
    """
    if cadence == "weekly":
        if country_code == "US" and day.weekday() == 5:
            def week_one_end(year):
                jan4 = date(year, 1, 4)
                return jan4 - timedelta(days=(jan4.weekday() + 1) % 7) + timedelta(days=6)
            year = next((year for year in range(day.year - 1, day.year + 2)
                         if week_one_end(year) <= day < week_one_end(year + 1)), None)
        elif country_code == "JP" and day.weekday() == 6:
            year = day.isocalendar().year  # The JP adapter explicitly uses IDWR ISO weeks.
        else:
            return None
        start, end = day - timedelta(days=6), day + timedelta(days=1)
    elif cadence in {"monthly", "quarterly", "annual"}:
        step = {"monthly": 1, "quarterly": 3, "annual": 12}[cadence]
        if day.day != 1 or (day.month - 1) % step:
            return None
        month = day.month + step
        start, end, year = day, date(day.year + (month - 1) // 12, (month - 1) % 12 + 1, 1), day.year
    else:
        return None
    return {"start": start.isoformat(), "end_exclusive": end.isoformat(), "reporting_year": year}


def reported_series(entry: dict, country_code: str, *, as_of: date | None = None, location_code: str | None = None) -> dict | None:
    """Export one reviewed count series without combining source definitions."""
    cadence = entry.get("period_granularity")
    if cadence not in {"weekly", "monthly", "quarterly", "annual"}:
        return None
    metric = entry.get("metric_type")
    allowed_metrics = {"cases", "case_notifications", "case_count", "case_count_reported", "confirmed_cases", "reported_cases"}
    if metric and metric not in allowed_metrics:
        return None
    if entry.get("comparability") in {"non_comparable", "incomparable", "not_comparable"}:
        return None
    if entry.get("data_layer") == "legacy_fallback":
        return None
    sources = entry.get("source_series") or []
    selected = set(entry.get("selected_series_codes") or [])
    selected_sources = [source for source in sources if source.get("series_code") in selected]
    # A map must be backed by the exact registered source series that produced
    # its points.  This also prevents legacy fallback curves (which have no
    # selected registry source) from entering a subnational map by accident.
    if (sources or selected) and (not selected or len(selected_sources) != len(selected)):
        return None
    point_sources = set(entry.get("point_series_codes") or [])
    if point_sources and not point_sources <= selected:
        return None
    profile = PROFILES.get(country_code, {})
    reviewed_direct = country_code in {"US", "JP", "SE"}
    if reviewed_direct:
        # A single explicitly selected source can be used independently even if
        # other definitions are non-additive. Never sum the alternative series.
        if len(selected) != 1 or len(selected_sources) != 1:
            return None
    elif entry.get("loss_risk") == "non_additive_series_not_rolled_up":
        return None
    if entry.get("projection_policy") == "priority_overlay":
        # The projection layer has already resolved these definitions per
        # period. Recheck its identity here so a malformed entry cannot turn
        # an arbitrary set of non-additive sources into a map.
        overlay_sets = {str(source.get("comparability_set") or "") for source in selected_sources}
        if (any(source.get("projection_policy") != "priority_overlay" for source in selected_sources)
                or len(overlay_sets) != 1 or "" in overlay_sets
                or any(isinstance(source.get("projection_priority"), bool)
                       or not isinstance(source.get("projection_priority"), (int, float))
                       or not math.isfinite(float(source.get("projection_priority")))
                       for source in selected_sources)):
            return None
    for source in selected_sources:
        if source.get("dimension_key") not in {None, "all"} or source.get("dimensions"):
            return None
        if source.get("reporting_basis") in {"sentinel_surveillance", "hospital_surveillance"}:
            return None
        if any(word in str(source.get("source_label") or "").lower() for word in ("in adults", "in children", "under 5", "pregnan")):
            return None
        if reviewed_direct:
            if (source.get("source_system") != profile.get("source_system")
                    or source.get("metric_type") not in allowed_metrics
                    or source.get("unit") not in {"count", "cases", "persons"}
                    or source.get("temporal_granularity") != cadence
                    or source.get("mapping_relation") != "exact"
                    or source.get("comparability") in {"non_comparable", "incomparable", "not_comparable"}
                    or source.get("comparability_break")):
                return None
            if location_code and source.get("geography_key") != f"country:{location_code}:national":
                return None
        elif (source.get("aggregation_policy") == "non_additive"
              and len(selected) > 1
              and entry.get("projection_policy") != "priority_overlay"):
            # ``non_additive`` means independent source definitions must not
            # be summed.  A single selected source and an explicit priority
            # overlay are already resolved by the series projection layer;
            # rejecting them here silently removes otherwise valid maps.
            return None
    dates, values = entry.get("dates") or [], entry.get("cases") or []
    if not dates or len(dates) != len(values):
        return None
    quality = entry.get("quality_statuses") or []
    granularities = entry.get("point_granularities") or []
    points, periods, seen = [], [], set()
    for index, (raw_date, value) in enumerate(zip(dates, values)):
        try:
            day = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            return None
        period = source_period(day, cadence, country_code)
        if not period or day in seen:
            return None
        if granularities and index < len(granularities) and granularities[index] not in {None, cadence}:
            return None
        seen.add(day)
        status = str(quality[index] or "observed") if index < len(quality) else "observed"
        if status in {"suppressed", "source_value_suppressed"}:
            value, status = None, "suppressed"
        elif value is None:
            status = "missing_or_suppressed" if country_code == "AU" else "missing"
        elif not finite(value) or value < 0 or int(value) != value:
            return None
        elif status in {"partial", "partial_report_window", "period_scope_mismatch", "missing", "invalid", "excluded"}:
            value, status = None, "incomplete"
        # ``raw`` remains explicit: it is a numeric source observation that
        # has not completed validation. It must not be presented as observed.
        elif status not in {"observed", "provisional", "revised", "verified", "good", "complete", "confirmed", "high", "raw"}:
            value, status = None, "incomplete"
        else:
            if status == "raw":
                status = "raw"
            else:
                status = "provisional" if status == "provisional" or (entry.get("provisional_from") and str(raw_date) >= entry["provisional_from"]) else "observed"
        if as_of and date.fromisoformat(period["end_exclusive"]) > as_of:
            value, status = None, "incomplete"
        points.append([day.isoformat(), value, status])
        periods.append(period)
    if not any(point[1] is not None for point in points):
        return None
    ordered = sorted(zip(points, periods), key=lambda item: item[0][0])
    points, periods = [item[0] for item in ordered], [item[1] for item in ordered]
    semantics = {
        "metric": metric or "reviewed_monthly_notifications",
        "definition": entry.get("definition_version") or "reviewed-import-profile-v1",
        "reporting_basis": entry.get("reporting_basis") or "notification",
        "time_basis": entry.get("time_basis") or ("reporting_week" if cadence == "weekly" else "reporting_period"),
        "source_series": sorted(selected),
        "source_labels": sorted({str(source.get("source_label") or source.get("source_series_code") or source["series_code"]) for source in selected_sources}),
        "cadence": cadence,
        "calendar": "MMWR" if country_code == "US" and cadence == "weekly" else "IDWR_ISO" if country_code == "JP" and cadence == "weekly" else "gregorian",
    }
    key = hashlib.sha256(json.dumps(semantics, sort_keys=True).encode()).hexdigest()[:16]
    case_version = hashlib.sha256(json.dumps({"points": points, "periods": periods, "semantics": semantics}, sort_keys=True).encode()).hexdigest()
    return {"points": points, "periods": periods, "cadence": cadence, "comparison_key": key, "semantics": semantics, "data_layer": entry.get("data_layer"), "case_version": case_version}


def monthly_series(entry: dict, country_code: str, *, as_of: date | None = None) -> dict | None:
    """Compatibility entry point for callers explicitly requiring monthly data."""
    return reported_series(entry, country_code, as_of=as_of) if entry.get("period_granularity") == "monthly" else None


def build_map_payloads(code: str, snapshots: dict[str, dict], population_rows: list[dict], boundary: dict) -> tuple[dict, dict, dict[str, dict]]:
    regions, by_disease = [], defaultdict(dict)
    source_candidates = defaultdict(lambda: defaultdict(dict))
    for region_code, snapshot in sorted(snapshots.items()):
        if region_code not in boundary["codes"] or parent_code({"code": region_code, "parent_code": snapshot.get("parent_code")}) != code:
            continue
        regions.append({"code": region_code, "name_en": snapshot.get("country_name_en") or snapshot.get("country_name") or region_code, "name_zh": snapshot.get("country_name_zh") or region_code})
        try:
            as_of = date.fromisoformat(str(snapshot.get("generated_at") or "")[:10])
        except ValueError:
            as_of = None
        for disease_id, entry in (snapshot.get("disease_series") or {}).items():
            candidates = source_series_candidates(entry, code, region_code, as_of=as_of)
            if candidates:
                for group, result in candidates.items():
                    source_candidates[disease_id][group][region_code] = {
                        **result,
                        "source_url": result.get("source_url"),
                        "labels": {"en": entry.get("name_en") or disease_id, "zh": entry.get("name_zh") or entry.get("name_en") or disease_id},
                        "slug": entry.get("slug"),
                    }
                continue
            result = reported_series(entry, code, as_of=as_of, location_code=region_code)
            if result:
                by_disease[disease_id][region_code] = {**result, "source_url": (snapshot.get("source_info") or {}).get("primary_url"), "labels": {"en": entry.get("name_en") or disease_id, "zh": entry.get("name_zh") or entry.get("name_en") or disease_id}, "slug": entry.get("slug")}
    # Select one registered source curve for each disease. This keeps every
    # country on one source definition/time basis and leaves uncovered regions
    # explicit instead of filling them from another adapter.
    for disease_id, groups in source_candidates.items():
        if groups:
            selected = sorted(groups, key=lambda item: (-len(groups[item]), item))[0]
            by_disease[disease_id] = groups[selected]
    populations: dict[str, dict] = defaultdict(dict)
    for row in population_rows:
        region_code = str(row.get("country_code"))
        if region_code not in {region["code"] for region in regions}:
            continue
        value = normalise_population(row)
        if value:
            year = str(value["year"])
            if year in populations[region_code]:
                raise ValueError(f"Duplicate population {region_code}/{year}")
            populations[region_code][year] = value
    disease_payloads, catalogue = {}, []
    for disease_id, entries in sorted(by_disease.items()):
        # Keep incompatible groups visible as excluded regions. Never merge them.
        counts = Counter(entry["comparison_key"] for entry in entries.values())
        comparison_key = sorted(counts, key=lambda key: (-counts[key], key))[0]
        years = sorted({period["reporting_year"] for entry in entries.values() if entry["comparison_key"] == comparison_key for period in entry["periods"]})
        first = next(entry for entry in entries.values() if entry["comparison_key"] == comparison_key)
        basis = PROFILES[code]["basis"]
        if code == "CN":
            system = str(first.get("source_system") or "")
            basis = CN_SOURCES.get(system, {}).get("basis", basis)
        payload = {"schema_version": 1, "country_code": code, "disease_id": disease_id, "labels": first["labels"], "years": years, "cadence": first["cadence"], "comparison_key": comparison_key, "case_basis": basis, "regions": entries}
        disease_payloads[disease_id] = payload
        catalogue.append({"id": disease_id, "labels": first["labels"], "years": years, "cadence": first["cadence"], "path": f"/site-data/subdivision-maps/{code.lower()}/{disease_id.lower()}.json"})
    catalogue_payload = {"schema_version": 1, "country_code": code, "cadence": "mixed" if len({item["cadence"] for item in catalogue}) > 1 else (catalogue[0]["cadence"] if catalogue else "monthly"), "regions": regions, "diseases": catalogue, "expected_regions": boundary["expected_regions"], "boundary": {**boundary, "path": f"/site-data/subdivision-maps/{code.lower()}/boundary.json"}, "population_path": f"/site-data/subdivision-maps/{code.lower()}/population.json"}
    population_payload = {"schema_version": 1, "country_code": code, "regions": dict(populations)}
    return catalogue_payload, population_payload, disease_payloads


def _read_existing_map_payloads(root: Path, code: str) -> tuple[dict, dict, dict[str, dict]]:
    """Read a previous browser map generation for an incremental merge.

    The lossless country snapshots are not guaranteed to be present in a
    scoped release directory. The previous map payloads are the only complete
    source for unchanged provinces and diseases in that case.
    """
    directory = root / code.lower()
    catalogue_path = directory / "index.json"
    if not catalogue_path.is_file():
        return {}, {}, {}
    try:
        catalogue = json.loads(catalogue_path.read_text())
    except (OSError, ValueError):
        return {}, {}, {}
    if not isinstance(catalogue, dict) or catalogue.get("country_code") != code:
        return {}, {}, {}
    population = {}
    population_path = directory / "population.json"
    if population_path.is_file():
        try:
            loaded = json.loads(population_path.read_text())
            if isinstance(loaded, dict) and loaded.get("country_code") == code:
                population = loaded
        except (OSError, ValueError):
            population = {}
    diseases = {}
    for item in catalogue.get("diseases") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        disease_id = str(item["id"]).upper()
        path = directory / f"{disease_id.lower()}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("country_code") == code:
            diseases[disease_id] = payload
    return catalogue, population, diseases


def _merge_existing_map_payloads(
    code: str,
    catalogue: dict,
    population: dict,
    diseases: dict[str, dict],
    previous_catalogue: dict,
    previous_population: dict,
    previous_diseases: dict[str, dict],
    *,
    active_codes: set[str] | None,
) -> tuple[dict, dict, dict[str, dict]]:
    """Merge fresh entries over an existing incremental map baseline."""
    # Disease IDs are written in a canonical form so an upstream casing change
    # cannot create duplicate catalogue entries or leave stale payload files.
    diseases = {
        str(disease_id).upper(): payload
        for disease_id, payload in diseases.items()
    }
    for disease_id, payload in diseases.items():
        if isinstance(payload, dict):
            payload["disease_id"] = disease_id
    # A scoped incremental export can contain only changed subdivisions. The
    # parent country code identifies a complete active catalogue, while a set
    # without that parent is a partial scope and must inherit every existing
    # region and disease from the prior publication.
    allowed = set(active_codes) if active_codes is not None and code in active_codes else None
    def active(region_code: str) -> bool:
        return allowed is None or region_code in allowed

    regions = {str(item.get("code")): item for item in catalogue.get("regions") or [] if item.get("code")}
    for item in previous_catalogue.get("regions") or []:
        region_code = str(item.get("code") or "")
        if region_code and active(region_code):
            regions.setdefault(region_code, item)
    catalogue["regions"] = [regions[key] for key in sorted(regions)]

    merged_population = dict(population.get("regions") or {})
    for region_code, years in (previous_population.get("regions") or {}).items():
        if active(str(region_code)):
            merged_population.setdefault(region_code, years)
    population["regions"] = merged_population

    for disease_id, previous in previous_diseases.items():
        fresh = diseases.get(disease_id)
        if fresh is None:
            diseases[disease_id] = previous
            continue
        merged_regions = dict(previous.get("regions") or {})
        merged_regions.update(fresh.get("regions") or {})
        fresh["regions"] = {
            region_code: value for region_code, value in merged_regions.items()
            if active(str(region_code))
        }
    catalogue_by_id = {
        str(item.get("id")).upper(): {**item, "id": str(item.get("id")).upper()}
        for item in catalogue.get("diseases") or []
        if item.get("id")
    }
    for item in previous_catalogue.get("diseases") or []:
        disease_id = str(item.get("id") or "").upper()
        if disease_id and disease_id in diseases:
            catalogue_by_id.setdefault(disease_id, {**item, "id": disease_id})
    catalogue["diseases"] = [
        catalogue_by_id[key]
        for key in sorted(catalogue_by_id)
        if key in diseases and diseases[key].get("regions")
    ]
    catalogue["cadence"] = "mixed" if len({item.get("cadence") for item in catalogue["diseases"]}) > 1 else (catalogue["diseases"][0].get("cadence", "monthly") if catalogue["diseases"] else "monthly")
    return catalogue, population, diseases


def write_subdivision_maps(output_dir: Path, public_dir: Path, population_rows: list[dict], *, recorded_at_as_of: str | None = None, active_codes: set[str] | None = None, preserve_existing: bool = False) -> dict:
    manifest = json.loads((BOUNDARIES / "manifest.json").read_text())
    root = public_dir / "subdivision-maps"
    countries = []
    for code, boundary in sorted(manifest["countries"].items()):
        previous_catalogue, previous_population, previous_diseases = (
            _read_existing_map_payloads(public_dir / "subdivision-maps", code)
            if preserve_existing and recorded_at_as_of is None else ({}, {}, {})
        )
        snapshots = {}
        for path in (output_dir / "countries").glob(f"{code.lower()}-*.json"):
            snapshot = json.loads(path.read_text())
            region_code = str(snapshot["country_code"]).upper()
            if active_codes is None or region_code in active_codes:
                snapshots[region_code] = snapshot
        if not snapshots and not previous_diseases:
            remove_stale_json_files(root / code.lower(), set())
            continue
        catalogue, population, diseases = build_map_payloads(code, snapshots, population_rows, boundary)
        if previous_diseases:
            catalogue, population, diseases = _merge_existing_map_payloads(
                code, catalogue, population, diseases,
                previous_catalogue, previous_population, previous_diseases,
                active_codes=active_codes,
            )
        directory = root / code.lower()
        directory.mkdir(parents=True, exist_ok=True)
        geometry_path = BOUNDARIES / boundary["file"]
        if hashlib.sha256(geometry_path.read_bytes()).hexdigest() != boundary["sha256"]:
            raise ValueError(f"Boundary checksum mismatch: {code}")
        write_compact_json(directory / "boundary.json", json.loads(geometry_path.read_text()))
        # Historical observation replay has no corresponding historical population
        # ledger. Fail closed for rates rather than mix revised populations in.
        if recorded_at_as_of:
            population["regions"] = {}
            population["unavailable_reason"] = "historical_population_version_unavailable"
        write_compact_json(directory / "population.json", population)
        write_compact_json(directory / "index.json", catalogue)
        for disease_id, payload in diseases.items():
            write_compact_json(directory / f"{disease_id.lower()}.json", payload)
        remove_stale_json_files(directory, {"index.json", "population.json", "boundary.json", *(f"{key.lower()}.json" for key in diseases)})
        if diseases:
            countries.append({"code": code, "path": f"/site-data/subdivision-maps/{code.lower()}/index.json"})
    index = {"schema_version": 1, "countries": countries}
    root.mkdir(parents=True, exist_ok=True)
    write_compact_json(root / "index.json", index)
    return index
