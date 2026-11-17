#!/usr/bin/env python3
"""Install Colombia's reviewed typed contracts in local configuration only."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ontology import read_ontology_document, write_ontology_document
from src.data.crawlers.co_bes_weekly import ingestion_rows
from src.services.co_surveillance_review import TARGETS
from src.services.global_high_frequency_review import FLUNET_TARGETS


def configure():
    directory = ROOT / "data/current/co"
    with (directory / "bes-weekly-notifications-preview/weekly_notifications.csv").open() as stream:
        points = list(csv.DictReader(stream))
    for point in points:
        point["value"] = int(point["value"]) if point["value"] else None
        for key in ("source_epi_year", "source_epi_week", "page"):
            point[key] = int(point[key])
    weekly = ingestion_rows(points)
    with (directory / "who_flunet_weekly.csv").open() as stream:
        flu = list(csv.DictReader(stream))
    contracts = {}
    for source_id, rows in (("SRC_CO_INS_BES", weekly), ("SRC_CO_WHO_FLUNET", flu)):
        for row in rows:
            contracts.setdefault((source_id, row["SourceDiseaseCode"]), row)
    ontology_path = ROOT / "configs/disease_ontology.json"
    ontology = read_ontology_document(ontology_path)
    if not any(s["id"] == "SRC_CO_WHO_FLUNET" for s in ontology["sources"]):
        ontology["sources"].append({"id": "SRC_CO_WHO_FLUNET", "country_code": "CO",
            "labels": {"en": "Colombia WHO FluNet Weekly Respiratory Laboratory Surveillance"},
            "legacy_data_sources": [flu[0]["Source"]]})
    series_by_id = {s["id"]: s for s in ontology["source_series"]}
    availability = {s["id"]: s for s in ontology["availability"]}
    mappings = []
    for (source_id, code), row in sorted(contracts.items()):
        is_bes = source_id == "SRC_CO_INS_BES"
        target = TARGETS.get(code.removeprefix("bes_weekly_")) if is_bes else FLUNET_TARGETS.get(code)
        # Other respiratory viruses are retained as native holding facts until
        # their source definitions and ontology targets are separately reviewed.
        if target is None:
            continue
        series_id = f"SER_CO_{'BES_WEEKLY' if is_bes else 'FLUNET'}_{code}"
        note = ("Notifications in the analyzed INS week excluding discarded records; provisional, not onset incidence. "
                "Source year/week and verified Sunday-Saturday boundaries retained. Never derive from annex cumulative or comparisons."
                if is_bes else "Laboratory detections in the reporting network; origins and influenza totals/subtypes overlap. "
                "Never interpret as national population cases or sum origins/subtypes.")
        spec = {"id": series_id, "source_id": source_id, "concept_id": target,
            "local_codes": [code], "local_labels": [row["RawDiseaseLabel"]],
            "geography_key": "country:CO:national", "frequency": "weekly", "measure": row["Measure"],
            "reporting_basis": row["ReportingBasis"], "time_basis": row["TimeBasis"],
            "unit": "count", "definition_version": row["DefinitionVersion"],
            "mapping_relation": "narrower", "comparability": "conditional", "aggregation_policy": "non_additive",
            "missing_value_policy": "missing_is_unknown", "case_definition": note,
            "case_definition_uri": row["SourceURL"] if not is_bes else "https://www.ins.gov.co/buscador-eventos/Paginas/Vista-Boletin-Epidemilogico.aspx",
            "facet_tags": {}, "status": "active", "rollup_policy": "no_auto_rollup"}
        if not is_bes or row["Measure"] == "death_notifications":
            spec["projection_policy"] = "source_observations_only"
        series_by_id[series_id] = spec
        availability["AV_" + series_id] = {"id": "AV_" + series_id, "source_id": source_id,
            "target_ref": {"kind": "concept", "id": target}, "series_id": series_id,
            "status": "available", "reason_code": "verified_official_originals_typed_contract",
            "missing_value_policy": "missing_is_unknown"}
        mappings.append({"country_code": "CO", "disease_id": target, "local_name": row["RawDiseaseLabel"], "local_code": code,
            "source_id": source_id, "mapping_scope": "source_category_dimension", "target_kind": "concept",
            "target_code": target, "mapping_relation": "narrower", "comparability": "conditional",
            "projection_policy": "canonical", "aggregation_policy": "non_additive",
            "definition_version": row["DefinitionVersion"], "notes": note})
    ontology["source_series"] = list(series_by_id.values())
    ontology["availability"] = list(availability.values())
    write_ontology_document(ontology_path, ontology)
    mapping_path = ROOT / "configs/mapping/reviewed/co.csv"
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if mapping_path.exists():
        with mapping_path.open(newline="") as stream:
            existing = list(csv.DictReader(stream))
    identities = {(m["source_id"], m["local_code"], m["definition_version"]): m for m in existing}
    for mapping in mappings:
        identities[(mapping["source_id"], mapping["local_code"], mapping["definition_version"])] = mapping
    combined = list(identities.values())
    with mapping_path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in combined for k in row)))
        writer.writeheader()
        writer.writerows(combined)
    bootstrap_path = ROOT / "configs/country_bootstrap.json"
    bootstrap = json.loads(bootstrap_path.read_text())
    entry = bootstrap["CO"]
    entry["public_source_notes"] = (
        "No same-structure national weekly open table exists for 2025–2026. Municipality products cover "
        "2007–2022 and 2022–2024; selected BES weekly notifications, BES YTD snapshots, annual microdata "
        "and WHO laboratory detections remain independent, with different definitions and calendars.")
    entry["notes"] = (
        "No same-structure national weekly table exists for 2025–2026. "
        "Keep BES notifications, YTD snapshots, WHO laboratory detections and annual microdata independent; "
        "overlapping periods and strata are not additive.")
    crawler = entry["crawler_config"]
    crawler["preferred_source"] = "co_ins_bes_weekly"
    crawler["sources"] = list(dict.fromkeys([*crawler["sources"], "who_flunet_weekly"]))
    crawler["source_policies"]["who_flunet_weekly"] = {
        "full_history_start_year": 2025, "public_release_enabled": True,
        "metric_contract": "laboratory_detections", "supports_fill_missing": False,
        "dynamic_revision_enabled": True, "cadence": "weekly"}
    crawler["source_policies"]["co_ins_bes_weekly"].update(
        public_release_enabled=True, observation_kind="typed_weekly_notifications_and_private_ytd",
        publication_rule="Only reviewed bes_weekly_* definitions; annex YTD remains private and non-projectable")
    crawler["high_frequency_strategy"]["status"] = "typed_contracts_active"
    entry["public_source_systems"] = list(dict.fromkeys([*entry.get("public_source_systems", []), "SRC_CO_INS_BES", "SRC_CO_WHO_FLUNET"]))
    entry["public_source_scopes"] = list(dict.fromkeys([*entry.get("public_source_scopes", []), "who_flunet_weekly"]))
    entry["public_source_details"]["who_flunet_weekly"] = {
        "label": "WHO FluNet Colombia weekly laboratory detections", "label_zh": "WHO FluNet 哥伦比亚实验室周检出数",
        "url": "https://www.who.int/tools/flunet", "cadence": "weekly", "source_id": "SRC_CO_WHO_FLUNET",
        "description": "Weekly respiratory laboratory detections; sentinel and nonsentinel retained separately, not national incident patient cases.",
        "description_zh": "呼吸道实验室每周检出数；哨点与非哨点分别展示，不能解释为全国新发病例。"}
    entry["public_source_details"]["co_ins_bes_weekly"].update(
        description="Reviewed body-table weekly notifications only. Annex year-to-date snapshots are separate and never weekly incidence; early issues without an explicit weekly definition remain gaps.",
        description_zh="仅展示正文中明确单周定义的通知数。附录 Acumulado 累计数单独保存，不作周发病数；早期缺少明确周定义的报告保持缺口。")
    crawler["source_policies"]["co_ins_sivigila_microdatos"].update(database_import_enabled=True)
    bootstrap_path.write_text(json.dumps(bootstrap, ensure_ascii=False, indent=2) + "\n")
    return {"series": len(mappings), "weekly_points": len(weekly), "flunet_cells": len(flu)}


if __name__ == "__main__":
    print(json.dumps(configure()))
