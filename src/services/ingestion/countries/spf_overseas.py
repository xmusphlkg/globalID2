"""Complete counted SPF API pages; organization only reads immutable source bytes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

from src.data.crawlers.spf_overseas import (
    DATASETS, DEFAULT_SCOPE, SPFOverseasCrawler, country_spec, metadata_url,
    records_url, page_document, parse_records, validate_metadata,
)
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, country_code, source=DEFAULT_SCOPE, crawler_type=SPFOverseasCrawler, **options):
    country_spec(country_code)
    if source not in {DEFAULT_SCOPE, 'all'}:
        raise ValueError('Unsupported SPF scope')
    crawler = crawler_type()
    retrieved = datetime.now(timezone.utc).isoformat()
    try:
        for dataset in DATASETS:
            url = metadata_url(dataset)
            response = await asyncio.to_thread(crawler.get, url)
            await writer.write(response.content, source_url=response.url, kind='archive_json', metadata={
                'endpoint': 'spf_metadata', 'dataset': dataset, 'country_code': country_code,
                'retrieved_at': retrieved, 'requested_url': url,
            })
            initial_meta = json.loads(response.content)
            validate_metadata(dataset, initial_meta)
            offset, total = 0, None
            while total is None or offset < total:
                url = records_url(dataset, country_code, offset=offset)
                response = await asyncio.to_thread(crawler.get, url)
                await writer.write(response.content, source_url=response.url, kind='archive_json', metadata={
                    'endpoint': 'spf_records', 'dataset': dataset, 'country_code': country_code,
                    'retrieved_at': retrieved, 'requested_url': url, 'offset': offset,
                })
                page = page_document(response.content)
                if total is not None and total != page['total_count']:
                    raise ValueError('SPF record count changed during pagination')
                total = page['total_count']
                expected = min(100, total - offset)
                if len(page['results']) != expected or total > 10000:
                    raise ValueError('SPF truncated, excessive or inconsistent page count')
                offset += len(page['results'])
                if total == 0:
                    break
            # Catch source changes during offset pagination, not only row-count changes.
            final_response = await asyncio.to_thread(crawler.get, metadata_url(dataset))
            await writer.write(final_response.content, source_url=final_response.url, kind='archive_json', metadata={
                'endpoint': 'spf_metadata_final', 'dataset': dataset, 'country_code': country_code,
                'retrieved_at': retrieved, 'requested_url': metadata_url(dataset),
            })
            if json.loads(final_response.content) != initial_meta:
                raise ValueError('SPF metadata changed during record capture')
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, **options):
    updater.normalize_source(source)
    country_code = updater.country_code
    metadata, records, counts = {}, [], {}
    captures = list(artifacts)
    if any(a.metadata_.get('dataset') not in DATASETS or a.metadata_.get('endpoint') not in {'spf_metadata', 'spf_metadata_final', 'spf_records'} for a in captures):
        raise ValueError('Unexpected SPF dataset or artifact endpoint')
    if any(a.metadata_.get('country_code') != country_code for a in captures):
        raise ValueError('SPF artifact territory differs from updater')
    captured_dates = {a.metadata_['retrieved_at'] for a in captures}
    if len(captured_dates) != 1:
        raise ValueError('SPF capture timestamps disagree')
    as_of = datetime.fromisoformat(captured_dates.pop().replace('Z', '+00:00')).date()
    for dataset in DATASETS:
        metas = [a for a in captures if a.metadata_.get('dataset') == dataset and a.metadata_.get('endpoint') == 'spf_metadata']
        finals = [a for a in captures if a.metadata_.get('dataset') == dataset and a.metadata_.get('endpoint') == 'spf_metadata_final']
        if len(metas) != 1 or len(finals) != 1:
            raise ValueError('Missing or duplicate SPF metadata capture')
        first, last = json.loads(read_artifact(metas[0])), json.loads(read_artifact(finals[0]))
        if first != last or metas[0].metadata_['requested_url'] != metadata_url(dataset):
            raise ValueError('SPF metadata changed during captured pagination')
        validate_metadata(dataset, first)
        metadata[dataset] = first
        pages = sorted((a for a in captures if a.metadata_.get('dataset') == dataset and a.metadata_.get('endpoint') == 'spf_records'), key=lambda a: a.metadata_['offset'])
        offset, total = 0, None
        if not pages:
            raise ValueError('Missing SPF record pages')
        for artifact in pages:
            if artifact.metadata_['offset'] != offset or artifact.metadata_['requested_url'] != records_url(dataset, country_code, offset=offset):
                raise ValueError('Discontinuous or duplicate SPF page sequence')
            page = page_document(read_artifact(artifact))
            if total is not None and total != page['total_count']:
                raise ValueError('SPF captured total changed')
            total = page['total_count']
            if total > 10000 or (total == 0 and len(pages) != 1):
                raise ValueError('SPF captured page count is excessive or duplicate')
            if len(page['results']) != min(100, total - offset):
                raise ValueError('SPF captured page is truncated')
            records.extend((dataset, row, {'DownloadURL': artifact.source_url, 'RawSHA256': artifact.sha256,
                'SourceMetadataSHA256': metas[0].sha256, 'RetrievedAt': artifact.metadata_['retrieved_at'],
                'SourceRecordIndex': offset + index}) for index, row in enumerate(page['results']))
            offset += len(page['results'])
        if offset != total:
            raise ValueError('Incomplete SPF page chain')
        counts[dataset] = total
    rows = parse_records(records, country_code=country_code, metadata=metadata, as_of=as_of)
    result = updater.prepare_rows(rows, source=source, **options)
    result.source_audit.update({
        'country_code': country_code, 'source_datasets': counts, 'source_records': len(records),
        'source_observations': len(rows), 'coverage_complete': True,
        'missing_observations': sum(r['MissingValueStatus'] == 'source_missing' for r in rows),
        'suppressed_observations': sum(r['MissingValueStatus'] == 'source_suppressed' for r in rows),
        'first_reported_year': min(int(r['Year']) for r in rows), 'latest_reported_year': max(int(r['Year']) for r in rows),
        'refresh_policy': 'all published history; upsert revisions; no deletion from absent years',
    })
    return result
