"""Prepare pinned, attributed ADM1 geometry; never infer geography from positions.

Run with venv/bin/python scripts/prepare_subdivision_boundaries.py.
Published source coordinates are retained. Names are joined by ISO code, except
the CHN source whose shapeISO column contains CHN for every feature: that source
uses the explicit reviewed name crosswalk below.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen
from shapely.geometry import shape, mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.core.country_library import get_country_bootstrap_config
DESTINATION = ROOT / "external-data/subdivision-boundaries"
REVISION = "9469f09"
CN_CODES = {
    "Hainan Province": "HI", "Guangxi Zhuang Autonomous Region": "GX",
    "Fujian Province": "FJ", "Yunnan Province": "YN", "Guizhou Province": "GZ",
    "Jiangxi Province": "JX", "Hunan Province": "HN", "Zhejiang Province": "ZJ",
    "Shanghai Municipality": "SH", "Chongqing Municipality": "CQ", "Hubei Province": "HB",
    "Sichuan Province": "SC", "Anhui Province": "AH", "Jiangsu Province": "JS",
    "Henan Province": "HA", "Tibet Autonomous Region": "XZ", "Shandong Province": "SD",
    "Qinghai Province": "QH", "Ningxia Ningxia Hui Autonomous Region": "NX",
    "Shaanxi Province": "SN", "Tianjin Municipality": "TJ", "Shanxi Province": "SX",
    "Beijing Municipality": "BJ", "Gansu Province": "GS", "Hebei Province": "HE",
    "Liaoning Province": "LN", "Jilin Province": "JL", "Xinjiang Uyghur Autonomous Region": "XJ",
    "Inner Mongolia Autonomous Region": "NM", "Heilongjiang Province": "HL",
    # Source feature is labelled Guangzhou Province; shape is Guangdong.
    "Guangzhou Province": "GD",
}
SOURCES = {
    "CN": ("CHN", "CHN-ADM1-43563684", "geoBoundaries / Wikimedia Commons", "Public Domain", 31),
    "AU": ("AUS", "AUS-ADM1-82085004", "Australian Bureau of Statistics / geoBoundaries", "CC BY 4.0", 8),
    "BR": ("BRA", "BRA-ADM1-14911670", "geoBoundaries / Wikimedia Commons", "CC BY 2.5", 27),
    "US": ("USA", "USA-ADM1-66186276", "United States Census Bureau / MAF-TIGER / geoBoundaries", "Public Domain", 56),
    "JP": ("JPN", "JPN-ADM1-47310658", "OpenStreetMap / Wambacher / geoBoundaries", "ODbL 1.0", 47),
    "SE": ("SWE", "SWE-ADM1-68755315", "geoBoundaries / Erik Frohne", "CC BY 3.0", 21),
}


def prepare(countries: list[str] | None = None) -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    manifest_path = DESTINATION / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if countries and manifest_path.exists() else {"schema_version": 1, "countries": {}}
    for code, (iso3, boundary_id, provider, license_name, expected) in SOURCES.items():
        if countries and code not in countries:
            continue
        url = f"https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/{REVISION}/releaseData/gbOpen/{iso3}/ADM1/geoBoundaries-{iso3}-ADM1_simplified.geojson"
        body = urlopen(url, timeout=60).read()
        geometry = json.loads(body)
        metadata = {}
        if code in {"US", "JP", "SE"}:
            metadata_url = url.replace("_simplified.geojson", "-metaData.json")
            metadata_body = urlopen(metadata_url, timeout=60).read()
            metadata = json.loads(metadata_body)
            if metadata["boundaryID"] != boundary_id or int(metadata["admUnitCount"]) != expected:
                raise ValueError(f"Boundary source metadata changed: {code}")
        features = []
        for feature in geometry["features"]:
            properties = feature["properties"]
            key = properties.get("shapeISO")
            if code == "CN":
                suffix = CN_CODES.get(properties["shapeName"])
                if not suffix:
                    continue  # This monitoring footprint covers 31 mainland provinces.
                key = f"CN-{suffix}"
            if code == "US" and key == "SU-SD" and properties["shapeName"] == "South Dakota":
                key = "US-SD"  # Pinned source typo; geography verified by the named state.
            if code == "AU" and not str(key).startswith("AU-"):
                key = "AU-OTHER"
            if key != "AU-OTHER" and get_country_bootstrap_config(key).get("parent_country_code") != code:
                raise ValueError(f"Unregistered boundary geography: {key}")
            feature["properties"] = {"name": key, "code": key, "source_name": properties["shapeName"], "source_id": properties["shapeID"]}
            original = shape(feature["geometry"])
            simplified = original.simplify(0.015, preserve_topology=True)
            if not simplified.is_valid:
                simplified = original
            if not simplified.is_valid:
                raise ValueError(f"Invalid source geometry: {key}")
            feature["geometry"] = mapping(simplified)
            features.append(feature)
        keys = [f["properties"]["code"] for f in features]
        if len(set(keys)) != len(keys) or len([k for k in keys if k != "AU-OTHER"]) != expected:
            raise ValueError(f"Unexpected {code} geometry coverage")
        document = {"type": "FeatureCollection", "features": features}
        output = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
        (DESTINATION / f"{code.lower()}.json").write_bytes(output)
        manifest["countries"][code] = {
            "file": f"{code.lower()}.json", "version": boundary_id,
            "source": provider, "source_url": url, "license": license_name,
            "license_url": "https://www.geoboundaries.org/index.html#citation",
            "input_sha256": hashlib.sha256(body).hexdigest(),
            "sha256": hashlib.sha256(output).hexdigest(), "codes": keys,
            "geography_level": "ADM1", "expected_regions": expected,
            "transform": "Topology-preserving 0.015 degree simplification; explicit ISO/name crosswalk; CN monitoring footprint restricted to 31 mainland provinces; US South Dakota source ISO typo SU-SD corrected to US-SD.",
            "historical_boundary_policy": "Current monitoring units; historical boundary changes are not reconstructed.",
        }
        if metadata:
            manifest["countries"][code].update({
                "metadata_url": metadata_url,
                "metadata_sha256": hashlib.sha256(metadata_body).hexdigest(),
                "source_boundary_year": metadata["boundaryYear"],
                "source_license": metadata["boundaryLicense"],
                "license_url": {"US": "https://www.census.gov/about/policies/copyright.html", "JP": "https://opendatacommons.org/licenses/odbl/1-0/", "SE": "https://creativecommons.org/licenses/by/3.0/"}[code],
            })
        print(code, len(features), len(output))
    (DESTINATION / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--countries", nargs="+", choices=list(SOURCES))
    prepare(parser.parse_args().countries)
