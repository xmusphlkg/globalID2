"""Publishable surveillance JSON, independent of the static-site build.

Private full projections are kept in ``state_dir`` for future scoped merges.
Only compact JSON, small summaries and immutable objects enter ``public_dir``.
The current pointer is written last and pins every browser request to one
complete generation. Coupled releases also freeze CSV/JSON/XLSX from the same
context; literature, reports and Astro are not involved.
"""

from __future__ import annotations

import hashlib
import calendar
import json
import math
import os
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

from src.services.data_release.artifact_index import ArtifactIndex
from .site_data_dependencies import RECEIPT_NAME, dependency_version, file_identity, save_receipt
from .site_data_views import build_country_source_series_data
from .site_data_writer import remove_stale_json_files, write_compact_json, write_pretty_json


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


COMPLETED_GENERATION = "completed-generation.json"


def _generation_version() -> str:
    return hashlib.sha256(dependency_version().encode() + Path(__file__).read_bytes()
                          + Path(__file__).with_name('home_summary.py').read_bytes()).hexdigest()


def completed_generation(state_dir: Path, public_dir: Path) -> dict | None:
    """Return an attested local baseline, rejecting partial or edited outputs."""
    try:
        receipt = _read(state_dir / COMPLETED_GENERATION)
        if receipt.get("schema_version") != 1 or receipt.get("projection_version") != _generation_version():
            return None
        current = (public_dir / "current.json").read_bytes()
        if hashlib.sha256(current).hexdigest() != receipt["manifest_sha256"]:
            return None
        manifest = json.loads(current)
        if manifest.get("schema_version") != 1 or manifest.get("release_id") != receipt.get("release_id"):
            return None
        for group, root in (("state_files", state_dir), ("public_files", public_dir)):
            inventory = receipt.get(group)
            if not isinstance(inventory, dict) or not inventory:
                return None
            for relative, identity in inventory.items():
                path = Path(relative)
                if path.is_absolute() or ".." in path.parts or not identity or file_identity(root / path) != identity:
                    return None
        return receipt
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        return None


def _complete_generation(state_dir: Path, public_dir: Path, logical: list[str], manifest: dict) -> None:
    state_names = ["meta.json", RECEIPT_NAME]
    state_names.extend(str(path.relative_to(state_dir)) for group in ("countries", "diseases") for path in sorted((state_dir / group).glob("*.json")))
    object_names = {f"objects/{entry['sha256']}.json" for entry in manifest["files"].values()}
    receipt = {
        "schema_version": 1, "projection_version": _generation_version(), "release_id": manifest["release_id"],
        "manifest_sha256": hashlib.sha256((public_dir / "current.json").read_bytes()).hexdigest(),
        "state_files": {name: file_identity(state_dir / name) for name in state_names},
        "public_files": {name: file_identity(public_dir / name) for name in sorted({*logical, "current.json", *object_names})},
    }
    if any(value is None for group in ("state_files", "public_files") for value in receipt[group].values()):
        raise ValueError("Surveillance generation lost an artifact before completion")
    write_compact_json(state_dir / COMPLETED_GENERATION, receipt)


def _last(values: list | None):
    return next((value for value in reversed(values or []) if value is not None), None)


