"""Lossless national WHO GHO capture; complete source-native offline replay."""
from datetime import datetime, timezone

from src.data.crawlers.who_annual import DEFAULT_SCOPE, INDICATORS, METADATA_URL, WHOAnnualCrawler, country_contract, data_url, parse_records, validate_indicators
from src.data.processors.who_annual import WHOAnnualUpdater
from .my_who_common import capture_pages, replay_pages


async def collect(writer, *, country_code, source=DEFAULT_SCOPE, crawler_type=WHOAnnualCrawler, **options):
    updater = WHOAnnualUpdater(country_code)
    updater.normalize_source(source)
    crawler = crawler_type()
    retrieved = datetime.now(timezone.utc).isoformat()
    try:
        await capture_pages(writer, crawler, url=METADATA_URL, dataset="gho_indicators", retrieved_at=retrieved)
        for indicator in INDICATORS:
            await capture_pages(writer, crawler, url=data_url(indicator, country_code), dataset=indicator, retrieved_at=retrieved)
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, country_code=None, source=DEFAULT_SCOPE, **options):
    country_code = updater.country_code if country_code is None else country_code
    country_code, contract = country_contract(country_code)
    if updater.country_code != country_code or updater.ontology_source_id != contract["source_id"]:
        raise ValueError("WHO updater country/source disagrees with captured contract")
    updater.normalize_source(source)
    metadata_records = replay_pages(artifacts, url=METADATA_URL, dataset="gho_indicators")
    metadata = [row for row, _, _ in metadata_records]
    validate_indicators(metadata)
    metadata_hashes = {row["IndicatorCode"]: artifact.sha256 for row, artifact, _ in metadata_records}
    captured_as_of = datetime.fromisoformat(metadata_records[0][1].metadata_["retrieved_at"].replace("Z", "+00:00")).date()
    records, empty, extents = [], [], {}
    for indicator in INDICATORS:
        data = replay_pages(artifacts, url=data_url(indicator, country_code), dataset=indicator)
        if not data:
            empty.append(indicator)
        years = [row.get("TimeDim") for row, _, _ in data]
        extents[indicator] = {"records": len(data), "first_year": min(years) if years else None, "last_year": max(years) if years else None}
        records.extend((indicator, row, {
            "DownloadURL": artifact.source_url, "RawSHA256": artifact.sha256,
            "RetrievedAt": artifact.metadata_["retrieved_at"], "SourceRecordIndex": index,
            "SourceIndicatorMetadataSHA256": metadata_hashes[indicator],
        }) for row, artifact, index in data)
    rows = parse_records(records, country_code=country_code, metadata=metadata, as_of=captured_as_of)
    result = updater.prepare_rows(rows, source=source, **options)
    result.source_audit.update({
        "country_code": country_code, "source_system": contract["source_id"],
        "source_records": len(rows), "source_indicators_with_records": len({row["SourceDiseaseCode"] for row in rows}),
        "queried_indicators": len(INDICATORS), "indicators_without_country_records": empty,
        "indicator_extents": extents, "captured_artifacts": len(artifacts),
        "numeric_observations": sum(row["MissingValueStatus"] == "reported" for row in rows),
        "missing_observations": sum(row["MissingValueStatus"] == "source_missing" for row in rows),
        "suppressed_observations": sum(row["MissingValueStatus"] == "source_suppressed" for row in rows),
        "frequency": "annual", "latest_reported_year": max(int(row["Year"]) for row in rows),
        "first_reported_year": min(int(row["Year"]) for row in rows),
        "refresh_policy": "full_published_history_upsert", "distribution": "WHO GHO",
        "coverage_note": "Limited to the 18 reviewed reported-count indicators; not comprehensive national disease surveillance.",
    })
    return result
