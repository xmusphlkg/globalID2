#!/usr/bin/env python3
"""Add verified high-frequency contracts without removing historical sources.

Writes only reviewed local configuration with --apply. Database imports and
publication have separate receipts and remain independently resumable.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.high_frequency_sources import HIGH_FREQUENCY_SOURCES, preferred_source
from src.core.country_library import get_country_profile


def publisher(spec):
    return {
        "ecdc_respiratory": "European Centre for Disease Prevention and Control (ECDC)",
        "who_flunet": "World Health Organization, Global Influenza Surveillance and Response System",
        "mx_high_frequency": "Secretaría de Salud, Dirección General de Epidemiología (DGE)",
        "ca_respiratory_weekly": "Public Health Agency of Canada (PHAC)",
        "spf_odisse_weekly": "Santé publique France",
    }.get(spec.module, "Státní zdravotní ústav (SZÚ)" if spec.country_code == "CZ" else "Terviseamet")


def source_details(spec):
    if spec.measurement == "case_notifications":
        en = "Source-published national incident notifications for the explicit reporting week or month. Cumulative totals, demographic strata and annual history remain separate. Missing cells remain unknown."
        zh = "按原表明确的报告周或月份展示全国新增报告数；累计数、人口分层与年度历史分别保留。缺失单元格保持未知。"
    elif spec.module == "spf_odisse_weekly":
        en = "Weekly syndromic healthcare activity rates by age and provider network. These are service-use rates, with source-specific denominators, not national patient counts or population incidence."
        zh = "按年龄及医疗服务网络分别展示综合征周度就诊指标率，保留各自分母；不能解释为全国患者数或人口发病率。"
    else:
        en = "Weekly respiratory laboratory surveillance. Detections, tests and positivity are separate measures; reporting networks, origins and subtypes overlap and must not be added or interpreted as national incident patient cases."
        zh = "呼吸道实验室周监测。检出数、检测数及阳性率分别展示，报告网络、来源和亚型可能重叠，不累加、不解释为全国新增患者数。"
    attribution = f"{publisher(spec)}. {spec.label_en}. Original source: {spec.url}. Source definitions, strata and periods retained."
    return {
        "source_id": spec.source_id, "label": spec.label_en,
        "publisher": publisher(spec), "url": spec.url, "type": "official_surveillance",
        "cadence": spec.frequency, "description": en, "description_zh": zh,
        "required_attribution": attribution,
        "role": "national_high_frequency" if spec.measurement == "case_notifications" else "high_frequency_supplement",
        "license": "Original source terms apply", "source_license_verified": False,
        "review_status": "source_contract_and_metrics_reviewed",
    }


def configure(document, channels):
    existing_channels = {(x["country_code"], x["scope"]): x for x in channels["channels"]}
    countries = sorted({s.country_code for s in HIGH_FREQUENCY_SOURCES.values()})
    for code in countries:
        entry = document.setdefault(code, {})
        crawler = entry.setdefault("crawler_config", {})
        specs = [s for s in HIGH_FREQUENCY_SOURCES.values() if s.country_code == code]
        new_source_ids = {s.source_id for s in specs}
        # An absent allow-list means all previously eligible sources. Creating
        # a list containing only the new feed would hide the old national data.
        previous_allowlist = entry.get("public_source_systems") or []
        restricted_sources = bool(set(previous_allowlist) - new_source_ids)
        if not restricted_sources:
            entry.pop("public_source_systems", None)
        preference = preferred_source(code)
        new_scopes = {s.scope for s in specs}
        legacy_scopes = [scope for scope in crawler.get("sources", []) if scope not in new_scopes]
        # Existing primary national collectors remain meaningful. Where only
        # an annual baseline existed, make the new explicit feed the preset.
        replace_preset = code in {"MX", "CZ", "EE"} or (
            "annual" in str(crawler.get("cadence", ""))
            and not any("weekly" in scope or "monthly" in scope for scope in legacy_scopes)
        )
        if replace_preset:
            crawler["preferred_source"] = preference.scope
        ordered = sorted(specs, key=lambda s: s.scope != preference.scope)
        crawler["sources"] = list(dict.fromkeys(
            [*(s.scope for s in ordered), *legacy_scopes] if replace_preset
            else [*legacy_scopes, *(s.scope for s in ordered)]
        ))
        for spec in specs:
            details = source_details(spec)
            policy = crawler.setdefault("source_policies", {}).setdefault(spec.scope, {})
            policy.update(public_release_enabled=True,
                          operator_authorized_public_release=True,
                          reuse_status="operator_authorized_public_release",
                          publisher=details["publisher"], required_attribution=details["required_attribution"],
                          cadence=spec.frequency, full_history_start_year=spec.start_year,
                          supports_fill_missing=False, metric_contract=spec.measurement,
                          source_update_cadence=f"{spec.frequency}_revisable",
                          supports_start_year=True, supports_current_month=False,
                          revision_window_unit="years", default_revision_window=3)
            public = entry.setdefault("public_source_details", {})
            public[spec.scope] = {**public.get(spec.scope, {}), **details}
            if restricted_sources:
                entry["public_source_systems"] = list(dict.fromkeys([
                    *entry.get("public_source_systems", []), spec.source_id,
                ]))
            key = code, spec.scope
            if key not in existing_channels:
                # MY already has its active 30-minute national-source job.
                # Its existing ID is retained rather than creating a duplicate.
                if key == ("MY", "who_flunet_weekly"):
                    continue
                channel = {
                    "country_code": code, "scope": spec.scope, "source_id": spec.source_id,
                    "publisher": details["publisher"], "official_url": spec.url,
                    "archive_url": spec.url, "discovery_urls": [spec.url],
                    "cadence": spec.frequency,
                    "refresh_strategy": "versioned_official_resource_full_replay" if spec.module in {"who_flunet", "ecdc_respiratory", "ca_respiratory_weekly", "spf_odisse_weekly"} else "full_live_directory_and_verified_reports",
                    "refresh_years": 3, "daily_time": "08:30",
                    "timezone": get_country_profile(code).timezone,
                }
                existing_channels[key] = channel
                channels["channels"].append(channel)
        old_public = [scope for scope in entry.get("public_source_scopes", legacy_scopes) if scope not in new_scopes]
        entry["public_source_scopes"] = list(dict.fromkeys(
            [*(s.scope for s in ordered), *old_public] if replace_preset
            else [*old_public, *(s.scope for s in ordered)]
        ))
        entry["high_frequency_reviewed_on"] = "2026-11-17"
        entry["public_source_notes"] = "High-frequency source measures and annual notification history are retained independently; overlapping networks, periods and strata are not additive."
    channels["verified_on"] = "2026-11-17"
    return countries


def merge_ontology(document, patches, *, replace_reviewed=False):
    counts = {}
    for key in ("sources", "source_series", "availability"):
        existing = {row["id"]: row for row in document[key]}
        added = 0
        for patch in patches:
            for row in patch.get(key, []):
                if row["id"] in existing:
                    if existing[row["id"]] != row and key != "sources":
                        reviewed_sources = {s.source_id for s in HIGH_FREQUENCY_SOURCES.values()} | {"SRC_AT_AGES_RADAR"}
                        if (not replace_reviewed or key not in {"source_series", "availability"}
                            or row.get("source_id") not in reviewed_sources
                            or row.get("source_id") != existing[row["id"]].get("source_id")):
                            raise ValueError(f"Conflicting existing ontology definition: {row['id']}")
                        existing[row["id"]].clear()
                        existing[row["id"]].update(row)
                    continue
                document[key].append(row)
                existing[row["id"]] = row
                added += 1
        counts[key] = added
    return counts


def merge_reviewed_mappings(patches):
    changes = {}
    for patch in patches:
        grouped = dict(patch.get("reviewed_mappings", {}))
        for key, additions in patch.items():
            if key.startswith("configs/mapping/reviewed/"):
                grouped.setdefault(Path(key).stem.upper(), []).extend(additions)
        for row in patch.get("rows", []):
            code = row["country_code"].upper()
            grouped.setdefault(code, []).append({k: v for k, v in row.items() if k != "country_code"})
        for code, additions in grouped.items():
            path = ROOT / "configs/mapping/reviewed" / f"{code.lower()}.csv"
            rows, fields = [], []
            if path.exists():
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream); fields = reader.fieldnames; rows = list(reader)
            fields = list(dict.fromkeys([*fields, *(k for row in additions for k in row)]))
            def identity(row):
                return row.get("source_id"), row.get("local_code"), row.get("definition_version") or "*"
            updates = {identity(row): row for row in additions}
            # Replacing an exact reviewed identity is deliberate: the prior
            # assertion remains in the immutable DB audit/release history.
            combined = [{**row, **updates.pop(identity(row), {})} for row in rows]
            combined.extend(updates.values())
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(combined)
            changes[code] = changes.get(code, 0) + len(additions)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--patch", type=Path, nargs="*", default=[])
    parser.add_argument("--replace-reviewed-definitions", action="store_true")
    args = parser.parse_args()
    bootstrap_path = ROOT / "configs/country_bootstrap.json"
    channels_path = ROOT / "configs/surveillance_channels.json"
    bootstrap = json.loads(bootstrap_path.read_text()); channels = json.loads(channels_path.read_text())
    countries = configure(bootstrap, channels)
    result = {"countries": countries, "source_contracts": len(HIGH_FREQUENCY_SOURCES), "applied": args.apply}
    if args.apply:
        for path, data in ((bootstrap_path, bootstrap), (channels_path, channels)):
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        if args.patch:
            patches = [json.loads(path.read_text()) for path in args.patch]
            ontology_path = ROOT / "configs/disease_ontology.json"
            ontology = json.loads(ontology_path.read_text())
            result["ontology"] = merge_ontology(ontology, patches, replace_reviewed=args.replace_reviewed_definitions)
            from src.ontology import DiseaseOntology
            DiseaseOntology.from_dict(ontology)
            ontology_path.write_text(json.dumps(ontology, ensure_ascii=False, indent=2) + "\n")
            result["reviewed_mappings"] = merge_reviewed_mappings(patches)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