def _series_summary(series: dict) -> dict:
    dates = series.get("dates") or []
    cases = series.get("cases") or []
    index = next((index for index in range(min(len(dates), len(cases)) - 1, -1, -1) if cases[index] is not None), None)
    period_start = dates[index] if index is not None else None
    period_end = None
    granularities = series.get("point_granularities") or []
    granularity = (granularities[index] if index is not None and index < len(granularities) else None) or series.get("period_granularity")
    if period_start:
        try:
            start = date.fromisoformat(str(period_start)[:10])
            months = {"monthly": 1, "quarterly": 3, "annual": 12, "yearly": 12}.get(str(granularity).lower())
            if months:
                ordinal = start.year * 12 + start.month - 1 + months
                year, month = divmod(ordinal, 12)
                period_end = date(year, month + 1, min(start.day, calendar.monthrange(year, month + 1)[1])).isoformat()
            elif str(granularity).lower() in {"daily", "weekly"}:
                period_end = (start + timedelta(days=7 if str(granularity).lower() == "weekly" else 1)).isoformat()
        except (ValueError, TypeError, OverflowError):
            pass
    return {
        **{key: series.get(key) for key in ("disease_id", "name_en", "name_zh", "name_fr", "slug", "category", "total_cases", "total_deaths", "period_granularity", "data_layer", "coverage_status", "metric_type", "reporting_basis", "time_basis", "comparability", "definition_version", "provisional_from")},
        "latest_cases": series.get("latest_cases") if series.get("latest_cases") is not None else _last(cases),
        "latest_deaths": series.get("latest_deaths") if series.get("latest_deaths") is not None else _last(series.get("deaths")),
        "incidence_rate": _last(series.get("incidence_rates")), "mortality_rate": _last(series.get("mortality_rates")),
        "date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
        "latest_period_start": period_start, "latest_period_end_exclusive": period_end,
    }


def country_summary(data: dict, *, generated_at: str, download: dict | None = None) -> dict:
    """Small page facts; a browser never needs full curves to show counters."""
    fields = {
        key: value for key, value in data.items()
        if key in {"country_code", "country_name", "country_name_en", "country_name_zh", "country_name_fr", "total_cases", "total_deaths", "disease_count", "date_range", "frequency_meta", "comparison_basis", "incidence_rate_basis", "location_type", "parent_code", "source_info", "data_layer_summary", "country_brief_status", "country_brief_updated_at"}
        or key.startswith(("brief_", "interpretation_", "surveillance_system_", "reporting_cadence_", "limitations_", "source_summary_"))
    }
    fields.update({
        "schema_version": 1,
        "generated_at": generated_at,
        "diseases": [_series_summary(series) for series in (data.get("disease_series") or {}).values()],
        # Download generation has its own lifecycle. Preserve its own date
        # instead of relabelling an older archive as this data release.
        "download": download,
    })
    return fields


def disease_summary(data: dict, *, generated_at: str, countries: dict[str, dict], download: dict | None = None) -> dict:
    all_series = data.get("country_series") or {}
    # Page coverage counters use national jurisdictions. Subdivisions remain
    # available in compact chart data, without inflating the country count.
    series = {code: item for code, item in all_series.items()
              if countries.get(code, {}).get("location_type") != "subdivision"
              and not countries.get(code, {}).get("parent_code") and "-" not in code}
    dates = [date for item in series.values() for date in item.get("dates") or [] if date]
    reporting = [{
        "code": code,
        "name_en": countries.get(code, {}).get("name_en") or countries.get(code, {}).get("name") or code,
        "name_zh": countries.get(code, {}).get("name_zh") or code,
        "name_fr": countries.get(code, {}).get("name_fr"),
        "dates_tail": (series[code].get("dates") or [])[-12:],
        "cases_tail": (series[code].get("cases") or [])[-12:],
        **{key: series[code].get(key) for key in ("data_layer", "projection_policy", "loss_risk", "metric_type", "period_granularity", "comparability")},
    } for code in sorted(series)]
    preferred = max(series, key=lambda code: (len(series[code].get("dates") or []), code), default=None)
    return {
        "schema_version": 1,
        **{key: data.get(key) for key in ("disease_id", "name_en", "name_zh", "name_fr", "slug", "category", "source_info", "data_layer_summary")},
        "generated_at": generated_at,
        "country_count": len(series),
        "subdivision_count": len(all_series) - len(series),
        "date_range": {"start": min(dates) if dates else None, "end": max(dates) if dates else None},
        "reporting_countries": reporting,
        "preferred_country": preferred,
        "download": download,
    }


