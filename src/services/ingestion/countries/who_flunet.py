"""Full-history country-specific FluNet capture and integrity-checked offline replay."""
from datetime import datetime, timezone

from src.data.crawlers.who_flunet import (
    DEFAULT_SCOPE, DICTIONARY_URL, WHOFluNetCrawler, country_contract,
    data_url, detection_fields, parse_records,
)
from .my_who_common import capture_pages, replay_pages


async def collect(writer, *, country_code, source=DEFAULT_SCOPE,
                  crawler_type=WHOFluNetCrawler, **options):
    code, _ = country_contract(country_code)
    if str(source or DEFAULT_SCOPE).casefold() not in {"all", DEFAULT_SCOPE, code.lower()}:
        raise ValueError("Unsupported WHO FluNet source")
    crawler = crawler_type()
    retrieved = datetime.now(timezone.utc).isoformat()
    try:
        await capture_pages(writer, crawler, url=DICTIONARY_URL,
                            dataset="flunet_dictionary", retrieved_at=retrieved)
        await capture_pages(writer, crawler, url=data_url(code),
                            dataset=f"flunet_{code.lower()}", retrieved_at=retrieved)
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    code, _ = country_contract(updater.country_code)
    artifacts = list(artifacts)
    allowed = {"flunet_dictionary", f"flunet_{code.lower()}"}
    if any(a.metadata_.get("endpoint") != "who_odata_page"
           or a.metadata_.get("dataset") not in allowed for a in artifacts):
        raise ValueError("Unexpected FluNet country/dataset artifacts")
    captured = {a.metadata_.get("retrieved_at") for a in artifacts}
    if len(captured) != 1 or None in captured:
        raise ValueError("FluNet artifacts do not belong to one capture")
    captured_as_of = datetime.fromisoformat(captured.pop().replace("Z", "+00:00")).date()
    dictionary = replay_pages(artifacts, url=DICTIONARY_URL, dataset="flunet_dictionary")
    fields = detection_fields([item[0] for item in dictionary])
    dictionary_hashes = sorted({item[1].sha256 for item in dictionary})
    records = [(row, {
        "DownloadURL": artifact.source_url, "RawSHA256": artifact.sha256,
        "RetrievedAt": artifact.metadata_["retrieved_at"], "SourceRecordIndex": index,
        "SourceDictionarySHA256": dictionary_hashes[0],
        "SourceDictionarySHA256s": ",".join(dictionary_hashes),
    }) for row, artifact, index in replay_pages(
        artifacts, url=data_url(code), dataset=f"flunet_{code.lower()}")]
    rows, audit = parse_records(records, country_code=code, fields=fields, as_of=captured_as_of)
    result = updater.prepare_rows(rows, source=source, **options)
    result.source_audit.update(audit, country_code=code, coverage_complete=True,
                               coverage_definition="all returned closed-week detection cells, including missing cells")
    result.source_audit.update(selected_observations=len(result.rows),
        selected_first_date=min((r["Date"] for r in result.rows), default=None),
        selected_latest_date=max((r["Date"] for r in result.rows), default=None),
        selected_source_years=sorted({int(r["SourceYear"]) for r in result.rows}))
    return result
