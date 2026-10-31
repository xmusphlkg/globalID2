"""ECDC transport discovery and offline annual normalization."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from datetime import date
from pathlib import Path

from src.data.crawlers.ecdc import (
    API_BASE,
    DATASET_ID,
    DEFAULT_SCOPE,
    TOPIC_CONTRACTS,
    ECDCAtlasCrawler,
    ECDCContractError,
    _annual_count_measure,
    _text,
)
from src.data.processors.ecdc import ECDCUpdateFetchResult
from src.services.ingestion.artifacts import read_artifact


def normalize_source(source):
    if str(source).strip().lower() not in {"all", "ecdc", "atlas", DEFAULT_SCOPE}:
        raise ValueError(f"Unsupported ECDC source: {source!r}")


async def collect(
    writer,
    *,
    source=DEFAULT_SCOPE,
    country_code,
    start_year=1990,
    history_csv=None,
    **options,
):
    normalize_source(source)
    crawler = ECDCAtlasCrawler(country_code)

    async def request(endpoint, **params):
        response = await asyncio.to_thread(
            crawler.get, f"{API_BASE}{endpoint}", params=params
        )
        await writer.write(
            response.content,
            source_url=response.url,
            kind="archive_json",
            metadata={"endpoint": endpoint, "params": params},
        )
        payload = json.loads(response.content)
        if not isinstance(payload, dict):
            raise ECDCContractError(f"ECDC {endpoint} returned a non-object payload")
        return payload

    try:
        topics = await request("GetHealthTopicsForDataset", datasetId=DATASET_ID)
        by_code = {item.get("Code"): item for item in topics.get("HealthTopics") or []}
        for contract in TOPIC_CONTRACTS:
            topic = by_code.get(contract.code)
            if topic is None:
                raise ECDCContractError(
                    f"ECDC topic contract changed for {contract.code}"
                )
            for population in contract.populations:
                measures = await request(
                    "GetIndicatorMeasuresForHealthTopicDatasetAndPopulation",
                    datasetId=DATASET_ID,
                    healthtopicId=int(topic["Id"]),
                    measurePopulation=population,
                )
                measure = _annual_count_measure(measures.get("Measures") or [])
                await request(
                    "GetMeasureResultsForTimeUnitAndGeoRegion",
                    measureId=int(measure["Id"]),
                    timeUnit="Y",
                    geoCode=crawler.source_geo_code,
                )
        if history_csv and Path(history_csv).exists():
            payload = await asyncio.to_thread(Path(history_csv).read_bytes)
            await writer.write(
                payload, source_url=Path(history_csv).as_posix(), kind="history_csv"
            )
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(
    artifacts,
    *,
    updater,
    source=DEFAULT_SCOPE,
    country_code=None,
    start_year=1990,
    **options,
):
    normalize_source(source)
    payloads = {}
    history = []
    for artifact in artifacts:
        raw = read_artifact(artifact)
        if artifact.kind == "history_csv":
            history.extend(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        else:
            meta = artifact.metadata_
            payloads[(meta["endpoint"], tuple(sorted(meta["params"].items())))] = (
                json.loads(raw)
            )

    def payload(endpoint, **params):
        try:
            return payloads[(endpoint, tuple(sorted(params.items())))]
        except KeyError as exc:
            raise ECDCContractError(
                f"Missing ECDC captured response: {endpoint} {params}"
            ) from exc

    crawler = ECDCAtlasCrawler(updater.country_code)
    try:
        topics = payload("GetHealthTopicsForDataset", datasetId=DATASET_ID)
        by_code = {item.get("Code"): item for item in topics.get("HealthTopics") or []}
        fresh = []
        for contract in TOPIC_CONTRACTS:
            topic = by_code.get(contract.code)
            if topic is None or (
                contract.code != "HIVAIDS"
                and _text(topic.get("Label")) != contract.label
            ):
                raise ECDCContractError(
                    f"ECDC topic contract changed for {contract.code}"
                )
            topic_id = int(topic["Id"])
            captured = []
            for population in contract.populations:
                measures = payload(
                    "GetIndicatorMeasuresForHealthTopicDatasetAndPopulation",
                    datasetId=DATASET_ID,
                    healthtopicId=topic_id,
                    measurePopulation=population,
                )
                measure = _annual_count_measure(measures.get("Measures") or [])
                results = payload(
                    "GetMeasureResultsForTimeUnitAndGeoRegion",
                    measureId=int(measure["Id"]),
                    timeUnit="Y",
                    geoCode=crawler.source_geo_code,
                )
                captured.append((population, measures, results))
            rows, _ = crawler.parse_topic_payloads(
                contract, topic_id, captured, start_year=int(start_year)
            )
            fresh.extend(rows)
        merged = {}
        for raw in history:
            row = {str(key): _text(value) for key, value in raw.items()}
            if (
                row.get("Date")
                and row.get("SourceDiseaseCode")
                and re.match(r"^\d{4}", row["Date"])
                and int(row["Date"][:4]) < int(start_year)
            ):
                merged[(row["Date"], row["SourceDiseaseCode"])] = row
        for raw in fresh:
            row = {str(key): _text(value) for key, value in raw.items()}
            merged[(row["Date"], row["SourceDiseaseCode"])] = row
        rows = sorted(
            merged.values(), key=lambda row: (row["Date"], row["SourceDiseaseCode"])
        )
        # The old CSV round-trip filled every absent historical column with
        # an empty string. Preserve that source-row shape before registry mapping.
        fields = {key for row in rows for key in row}
        rows = [
            {key: row.get(key, "") for key in fields}
            for row in rows
            if _text(row.get("Date")) and _text(row.get("Cases"))
        ]
        latest = max((date.fromisoformat(row["Date"]) for row in rows), default=None)
        return ECDCUpdateFetchResult(
            rows,
            latest,
            updater.output_csv,
            [
                f"[artifacts] {len(artifacts)} captured responses; {len(rows)} annual ECDC observations"
            ],
        )
    finally:
        crawler.session.close()