def home_influenza_series(data: dict) -> dict | None:
    """At most 242 original points, retaining bucket extrema and missing values."""
    series = (data.get("disease_series") or {}).get("D038")
    if not series:
        series = next((row for row in (data.get("disease_series") or {}).values()
                       if row.get("slug") == "influenza"), None)
    if not series:
        return None
    dates, values = series.get("dates") or [], series.get("cases") or []
    size = min(len(dates), len(values))
    if not size:
        return None
    valid = lambda index: isinstance(values[index], (int, float)) and not isinstance(values[index], bool) and math.isfinite(values[index]) and values[index] >= 0
    selected = {0, size - 1}
    width = max(1, math.ceil(size / 80))
    for start in range(0, size, width):
        indexes = list(range(start, min(size, start + width)))
        observed = [index for index in indexes if valid(index)]
        if observed:
            selected.update((min(observed, key=lambda index: values[index]), max(observed, key=lambda index: values[index])))
        missing = next((index for index in indexes if not valid(index)), None)
        if missing is not None:
            selected.add(missing)
    indexes = sorted(selected)
    return {"dates": [dates[index] for index in indexes],
            "cases": [values[index] if valid(index) else None for index in indexes],
            "date_range": {"start": dates[0], "end": dates[size - 1]},
            "period_granularity": series.get("period_granularity")}


def write_surveillance_artifacts(context: dict, state_dir: Path, public_dir: Path, *, downloads: dict | None = None) -> list[str]:
    """Write only surveillance state and browser inputs, returning logical names."""
    for root in (state_dir, public_dir):
        for group in ("countries", "diseases"):
            (root / group).mkdir(parents=True, exist_ok=True)
    countries = {item["code"]: item for item in context["countries_simple"]}
    country_downloads = {item["code"]: item for item in (downloads or {}).get("countries", [])}
    disease_downloads = {item["disease_id"]: item for item in (downloads or {}).get("diseases", [])}
    logical = []
    for item in context["country_exports"]:
        code = item["code"].lower()
        paths = (f"countries/{code}.json", f"countries/{code}-source-series.json", f"countries/{code}-summary.json")
        logical.extend(paths)
        if not item.get("unchanged_curve"):
            data = item["country_data"]
            write_pretty_json(state_dir / paths[0], data)
            write_compact_json(public_dir / paths[0], item["site_data"])
            write_compact_json(public_dir / paths[1], build_country_source_series_data(data))
            write_compact_json(public_dir / paths[2], country_summary(data, generated_at=context["generated_at"], download=country_downloads.get(item["code"])))
        elif not (public_dir / paths[2]).is_file():
            data = _read(state_dir / paths[0])
            write_compact_json(public_dir / paths[2], country_summary(data, generated_at=context["generated_at"], download=country_downloads.get(item["code"])))
    for item in context["disease_exports"]:
        did = item["disease_id"].lower()
        paths = (f"diseases/{did}.json", f"diseases/{did}-summary.json")
        logical.extend(paths)
        if not item.get("unchanged_curve"):
            data = item["disease_data"]
            write_pretty_json(state_dir / paths[0], data)
            write_compact_json(public_dir / paths[0], item["site_data"])
            write_compact_json(public_dir / paths[1], disease_summary(data, generated_at=context["generated_at"], countries=countries, download=disease_downloads.get(item["disease_id"])))
        elif not (public_dir / paths[1]).is_file():
            data = _read(state_dir / paths[0])
            write_compact_json(public_dir / paths[1], disease_summary(data, generated_at=context["generated_at"], countries=countries, download=disease_downloads.get(item["disease_id"])))
    # Other country updates reuse this small immutable object without reading
    # Japan/China histories again. The hero never downloads full country bundles.
    hero_path = public_dir / "home-influenza.json"
    exports = {item["code"].upper(): item for item in context["country_exports"]}
    changed = {code for code, item in exports.items() if not item.get("unchanged_curve")}
    changed.update(context.get("retired_country_codes") or [])
    if not hero_path.exists() or changed.intersection({"JP", "CN"}):
        previous = _read(hero_path).get("series", {}) if hero_path.exists() else {}
        hero = {}
        for code in ("JP", "CN"):
            if code not in countries:
                continue
            if code not in changed and code in previous:
                hero[code] = previous[code]
                continue
            path = state_dir / "countries" / f"{code.lower()}.json"
            data = exports.get(code, {}).get("country_data")
            if data is not None or path.exists():
                series = home_influenza_series(data if data is not None else _read(path))
                if series:
                    hero[code] = series
        write_compact_json(hero_path, {"schema_version": 1, "series": hero})
    logical.append("home-influenza.json")
    meta = {
        "schema_version": 1,
        "dataset_kind": "surveillance",
        "generated_at": context["generated_at"],
        "total_countries": sum(bool(item.get("data_available")) for item in countries.values()),
        "total_diseases": len(context["diseases"]),
        "countries": context["countries_simple"],
        "diseases": context["diseases"],
        "disease_coverage": {
            item["disease_id"]: {
                "country_count": sum("-" not in code and countries.get(code, {}).get("location_type") != "subdivision" and not countries.get(code, {}).get("parent_code") for code in (item["disease_data"].get("country_series") or {})),
                "subdivision_count": sum("-" in code or countries.get(code, {}).get("location_type") == "subdivision" or bool(countries.get(code, {}).get("parent_code")) for code in (item["disease_data"].get("country_series") or {})),
            } for item in context["disease_exports"]
        },
    }
    write_pretty_json(state_dir / "meta.json", meta)
    write_compact_json(public_dir / "meta.json", meta)
    from .home_summary import surveillance_home_summary
    write_compact_json(public_dir / "home-summary.json", surveillance_home_summary(meta))
    write_compact_json(public_dir / "site-status.json", {"generated_at": context["generated_at"]})
    logical.extend(("meta.json", "site-status.json", "home-summary.json"))
    for group in ("countries", "diseases"):
        expected = {Path(name).name for name in logical if name.startswith(group + "/")}
        remove_stale_json_files(public_dir / group, expected)
        remove_stale_json_files(state_dir / group, {name for name in expected if not name.endswith(("-summary.json", "-source-series.json"))})
    save_receipt(context, state_dir, public_dir)
    return logical


