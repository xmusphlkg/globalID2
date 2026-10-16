from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest

from src.data.normalizers.disease_mapper import DiseaseMapper
from src.core.ecdc_baselines import ECDC_BASELINE_COUNTRY_CODES
from src.services.disease_mapping_config import (
    DiseaseMappingConfigError,
    load_reviewed_source_category_mappings,
)


ROOT = Path(__file__).resolve().parents[2]


def _source_codes(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["SourceDiseaseCode"] for row in csv.DictReader(handle)}


def test_at_de_reviewed_manifests_cover_full_source_inventory() -> None:
    mappings = load_reviewed_source_category_mappings()
    national_source_ids = {
        "AT": "SRC_AT_AGES_RADAR",
        "DE": "SRC_DE_RKI_SURVSTAT",
    }
    by_country = {
        country: {
            item.source_code
            for item in mappings
            if item.country_code == country
            and item.source_id == national_source_ids[country]
        }
        for country in ("AT", "DE")
    }

    expected_counts = {code: 55 for code in ECDC_BASELINE_COUNTRY_CODES}
    expected_counts.update({
        "AT": 118,
        "CA": 70,
        "CH": 40,
        "CN": 1,
        "DE": 142,
        "IE": 111,
        "JP": 60,
        "NZ": 81,
        "SG": 76,
        "US": 66,
    })
    assert Counter(item.country_code for item in mappings) == expected_counts
    assert all(
        len(codes) == expected
        for codes, expected in ((by_country["AT"], 63), (by_country["DE"], 87))
    )

    # Developer workspaces retain the latest source snapshots outside git. If
    # present they provide an additional exact drift check; CI still validates
    # the reviewed manifest's fixed and unique inventories above.
    source_paths = {
        "AT": ROOT / "data/current/at/austria_ages_radar_monthly.csv",
        "DE": ROOT / "data/current/de/germany_rki_survstat_weekly.csv",
    }
    for country, path in source_paths.items():
        if path.exists():
            assert by_country[country] == _source_codes(path)


def test_at_de_high_risk_semantics_and_no_projection_boundaries() -> None:
    mappings = load_reviewed_source_category_mappings()
    indexed = {(item.country_code, item.source_code): item for item in mappings}

    assert indexed[("AT", "typhus")].target_code == "D124"
    assert indexed[("AT", "fleckfieber-rickettsiose-durch-r-prowazekii")].target_code == "D183"
    assert indexed[("DE", "typhus-abdominalis")].target_code == "D124"
    assert indexed[("DE", "fleckfieber")].target_code == "D183"
    assert indexed[("DE", "keratokunjunktivitis-meldepflicht-gem-ss-ifsg")].target_code == "D137"

    expected_no_projection = {
        ("AT", "clostridioides-difficile-infektion-schwerer-verlauf"),
        ("AT", "norovirus-gastroenteritis"),
        ("AT", "puerperalfieber"),
        ("AT", "sonstige-virusbedingte-meningoenzephalitis"),
        ("DE", "acinetobacter-infektion-oder-kolonisation"),
        ("DE", "bornavirus"),
        ("DE", "clostridium-difficile-schwerer-verlauf"),
        ("DE", "gasbrand"),
        ("DE", "hepatitis-non-a-e"),
        ("DE", "keratokunjunktivitis-meldepflicht-gem-ss-landesmeldeverordnung"),
        ("DE", "meningitis-andere"),
        ("DE", "norovirus-gastroenteritis"),
        ("DE", "orthopocken"),
        ("DE", "subakute-sklerosierende-panenzephalitis"),
        ("DE", "tollwutexpositionsverdacht"),
    }
    actual_no_projection = {
        (item.country_code, item.source_code)
        for item in mappings
        if item.projection_policy == "no_projection"
        and item.source_id in {"SRC_AT_AGES_RADAR", "SRC_DE_RKI_SURVSTAT"}
    }
    assert actual_no_projection == expected_no_projection
    assert all(
        item.target_kind == "group"
        and item.mapping_relation == "unmapped"
        and item.comparability == "not_comparable"
        for item in mappings
        if item.projection_policy == "no_projection"
        and item.source_id in {"SRC_AT_AGES_RADAR", "SRC_DE_RKI_SURVSTAT"}
    )


def test_ch_idd_manifest_preserves_lyme_metric_uncertainty() -> None:
    swiss = {
        item.source_code: item
        for item in load_reviewed_source_category_mappings()
        if item.country_code == "CH"
    }
    assert len(swiss) == 40
    assert swiss["campylobacteriosis"].target_code == "D092"
    assert swiss["campylobacteriosis"].projection_policy == "canonical"
    assert swiss["lyme_borreliosis"].target_kind == "group"
    assert swiss["lyme_borreliosis"].projection_policy == "no_projection"


