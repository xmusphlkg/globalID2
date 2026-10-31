"""NIDSS downloads source bytes; monthly national normalization runs offline."""
import asyncio
import requests
from datetime import datetime, timezone
from pathlib import Path

from src.data.crawlers.tw import TaiwanNIDSSCrawler, TWDiseaseSource, aggregate_monthly_csv_rows, normalize_monthly_exports, _decode_csv_response, DEFAULT_MONTHLY_CSV_TEMPLATE, logger
from src.data.processors.tw import TWUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer
from .monthly_common import normalized_csv


async def collect(writer, *, source, months=None, output_csv):
    path = Path(output_csv)
    if path.exists():
        await writer.write(await asyncio.to_thread(path.read_bytes), source_url=path.resolve().as_uri(), kind="prior_csv")
    crawler = TaiwanNIDSSCrawler(save_raw=False)
    for cached in sorted((crawler.raw_dir / "monthly").glob("*.csv")):
        await writer.write(await asyncio.to_thread(cached.read_bytes), source_url=cached.resolve().as_uri(), kind="cached_monthly_csv", metadata={"code": cached.stem})
    try:
        with capture_writer(writer) as capture:
            try:
                diseases = await asyncio.to_thread(crawler.fetch_disease_index)
            except requests.RequestException:
                capture.verify()
                prior = [item for item in writer.artifacts if item.kind in {"prior_csv", "cached_monthly_csv"}]
                if prior:
                    return prior
                raise
            capture.verify()
        if not diseases:
            prior = [item for item in writer.artifacts if item.kind in {"prior_csv", "cached_monthly_csv"}]
            if prior:
                return prior
            raise RuntimeError("[TW-NIDSS] No diseases discovered from NIDSS dashboard")
        expected_disease_codes = [disease.code for disease in diseases]
        for disease in diseases:
            raw = await asyncio.to_thread(crawler.download_csv_bytes, disease)
            if raw is not None:
                await writer.write(raw, source_url=disease.monthly_csv_url, kind="monthly_csv", metadata={"code": disease.code, "name": disease.name, "weekly_csv_url": disease.weekly_csv_url, "expected_disease_codes": expected_disease_codes})
            else:
                try:
                    exports = await asyncio.to_thread(crawler.fetch_monthly_exports, disease, months=months)
                except (requests.RequestException, ValueError) as exc:
                    logger.warning(f"[TW-NIDSS] Monthly export unavailable | code={disease.code} error={exc}")
                    continue
                for item in exports:
                    await writer.write(item["payload"], source_url=item["source_url"], kind="monthly_ods", metadata={**item["metadata"], "expected_disease_codes": expected_disease_codes})
    finally:
        crawler.session.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv):
    requested = updater._resolve_requested_months(months)
    prior_rows = []
    for artifact in artifacts:
        if artifact.kind == "prior_csv":
            prior_rows = updater.parse_source_csv(read_artifact(artifact))
    labels = {row["DiseaseCode"]: row["RawDiseaseLabel"] for row in prior_rows if row.get("DiseaseCode")}
    live, cached = [], []
    export_groups = {}
    for artifact in artifacts:
        if artifact.kind == "monthly_ods":
            export_groups.setdefault(artifact.metadata_["code"], []).append({"payload": read_artifact(artifact), "metadata": artifact.metadata_})
            continue
        if artifact.kind not in {"monthly_csv", "cached_monthly_csv"}:
            continue
        meta = artifact.metadata_
        code = meta["code"]
        disease = TWDiseaseSource(code, meta.get("name") or labels.get(code) or code, artifact.source_url if artifact.kind == "monthly_csv" else DEFAULT_MONTHLY_CSV_TEMPLATE.format(disease_code=code), meta.get("weekly_csv_url", ""))
        detail = TaiwanNIDSSCrawler._parse_csv_rows(_decode_csv_response(read_artifact(artifact)))
        rows = aggregate_monthly_csv_rows(disease, detail, months=set(map(tuple, requested)))
        (live if artifact.kind == "monthly_csv" else cached).extend(rows)
    for code, exports in export_groups.items():
        disease = TWDiseaseSource(code, exports[0]["metadata"]["name"], "", "")
        live.extend(normalize_monthly_exports(exports, disease=disease))
    live_rows = updater.parse_source_csv(normalized_csv(live)) if live else []
    if not live_rows:
        raise RuntimeError("TW live refresh produced no usable rows; cached data is not a successful refresh")
    today = datetime.now(timezone.utc).date()
    for row in cached:
        is_open = row["Date"][:7] == today.strftime("%Y-%m")
        row["DatasetStatus"] = "provisional" if is_open else "closed_revisable"
        row["IsProvisional"] = "true" if is_open else "false"
    prior = updater._filter_rows_for_months(prior_rows, requested) if updater._rows_cover_months(prior_rows, requested) else []
    # A richer old snapshot must not discard freshly fetched values merely
    # because a few disease exports are unavailable. Retain fallback coverage
    # while letting each live disease/month replace its own previous value.
    codes_by_label = {row["RawDiseaseLabel"]: row["DiseaseCode"] for row in [*prior, *cached, *live_rows] if row.get("DiseaseCode")}
    def identity(row):
        return row["Date"], row.get("DiseaseCode") or codes_by_label.get(row["RawDiseaseLabel"]) or row["RawDiseaseLabel"]
    merged = {identity(row): row for row in [*prior, *cached]}
    live_keys = {identity(row) for row in live_rows}
    merged.update({identity(row): row for row in live_rows})
    for key in merged.keys() - live_keys:
        merged[key] = {**merged[key], "SourceFreshness": "cache_fallback", "SourceDataDate": merged[key]["Date"], "SourceFreshnessReason": "No live result for this disease/month"}
    rows = list(merged.values())
    if not rows:
        raise RuntimeError("TW crawler produced no usable rows")
    rows.sort(key=lambda row: (row["Date"], row["RawDiseaseLabel"]))
    expected_codes = {code for artifact in artifacts for code in (getattr(artifact, "metadata_", None) or {}).get("expected_disease_codes", [])}
    live_codes = {row["DiseaseCode"] for row in live_rows if row.get("DiseaseCode")}
    fallback_rows = [row for row in rows if row.get("SourceFreshness") == "cache_fallback"]
    missing_codes = sorted(expected_codes - live_codes)
    audit = {"status": "partial" if fallback_rows or missing_codes else "fresh", "live_disease_codes": sorted(live_codes), "missing_live_disease_codes": missing_codes, "live_rows": len(live_keys), "fallback_rows": len(fallback_rows), "fallback_disease_dates": [{"code": row.get("DiseaseCode"), "date": row["Date"]} for row in fallback_rows]}
    return TWUpdateFetchResult(rows, updater._latest_row_date(rows), Path(output_csv), [f"[organize] live_rows={len(live_keys)} fallback_rows={len(fallback_rows)}"], source_audit=audit)