def freeze_public_generation(public_dir: Path, state_dir: Path, logical: list[str], *, release_id: str, generated_at: str, public_url_base: str = "") -> dict:
    """Materialize immutable objects, then atomically move the public pointer."""
    index = ArtifactIndex(public_dir, cache_dir=state_dir / "artifact-index")
    files = {}
    objects = public_dir / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    new_objects = 0
    for name in sorted(logical):
        source = public_dir / name
        info = index.describe(source)
        relative = f"objects/{info['sha256']}.json"
        target = public_dir / relative
        if not target.is_file() or index.describe(target)["sha256"] != info["sha256"]:
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            try:
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            # Describe actual copied bytes, never attest a potentially racing source.
            if index.describe(target)["sha256"] != info["sha256"]:
                raise ValueError(f"Surveillance artifact changed while freezing: {name}")
            new_objects += 1
        files[name] = {"url": f"{public_url_base.rstrip('/')}/{relative}" if public_url_base else relative, "sha256": info["sha256"], "bytes": info["bytes"]}
    manifest = {"schema_version": 1, "release_id": release_id, "generated_at": generated_at, "files": files}
    index.save()
    write_compact_json(public_dir / "current.json", manifest)
    print(f"  Surveillance objects: {new_objects} created, {len(files) - new_objects} reused", flush=True)
    return manifest


