"""Offline acceptance: real manifests -> organize -> static facts -> export guard.

No database writes. Export period fields are derived from the captured source
rows, matching the required production persistence contract.
"""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def artifacts(path):
    return [SimpleNamespace(**a) for a in json.loads(Path(path).read_bytes())]


def main(args):
    from src.data.crawlers.ecdc_respiratory import COUNTRY_NAMES
    from src.data.processors.ecdc_respiratory import ECDCRespiratoryUpdater
    from src.data.processors.eu_monthly import EUMonthlyUpdater
    from src.services.ingestion.countries import ecdc_respiratory, eu_monthly
    from src.services.europe_high_frequency_review import reviewed_europe_high_frequency_record
    from src.ontology.disease import DiseaseOntology
    from src.data.storage.series_observation_store import SeriesObservationStore

    from src.ontology import read_ontology_document
    document = read_ontology_document(ROOT / "configs/disease_ontology.json")
    patch = json.loads((ROOT / "configs/high_frequency_europe_ontology.json").read_bytes())
    for key, rows in patch.items():
        index = {r["id"]: r for r in document[key]}; index.update({r["id"]: r for r in rows})
        document[key] = list(index.values())
    store = SeriesObservationStore(DiseaseOntology.from_dict(document))
    series = {s["id"]: s for s in patch["source_series"]}
    receipts = []
    shared = artifacts(args.ecdc_manifest)
    jobs = [(cc, ecdc_respiratory, ECDCRespiratoryUpdater(country_code=cc), shared) for cc in COUNTRY_NAMES]
    jobs += [(cc, eu_monthly, EUMonthlyUpdater(country_code=cc), artifacts(path)) for cc, path in [("CZ", args.cz_manifest), ("EE", args.ee_manifest)]]
    for cc, module, updater, raw in jobs:
        fetched = module.organize(raw, updater=updater, source=updater.source_scope)
        built = store.build_observations(fetched.rows, cc, source_id=updater.ontology_source_id)
        if built.skipped_unmatched or built.skipped_ambiguous or built.skipped_invalid or len(built.observations) != len(fetched.rows):
            raise ValueError(f"Static registry failed {cc}/{updater.source_scope}: {built}")
        reviewed, unmapped, reviewed_group, source_only = 0, 0, 0, 0
        for obs in built.observations:
            s = series[obs["series_code"]]; row = obs["raw_data"]
            exported = {"date": obs["time"].date().isoformat(), "period_start": row["PeriodStart"],
                "period_end_exclusive": (date.fromisoformat(row["PeriodEnd"]) + timedelta(days=1)).isoformat(),
                "disease_id": s.get("concept_id"), "source_system": s["source_id"], "source_series_code": s["id"],
                "source_label": s["local_labels"][0], "reviewed_source_code": row["SourceDiseaseCode"], "reviewed_source_label": row["RawDiseaseLabel"],
                "definition_version": s["definition_version"], "metric_type": s["measure"], "reporting_basis": s["reporting_basis"],
                "temporal_granularity": s["frequency"], "series_unit": s["unit"], "observation_unit": obs["unit"],
                "mapping_relation": s["mapping_relation"], "comparability": s["comparability"], "aggregation_policy": s["aggregation_policy"],
                "geography_key": obs["geography_key"], "dimensions": obs["dimensions"]}
            result = reviewed_europe_high_frequency_record(exported, cc)
            if "group_id" in s:
                if s["mapping_relation"] == "unmapped":
                    unmapped += 1
                else:
                    reviewed_group += 1
                if result is not None:
                    raise ValueError("Unmapped category entered public concept path")
                continue
            if result is None:
                raise ValueError(f"Real source cell rejected by publication contract: {exported}")
            reviewed += 1
            if result.get("projection_policy") == "source_observations_only":
                source_only += 1
                if result.get("projection_metric_type") == "case_notifications":
                    raise ValueError("Source-only statistic acquired patient case projection")
        receipt = {"country_code": cc, "scope": updater.source_scope, "source_rows": len(fetched.rows), "static_observations": len(built.observations),
            "reviewed_public_contract_rows": reviewed, "unmapped_preserved_rows": unmapped, "reviewed_group_preserved_rows": reviewed_group, "source_observations_only_rows": source_only,
            "first_period": min(r["PeriodStart"] for r in fetched.rows), "latest_period_end": max(r["PeriodEnd"] for r in fetched.rows)}
        receipts.append(receipt); print(json.dumps(receipt), flush=True)
    target = ROOT / "data/current/europe_high_frequency/staged_registry_publication_acceptance.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"database_writes": False, "manifests": {"ecdc": args.ecdc_manifest, "cz": args.cz_manifest, "ee": args.ee_manifest}, "receipts": receipts}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ecdc-manifest", required=True)
    parser.add_argument("--cz-manifest", required=True)
    parser.add_argument("--ee-manifest", required=True)
    main(parser.parse_args())
