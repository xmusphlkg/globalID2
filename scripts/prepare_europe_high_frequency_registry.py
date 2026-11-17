"""Prepare independent Europe registry and reviewed mapping patches from captures.

This command never mutates the shared ontology, reviewed CSVs or database.
The explicit ICD dictionary below was reviewed against the actual Czech and
Estonian report labels; unlisted categories remain source-native and unmapped.
"""
from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Exact source ICD strings, including native footnotes, distinguish totals
# from named subsets. No prefix matching or inferred ICD range expansion.
CZ = {
    "A00": "D002", "A01": "D026", "A02": "D114", "A03": "D105",
    "A04.3": "D115", "A04.5": "D092", "z toho A05.1": "D091",
    "A06": "D165", "A07.1": "D099", "A07.2": "D096", "A09": "D156",
    "A21": "D123", "A23": "D032", "A27": "D035", "A32": "D108",
    "A35": "D120", "A36": "D029", "A37.0": "D028", "A38": "D031",
    "A39": "D110", "A48.1": "D107", "A56": "D094", "A69.2": "D109",
    "A70": "D112", "A78": "D113", "A79": "D159",
    "z toho A79.8": "D098", "A81.0": "D144", "A84.1": "D205",
    "A87": "D134", "A92.0": "D052", "A92.3": "D128", "A92.5": "D051",
    "A95": "D059", "A97": "D021", "z toho A97.2": "D021", "A98.5": "D018",
    "B01": "D054", "B02": "D063", "B04": "D065", "B05": "D017", "B06": "D040",
    "z toho B08.3": "D138", "B15": "D007", "B16": "D068",
    "B17.1, B18.2": "D009", "B17.2": "D011", "B18.0, B18.1": "D208",
    "B26": "D039", "B50-B54": "D037", "B55": "D082", "B58": "D085",
    "B65": "D036", "B67": "D045", "B75": "D122", "B97.2": "D004",
    "G00 ‡‡)": "D135", "IPO *)": "D106", "IHO **)": "D100",
}
EE = {
    "A01.0": "D124", "A01.1–A01.4": "D234", "A02": "D114", "A03": "D105",
    "A04.3": "D115", "A04.5": "D092", "A04.6": "D155", "A05.1": "D091",
    "A06": "D165", "A07.1": "D099", "A07.2": "D096", "A08.0": "D199",
    "A21": "D123", "A22": "D023", "A23": "D032", "A27": "D035", "A30": "D042",
    "A32;P37.2": "D108", "P37.2": "D108", "A33–A35": "D120", "A36": "D029",
    "A37.0": "D028", "A38": "D031", "A39": "D110", "A39.0": "D110", "A39.2": "D110",
    "G00.1": "D106", "A40.3": "D106", "A41.3;G00.0;J14;A49.2": "D100",
    "A41.3": "D100", "G00.0": "D100", "A48.1": "D107", "A50–A53": "D034",
    "A50": "D167", "A54": "D150", "A69.2": "D109", "A70": "D112", "A79.8": "D098",
    "A80": "D013", "A81.0": "D144", "A82": "D019", "A84": "D205",
    "A85;A87": "D240", "A98.5": "D018", "A90,A91": "D021", "A92.0": "D052",
    "B01": "D054", "B05": "D017", "B06,P35.0": "G_RUBELLA_SPECTRUM", "P35.0": "D168",
    "B15–B19": "D006", "B15": "D007", "B16": "D068", "B17.1": "D210",
    "B17.2": "D011", "B18.0,B18.1": "D208", "B18.2": "D211",
    "Z21;B23.0;B23.1": "D162", "B26": "D039", "B50–B54": "D037",
    "P37.1": "D085", "B67": "D045", "B75": "D122", "J09–J11": "D038", "B04": "D065",
}
# These narrower categories must not compete with their source-reported totals.
NARROWER = {("CZ", x) for x in ["A35", "A56", "A79", "z toho A79.8", "A84.1", "A87", "z toho A97.2", "IHO **)"]} | {
    ("EE", x) for x in ["A01.1–A01.4", "A04.6", "P37.2", "A39.0", "A39.2", "G00.1", "A40.3", "A41.3", "G00.0", "A79.8", "P37.1", "Z21;B23.0;B23.1"]
}
DISCOVERY_ONLY = {("CZ", "z toho A97.2")} | {("EE", x) for x in ["P37.2", "A39.0", "A39.2", "G00.1", "A40.3", "A41.3", "G00.0", "P37.1"]}


