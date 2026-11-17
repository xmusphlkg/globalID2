"""Reviewed annual source semantics for WHO GHO and SPF overseas onboarding.

This module describes identities, not statistical conversions. Callers must
retain the metric, dimensions, time basis and original observations. In
particular a disease mapping never authorizes adding totals and components.
"""
from __future__ import annotations

import hashlib
import json

from src.data.crawlers.my_who_annual import INDICATORS
from src.data.crawlers.spf_overseas import DATASETS


WHO_TARGETS = {
    "CHOLERA_0000000001": "D002",
    "CHOLERA_0000000002": "D002",
    "WHS3_41": "D029",
    "WHS3_42": "D020",
    "WHS3_43": "D028",
    "WHS3_45": "D042",
    "WHS3_46": "D120",
    "WHS3_47": "D235",
    "WHS3_49": "D013",
    "WHS3_51": "D014",
    "WHS3_53": "D039",
    "WHS3_55": "D168",
    "WHS3_56": "D030",
    "WHS3_57": "D040",
    "WHS3_62": "D017",
    "TB_c_newinc": "D025",
    "MALARIA_CONF_CASES": "D037",
    "MALARIA001": "D037",
}
SPF_TARGETS = {
    "hepatitis_a": "D007",
    "legionellosis": "D107",
    "measles": "D017",
    "tuberculosis": "D025",
    "invasive_meningococcal": "D110",
}


def who_mapping(source_code: str, label: str, definition_version: str) -> dict:
    """Return a mapping only for an unchanged, reviewed WHO indicator."""
    if source_code not in WHO_TARGETS or label != INDICATORS[source_code][0]:
        raise ValueError("WHO indicator identity/label is outside the reviewed contract")
    if definition_version not in {
        "who_gho_country_reported_annual_v1", "my_who_gho_country_reported_annual_v1"
    }:
        raise ValueError("WHO source definition version needs review")
    return {
        "target_code": WHO_TARGETS[source_code],
        "mapping_relation": "narrower" if source_code in {
            "TB_c_newinc", "MALARIA_CONF_CASES", "WHS3_45"
        } else "exact",
        "projection_policy": "canonical",
        "comparability": "conditional",
        "aggregation_policy": "non_additive",
        "metric_type": INDICATORS[source_code][1],
        "temporal_granularity": "annual",
        "reporting_basis": "who_country_reported_notifications",
        "total_dimensions": {"who_indicator": source_code},
        "notes": (
            "WHO GHO country-reported annual count; preserve indicator, measure, country, "
            "closed calendar year and revisions. No cross-indicator sum; cases and deaths "
            "remain distinct; total/neonatal tetanus and rubella/CRS remain distinct. "
            "Meningitis is all reported etiologies, not bacterial or meningococcal-only. "
            "New leprosy, new/relapse TB and confirmed malaria retain their narrower scope. "
            "Missing/suppressed values remain unknown. Mapping does not imply national "
            "primary-feed completeness or release approval. https://www.who.int/data/gho"
        ),
    }


def spf_mapping(source_code: str, label: str, definition_version: str) -> dict:
    """Verify hashed strata and admit only published totals to canonical curves."""
    base, _, suffix = source_code.partition("__")
    if base not in SPF_TARGETS:
        raise ValueError("SPF disease identity needs review")
    dataset_id, spec = next((key, value) for key, value in DATASETS.items() if value.code == base)
    label_prefix = spec.title + ("; " if spec.strata else "")
    if not label.startswith(label_prefix):
        raise ValueError("SPF disease label needs review")
    strata = {}
    if spec.strata:
        components = label[len(label_prefix):].split("; ")
        if len(components) != len(spec.strata):
            raise ValueError("SPF stratum labels need review")
        for key, component in zip(spec.strata, components):
            prefix = key + ": "
            if not component.startswith(prefix) or not component[len(prefix):]:
                raise ValueError("SPF stratum identity needs review")
            strata[key] = component[len(prefix):]
        digest = hashlib.sha256(json.dumps(strata, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        if suffix != digest:
            raise ValueError("SPF source code does not match retained strata")
    elif label != spec.title or suffix:
        raise ValueError("SPF unstratified source identity needs review")
    bases = {
        "hepatitis_a": {"residence_department"},
        "legionellosis": {"residence_department"},
        "measles": {"residence_or_notifier_department_rash_year"},
        "tuberculosis": {"notification_department", "residence_department"},
        "invasive_meningococcal": {"residence_or_notifier_region"},
    }
    matches = [basis for basis in bases[base] if definition_version == f"spf_odisse_{base}_{basis}_v1"]
    if len(matches) != 1:
        raise ValueError("SPF definition/geographic basis needs review")
    total = not strata or strata == {"mdo_cl_age_rougeole": "Tous âges"} or strata == {
        "classe_age": "Tous âges", "meningocoque": "Tous types"
    }
    return {
        "target_code": SPF_TARGETS[base],
        "mapping_relation": "exact" if total else "narrower",
        "projection_policy": "canonical" if total else "discovery_only",
        "comparability": "conditional",
        "aggregation_policy": "non_additive",
        "metric_type": "case_notifications",
        "temporal_granularity": "annual",
        "reporting_basis": matches[0],
        "total_dimensions": {"dataset": dataset_id, **strata} if total else None,
        "notes": (
            "SPF official annual territory notifications. Preserve source-reported age/serogroup "
            "totals and components separately; never sum them. Only the published all-age/all-type "
            "total is eligible for a territory curve. Measles retains rash-year timing; TB "
            "notification department through 2018 differs from residence department from 2019. "
            "No France-national rollup; rates/population never replace cases; missing is unknown. "
            "Licence ouverte 2.0: attribute Santé publique France, dataset and extraction date. "
            f"https://odisse.santepubliquefrance.fr/explore/dataset/{dataset_id}/"
        ),
    }
