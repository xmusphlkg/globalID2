"""NIDSS downloads source bytes; monthly national normalization runs offline."""
import asyncio
import requests
from datetime import datetime, timezone
from pathlib import Path

from src.data.crawlers.tw import TaiwanNIDSSCrawler, TWDiseaseSource, aggregate_monthly_csv_rows, normalize_monthly_exports, _decode_csv_response, DEFAULT_MONTHLY_CSV_TEMPLATE, DEFAULT_MONTHLY_EXPORT_URL, is_tw_ods_transient_error, logger
from src.data.processors.tw import TWUpdateFetchResult
from src.ontology import load_disease_ontology
from src.services.ingestion.artifacts import read_artifact
from src.services.ingestion.capture import capture_writer
from .monthly_common import normalized_csv


ODS_CONSECUTIVE_TRANSPORT_FAILURE_LIMIT = 3
ODS_MAX_WINDOW_MONTHS = 12
ODS_TRANSPORT_ATTEMPTS = 3


async def _fetch_monthly_exports_with_retry(crawler, disease, window):
    """Retry only transport failures; never change the requested source scope."""
    for attempt in range(1, ODS_TRANSPORT_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(crawler.fetch_monthly_exports, disease, months=window)
        except requests.RequestException as exc:
            if not is_tw_ods_transient_error(exc) or attempt == ODS_TRANSPORT_ATTEMPTS:
                raise
            logger.warning("[TW-NIDSS] Monthly export transport retry | code={} attempt={}/{} error={}",
                           disease.code, attempt, ODS_TRANSPORT_ATTEMPTS, exc)
            await asyncio.sleep(attempt)


def _ods_month_windows(months):
    """Keep sparse requested months separate and cap each source request."""
    if months is None:
        return [None]
    values = sorted(set(map(tuple, months)))
    windows = []
    for year, month in values:
        if not 1 <= month <= 12:
            raise ValueError(f"Invalid NIDSS month: {year}-{month}")
        index = year * 12 + month - 1
        if not windows or len(windows[-1]) >= ODS_MAX_WINDOW_MONTHS:
            windows.append([(year, month)])
            continue
        last_year, last_month = windows[-1][-1]
        if index != last_year * 12 + last_month:
            windows.append([(year, month)])
        else:
            windows[-1].append((year, month))
    # The recent window should survive an interrupted historical backfill.
    return list(reversed(windows))


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
                raise
            capture.verify()
        if not diseases:
            raise RuntimeError("[TW-NIDSS] No diseases discovered from NIDSS dashboard")
        expected_disease_codes = [disease.code for disease in diseases]
        consecutive_ods_failures = 0
        unresolved_transport = []
        windows = _ods_month_windows(months)
        for disease in diseases:
            raw = await asyncio.to_thread(crawler.download_csv_bytes, disease)
            if raw is not None:
                consecutive_ods_failures = 0
                actual_url = getattr(crawler, "last_csv_transport_url", None) or disease.monthly_csv_url
                await writer.write(raw, source_url=actual_url, kind="monthly_csv", metadata={"code": disease.code, "name": disease.name, "weekly_csv_url": disease.weekly_csv_url, "expected_disease_codes": expected_disease_codes, "canonical_source_url": disease.monthly_csv_url, "transport_security": "https" if actual_url.lower().startswith("https://") else "http"})
            else:
                for window in windows:
                    try:
                        exports = await _fetch_monthly_exports_with_retry(crawler, disease, window)
                    except (requests.RequestException, ValueError) as exc:
                        if is_tw_ods_transient_error(exc):
                            consecutive_ods_failures += 1
                            unresolved_transport.append(disease.code)
                            if consecutive_ods_failures >= ODS_CONSECUTIVE_TRANSPORT_FAILURE_LIMIT:
                                raise requests.ConnectionError(
                                    "TW NIDSS monthly export temporarily unavailable after "
                                    f"{consecutive_ods_failures} consecutive transport failures; "
                                    "partial source artifacts were retained for diagnosis"
                                ) from exc
                        else:
                            consecutive_ods_failures = 0
                        logger.warning(f"[TW-NIDSS] Monthly export unavailable | code={disease.code} error={exc}")
                        break
                    consecutive_ods_failures = 0
                    for item in exports:
                        await writer.write(item["payload"], source_url=item["source_url"], kind="monthly_ods", metadata={**item["metadata"], "expected_disease_codes": expected_disease_codes})
        if unresolved_transport:
            raise requests.ConnectionError(
                "TW NIDSS monthly export temporarily unavailable after retries for "
                + ", ".join(sorted(set(unresolved_transport)))
                + "; partial source artifacts were retained for diagnosis"
            )
    finally:
        crawler.session.close()
    return writer.artifacts


def organize(artifacts, *, updater, source, months=None, output_csv):
    requested = updater._resolve_requested_months(months)
    recent_months = set(updater._default_recent_months())
    ontology = load_disease_ontology()
    series_by_code = {}
    excluded = []

    def keep_row(row, *, ods=False):
        code, day = row.get("DiseaseCode"), row["Date"]
        if not code:
            return True
        if code not in series_by_code:
            series_by_code[code] = ontology.series_lookup(
                source_id="SRC_TW_NIDSS", country_code="TW", local_code=code
            )
        matches = series_by_code[code]
        series = matches[0] if len(matches) == 1 else {}
        start, end = series.get("valid_from"), series.get("valid_to")
        outside = (start and day < start) or (end and day > end)
        zero = str(row.get("Cases", "")).strip() in {"0", "0.0"} and all(
            str(row.get(field, "") or "0").strip() in {"0", "0.0"}
            for field in ("LocalCases", "ImportedCases")
        )
        if outside:
            if not zero:
                raise ValueError(f"TW {code} has a nonzero observation outside its reviewed validity on {day}")
            excluded.append({"code": code, "date": day, "reason": "outside_reviewed_validity"})
            return False
        month = tuple(map(int, day[:7].split("-")))
        if ods and zero and month not in recent_months and not start:
            # A zero-filled report cannot establish when a disease entered
            # surveillance. Keep historical coverage unknown until verified.
            excluded.append({"code": code, "date": day, "reason": "unverified_historical_zero"})
            return False
        return True

    prior_rows = []
    for artifact in artifacts:
        if artifact.kind == "prior_csv":
            prior_rows = updater.parse_source_csv(read_artifact(artifact))
    labels = {row["DiseaseCode"]: row["RawDiseaseLabel"] for row in prior_rows if row.get("DiseaseCode")}
    live, cached = [], []
    export_groups = {}
    rejected_exports = []
    for artifact in artifacts:
        if artifact.kind == "monthly_ods":
            metadata = artifact.metadata_
            key = (metadata["code"], tuple(sorted(set(map(tuple, metadata["months"])))))
            export_groups.setdefault(key, []).append({"payload": read_artifact(artifact), "metadata": metadata})
            continue
        if artifact.kind not in {"monthly_csv", "cached_monthly_csv"}:
            continue
        meta = artifact.metadata_
        code = meta["code"]
        disease = TWDiseaseSource(code, meta.get("name") or labels.get(code) or code, artifact.source_url if artifact.kind == "monthly_csv" else DEFAULT_MONTHLY_CSV_TEMPLATE.format(disease_code=code), meta.get("weekly_csv_url", ""))
        detail = TaiwanNIDSSCrawler._parse_csv_rows(_decode_csv_response(read_artifact(artifact)))
        rows = aggregate_monthly_csv_rows(disease, detail, months=set(map(tuple, requested)))
        (live if artifact.kind == "monthly_csv" else cached).extend(rows)
    for (code, _window), exports in export_groups.items():
        disease = TWDiseaseSource(code, exports[0]["metadata"]["name"], "", "")
        try:
            normalized = normalize_monthly_exports(exports, disease=disease)
        except ValueError as exc:
            # Reject the whole disease/population batch, retaining valid peers
            # and explicitly marked cache coverage without inventing counts.
            rejected_exports.append({"code": code, "reason": str(exc)})
            logger.warning("[TW-NIDSS] Monthly export rejected | code={} error={}", code, exc)
            continue
        live.extend(row for row in normalized if keep_row(row, ods=True))
    live = [row for row in live if keep_row(row)]
    live_rows = updater.parse_source_csv(normalized_csv(live)) if live else []
    if not live_rows:
        raise RuntimeError("TW live refresh produced no usable rows; cached data is not a successful refresh")
    today = datetime.now(timezone.utc).date()
    for row in cached:
        is_open = row["Date"][:7] == today.strftime("%Y-%m")
        row["DatasetStatus"] = "provisional" if is_open else "closed_revisable"
        row["IsProvisional"] = "true" if is_open else "false"
    prior = [row for row in updater._filter_rows_for_months(prior_rows, requested) if keep_row(row, ods=row.get("SourceURL") == DEFAULT_MONTHLY_EXPORT_URL)]
    cached = [row for row in cached if keep_row(row)]
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
    insecure_codes = sorted({artifact.metadata_["code"] for artifact in artifacts if artifact.kind == "monthly_csv" and (getattr(artifact, "metadata_", None) or {}).get("transport_security") == "http"})
    audit = {"status": "partial" if fallback_rows or missing_codes or rejected_exports or excluded or insecure_codes else "fresh", "live_disease_codes": sorted(live_codes), "missing_live_disease_codes": missing_codes, "live_rows": len(live_keys), "fallback_rows": len(fallback_rows), "fallback_disease_dates": [{"code": row.get("DiseaseCode"), "date": row["Date"]} for row in fallback_rows], "rejected_exports": rejected_exports, "excluded_rows": excluded, "unverified_transport_disease_codes": insecure_codes}
    return TWUpdateFetchResult(rows, updater._latest_row_date(live_rows), Path(output_csv), [f"[organize] live_rows={len(live_keys)} fallback_rows={len(fallback_rows)}"], source_audit=audit, store_rows=live_rows)