def main():
    from src.data.crawlers.ecdc_respiratory import COUNTRY_NAMES, DATASETS, parse_csv
    from src.data.crawlers.eu_monthly import CONTRACTS
    patch = {"sources": [], "source_series": [], "availability": []}
    mappings = {}

    def append(cc, source, row, target, *, relation="exact", discovery=False):
        local = row["SourceDiseaseCode"]
        sid = "SER_" + cc + "_" + ("ECDC_RESP_" if local.startswith("resp_") else "NATIONAL_MONTHLY_") + local.split("_", 1)[1].upper()
        known_group = bool(target and target.startswith("G_"))
        target_key = "concept_id" if target and not known_group else "group_id"
        target = target or "G_UNMAPPED_SOURCE_CATEGORIES"
        mapped = target_key == "concept_id"
        comparison = "conditional" if mapped else "not_comparable"
        relation = relation if mapped else "aggregate" if known_group else "unmapped"
        series = {"id": sid, "source_id": source, target_key: target,
            "local_codes": [local], "local_labels": [row["RawDiseaseLabel"]],
            "frequency": row["Frequency"], "measure": row["Measure"], "unit": row["Unit"],
            "reporting_basis": row["ReportingBasis"], "time_basis": row["TimeBasis"],
            "definition_version": row["DefinitionVersion"], "mapping_relation": relation,
            "comparability": comparison, "aggregation_policy": "non_additive", "missing_value_policy": "missing_is_unknown",
            "case_definition": "Source-reported laboratory surveillance statistic; specimens/tests/detections are not unique national patient cases. No aggregation of surveillance origins, ages, virus types or subtypes." if local.startswith("resp_") else "Source-reported national single-calendar-month notifications from the edition-year column; native category and subsets remain distinct. Not year-to-date.",
            "projection_policy": "source_observations_only" if discovery or not mapped else "priority_overlay",
            "projection_priority": 200, "status": "active", "rollup_policy": "no_auto_rollup"}
        patch["source_series"].append(series)
        patch["availability"].append({"id": "AV_" + sid[4:], "source_id": source,
            "target_ref": {"kind": "concept" if mapped else "group", "id": target}, "series_id": sid,
            "status": "upstream_available_ingestion_pending" if mapped or known_group else "mapping_missing",
            "reason_code": "official_numeric_cells_captured_and_parsed_import_pending" if mapped or known_group else "source_category_preserved_no_reviewed_exact_concept",
            "missing_value_policy": "missing_is_unknown"})
        notes = ("Reviewed source-native respiratory laboratory dictionary; preserve each dataset, surveillance origin, pathogen type/subtype, indicator and age separately. Never project to patient case counts." if local.startswith("resp_") else "Reviewed actual national single-month PDF ICD/label pair: " + row["SourceICDCode"] + "; no summing parent/subset categories; blank is unknown.")
        mapping = {"disease_id": target if mapped else "", "local_name": row["RawDiseaseLabel"], "local_code": local,
            "category": "Weekly laboratory surveillance" if local.startswith("resp_") else "National monthly notifications", "aliases": "", "notes": notes,
            "data_source": row["Source"], "source_id": source, "series_id": sid, "mapping_scope": "source_category_dimension",
            "target_kind": "concept" if mapped else "group", "target_code": target, "mapping_relation": relation,
            "comparability": comparison, "projection_policy": ("canonical" if local.startswith("resp_") or not discovery else "discovery_only") if mapped else "no_projection",
            "aggregation_policy": "non_additive", "definition_version": row["DefinitionVersion"]}
        mappings.setdefault(f"configs/mapping/reviewed/{cc.lower()}.csv", []).append(mapping)

    payloads = {p: (ROOT / "data/raw/europe_frequency_research/ecdc" / p).read_bytes() for p in DATASETS}
    for cc, name in COUNTRY_NAMES.items():
        source = f"SRC_{cc}_ECDC_RESPIRATORY"
        patch["sources"].append({"id": source, "country_code": cc, "labels": {"en": name + " ECDC Weekly Respiratory Laboratory Surveillance"}, "legacy_data_sources": [name + " ECDC Weekly Respiratory Laboratory Surveillance"]})
        categories = {}
        for dataset, payload in payloads.items():
            rows, _ = parse_csv(payload, country_code=cc, dataset=dataset, as_of=date(2026, 11, 17))
            for row in rows:
                categories[row["SourceDiseaseCode"]] = row
        for row in sorted(categories.values(), key=lambda r: r["SourceDiseaseCode"]):
            dims = json.loads(row["Dimensions"])
            target = {"Influenza": "D038", "RSV": "D142", "SARS-CoV-2": "D004"}[dims["pathogen"]]
            append(cc, source, row, target, relation="exact" if dims["pathogentype"] == dims["pathogensubtype"] == "NA" else "narrower", discovery=True)
    for cc, contract in CONTRACTS.items():
        patch["sources"].append({"id": contract["source_id"], "country_code": cc, "labels": {"en": contract["name"]}, "legacy_data_sources": [contract["name"]]})
        rows = list(csv.DictReader((ROOT / f"data/current/{cc.lower()}/{contract['scope']}.csv").open()))
        categories = {r["SourceDiseaseCode"]: r for r in rows}
        dictionary = CZ if cc == "CZ" else EE
        absent = set(dictionary) - {r["SourceICDCode"] for r in categories.values()}
        if absent:
            raise ValueError(f"Reviewed source ICD strings absent from capture: {cc} {absent}")
        for row in sorted(categories.values(), key=lambda r: r["SourceDiseaseCode"]):
            key = (cc, row["SourceICDCode"])
            append(cc, contract["source_id"], row, dictionary.get(key[1]), relation="narrower" if key in NARROWER else "exact", discovery=key in DISCOVERY_ONLY)
    ontology = json.loads((ROOT / "configs/disease_ontology.json").read_bytes())
    active_concepts = {c["id"] for c in ontology["concepts"] if c["status"] == "active"}
    if any(s.get("concept_id") and s["concept_id"] not in active_concepts for s in patch["source_series"]):
        raise ValueError("New Europe mapping must target an active concept")
    for filename, value in [("high_frequency_europe_ontology.json", patch), ("high_frequency_europe_reviewed_mappings.json", mappings)]:
        path = ROOT / "configs" / filename
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"sources": len(patch["sources"]), "source_series": len(patch["source_series"]), "reviewed_rows": sum(map(len, mappings.values()))}))


if __name__ == "__main__":
    main()