def test_cn_reviewed_manifest_maps_native_viral_hepatitis_identity() -> None:
    mappings = load_reviewed_source_category_mappings()
    mapping = next(
        item
        for item in mappings
        if item.country_code == "CN" and item.source_code == "病毒性肝炎"
    )

    assert mapping.source_id == "SRC_CN_CDC"
    assert mapping.target_code == "D006"
    assert mapping.mapping_relation == "exact"
    assert mapping.projection_policy == "canonical"
    assert mapping.aggregation_policy == "reported_total"


def test_nz_reviewed_manifest_covers_every_registered_source_series() -> None:
    mappings = load_reviewed_source_category_mappings()
    nz = [
        item for item in mappings
        if item.country_code == "NZ" and item.source_id == "SRC_NZ_PHS"
    ]
    indexed = {item.source_code: item for item in nz}

    assert len(nz) == 36
    assert len(indexed) == 36
    assert indexed[
        "SER_NZ_HAEMOPHILUS_INFLUENZAE_TYPE_B_MONTHLY"
    ].mapping_relation == "narrower"
    assert indexed[
        "SER_NZ_NON_SEASONAL_INFLUENZA_A_H1N1_MONTHLY"
    ].mapping_relation == "narrower"
    assert all(item.source_id == "SRC_NZ_PHS" for item in nz)
    assert all(item.projection_policy == "canonical" for item in nz)


def test_nz_annual_dhb_manifest_preserves_source_semantics() -> None:
    rows = [
        item for item in load_reviewed_source_category_mappings()
        if item.country_code == "NZ" and item.source_id == "SRC_NZ_PHS_DHB"
    ]
    indexed = {item.local_name: item for item in rows}

    assert len(indexed) == 45
    assert sum(item.projection_policy == "canonical" for item in rows) == 40
    assert indexed["Haemophilus influenzae type b"].mapping_relation == "narrower"
    assert indexed["Hydatid disease"].target_code == "D045"
    assert indexed["Trichinosis"].target_code == "D122"
    assert indexed["Paratyphoid fever"].target_code == "D234"
    assert indexed["Typhoid fever"].target_code == "D124"
    assert {
        item.local_name for item in rows if item.projection_policy == "no_projection"
    } == {
        "Hepatitis NOS",
        "Meningoencephalitis - primary amoebic",
        "Rabies and other lyssaviruses",
        "Taeniasis",
        "Toxic shellfish poisoning",
    }


def test_us_reviewed_manifest_distinguishes_cases_from_other_metrics() -> None:
    rows = [
        item for item in load_reviewed_source_category_mappings()
        if item.country_code == "US"
        and item.source_id == "SRC_US_NNDSS"
        and item.definition_version == "source-current"
    ]
    indexed = {item.local_name: item for item in rows}

    assert len(indexed) == 66
    assert sum(item.projection_policy == "canonical" for item in rows) == 62
    assert indexed["Invasive pneumococcal disease, all ages, Confirmed"].mapping_relation == "narrower"
    assert indexed["Syphilis, Primary and secondary"].mapping_relation == "narrower"
    assert indexed["Salmonellosis (excluding Salmonella Typhi infection and Salmonella Paratyphi infection)"].target_code == "D114"
    assert indexed["Salmonella Typhi infection"].target_code == "D124"
    assert {
        item.local_name for item in rows if item.projection_policy == "no_projection"
    } == {
        "Candida auris, clinical",
        "Candida auris, screening",
        "Rabies, animal",
        "Influenza-associated pediatric mortality",
    }


def test_reviewed_manifest_rejects_duplicate_source_identity(tmp_path: Path) -> None:
    path = tmp_path / "at.csv"
    header = (
        "disease_id,local_name,local_code,source_id,mapping_scope,target_kind,"
        "target_code,mapping_relation,comparability,projection_policy,"
        "aggregation_policy,definition_version\n"
    )
    row = (
        "D124,Typhus,typhus,SRC_AT_AGES_RADAR,source_category_dimension,"
        "concept,D124,exact,conditional,canonical,direct_only,*\n"
    )
    path.write_text(header + row + row, encoding="utf-8")

    with pytest.raises(DiseaseMappingConfigError, match="duplicate reviewed source identity"):
        load_reviewed_source_category_mappings(tmp_path)


def test_legacy_mapper_skips_reviewed_no_projection_rows(tmp_path: Path) -> None:
    path = tmp_path / "de.csv"
    path.write_text(
        "disease_id,local_name,local_code,category,aliases\n"
        ",Orthopocken,orthopocken,National weekly,\n"
        "D065,Mpox,mpox,National weekly,Monkeypox\n",
        encoding="utf-8",
    )
    mapper = DiseaseMapper.__new__(DiseaseMapper)
    mapper.country_code = "de"
    mapper.mapping_file = path
    mapper.local_mappings = {}
    mapper.local_to_id = {}
    mapper.id_to_local = {}

    mapper._load_local_mappings()

    assert mapper.local_to_id == {"Mpox": "D065", "Monkeypox": "D065"}
    assert "nan" not in mapper.id_to_local