async def _export_surveillance_data_unlocked(
    state_dir: Path, public_dir: Path, *, release_id: str,
    incremental_country_codes: list[str] | tuple[str, ...] | None = None,
    incremental: bool = False, country_scopes_exact: bool = True,
    public_url_base: str = "", downloads_manifest: Path | None = None,
    publication_changes: list[dict] | None = None,
    country_workers: int = 1,
    download_candidate: Path | None = None, download_output: Path | None = None,
    download_url_base: str = "",
) -> dict:
    """Export independent dynamic JSON within the caller's frozen DB snapshot.

    An absent completed baseline must be initialized with an explicit full run.
    The caller owns publication and only acknowledges dirty IDs after the
    immutable objects and current pointer are uploaded successfully.
    """
    from .site_data_export import collect_site_export_context
    from .release_projection_snapshot import write_projection_snapshot
    from src.core.database import get_db
    from sqlalchemy import bindparam, text

    if not str(release_id).strip():
        raise ValueError("A surveillance data release requires release_id")
    if (incremental or incremental_country_codes) and completed_generation(state_dir, public_dir) is None:
        raise ValueError("Incremental surveillance export requires an attested completed generation; initialize with a full export")
    projection_version = _generation_version()
    started = time.perf_counter()
    context = await collect_site_export_context(
        state_dir, incremental_country_codes=incremental_country_codes,
        public_site_data_dir=public_dir, incremental_site=incremental,
        country_scopes_exact=country_scopes_exact, surveillance_only=True,
        publication_changes=publication_changes,
        country_workers=country_workers,
    )
    print(f"  Surveillance projection: {time.perf_counter() - started:.1f}s", flush=True)
    downloads = _read(downloads_manifest) if downloads_manifest and downloads_manifest.is_file() else None
    if download_candidate is not None:
        from .surveillance_downloads import generate_download_candidate
        downloads = generate_download_candidate(context, download_output, download_candidate,
            release_id=release_id, download_url_base=download_url_base)
    logical = write_surveillance_artifacts(context, state_dir, public_dir, downloads=downloads)
    selected = context["incremental_country_codes"] if context.get("incremental") else [item["code"] for item in context["country_exports"]]
    high_water = {}
    watermark_codes = sorted(set(selected) - set(context.get("partial_country_codes") or ()))
    if watermark_codes:
        async with get_db() as db:
            rows = await db.execute(text(
                "SELECT geography_key, max(updated_at) FROM disease_series_observations "
                "WHERE geography_key IN :keys GROUP BY geography_key"
            ).bindparams(bindparam("keys", expanding=True)), {"keys": [f"country:{code}:national" for code in watermark_codes]})
            high_water = {str(key).removeprefix("country:").removesuffix(":national"): value for key, value in rows}
    write_projection_snapshot(context, state_dir / "_release-projection", high_water)
    if projection_version != _generation_version():
        raise ValueError("Surveillance projection implementation changed during export; retry with one version")
    manifest = freeze_public_generation(public_dir, state_dir, logical, release_id=release_id, generated_at=context["generated_at"], public_url_base=public_url_base)
    if projection_version != _generation_version():
        raise ValueError("Surveillance projection implementation changed while freezing outputs; retry")
    _complete_generation(state_dir, public_dir, logical, manifest)
    print(f"  Surveillance JSON export completed in {time.perf_counter() - started:.1f}s", flush=True)
    return manifest


async def export_surveillance_data(
    state_dir: Path, public_dir: Path, *, release_id: str,
    incremental_country_codes: list[str] | tuple[str, ...] | None = None,
    incremental: bool = False, country_scopes_exact: bool = True,
    public_url_base: str = "", downloads_manifest: Path | None = None,
    publication_changes: list[dict] | None = None,
    country_workers: int = 1,
    download_candidate: Path | None = None, download_output: Path | None = None,
    download_url_base: str = "",
) -> dict:
    """Serialize local output writers; parent-managed exports may hold the lock."""
    from .surveillance_lock import LOCKED_ENV, surveillance_output_lock
    options = dict(release_id=release_id, incremental_country_codes=incremental_country_codes,
                   incremental=incremental, country_scopes_exact=country_scopes_exact,
                   public_url_base=public_url_base, downloads_manifest=downloads_manifest,
                   publication_changes=publication_changes, country_workers=country_workers,
                   download_candidate=download_candidate, download_output=download_output,
                   download_url_base=download_url_base)
    if os.environ.get(LOCKED_ENV) == "1":
        return await _export_surveillance_data_unlocked(state_dir, public_dir, **options)
    async with surveillance_output_lock(state_dir, public_dir):
        return await _export_surveillance_data_unlocked(state_dir, public_dir, **options)
