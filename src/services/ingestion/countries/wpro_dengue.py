"""Current WHO WPRO dengue directory capture and pure PDF organization."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from src.data.crawlers.wpro_dengue import DEFAULT_SCOPE, LANDING_URL, WPRODengueCrawler, country_contract, discover_publications, discover_pdf, parse_report
from src.services.ingestion.artifacts import read_artifact


async def collect(writer, *, country_code, source=DEFAULT_SCOPE, crawler_type=WPRODengueCrawler, **options):
    country_contract(country_code)
    if source != DEFAULT_SCOPE:
        raise ValueError('Use canonical WPRO dengue scope')
    crawler = crawler_type()
    retrieved = datetime.now(timezone.utc).isoformat()
    try:
        listing = await asyncio.to_thread(crawler.get, LANDING_URL)
        await writer.write(listing.content, source_url=listing.url, kind='html_report', metadata={'endpoint': 'listing', 'retrieved_at': retrieved})
        urls = discover_publications(listing.content)
        plan = {'country_code': country_code, 'retrieved_at': retrieved, 'listing_sha256': hashlib.sha256(listing.content).hexdigest(), 'publications': urls, 'reports': [], 'failed_reports': []}
        for publication in urls:
            try:
                page = await asyncio.to_thread(crawler.get, publication)
            except Exception as exc:
                plan['failed_reports'].append({'publication': publication, 'error': str(exc)})
                continue
            await writer.write(page.content, source_url=publication, kind='html_report', metadata={'endpoint': 'publication', 'retrieved_at': retrieved})
            try:
                url = discover_pdf(page.content)
                response = await asyncio.to_thread(crawler.get, url)
                if not response.content.startswith(b'%PDF'):
                    raise ValueError('Official PDF response is not PDF')
            except Exception as exc:
                plan['failed_reports'].append({'publication': publication, 'error': str(exc)})
                continue
            await writer.write(response.content, source_url=url, kind='cda_pdf', metadata={'endpoint': 'pdf', 'publication': publication, 'retrieved_at': retrieved})
            plan['reports'].append({'publication': publication, 'pdf': url, 'sha256': hashlib.sha256(response.content).hexdigest()})
        if not plan['reports']:
            raise ValueError('No WPRO dengue PDFs retrieved')
        await writer.write(json.dumps(plan, sort_keys=True).encode(), source_url=LANDING_URL, kind='archive_json', metadata={'endpoint': 'collection_plan', 'retrieved_at': retrieved})
        return writer.artifacts
    finally:
        crawler.session.close()


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, country_code=None, **options):
    updater.normalize_source(source)
    country_code = country_code or updater.country_code
    if country_code != updater.country_code:
        raise ValueError('WPRO updater country differs from plan')
    captures = list(artifacts)
    if any(a.metadata_.get('endpoint') not in {'listing', 'publication', 'pdf', 'collection_plan'} for a in captures):
        raise ValueError('Unexpected WPRO artifact endpoint')
    plans = [a for a in captures if a.metadata_.get('endpoint') == 'collection_plan']
    listings = [a for a in captures if a.metadata_.get('endpoint') == 'listing']
    if len(plans) != 1 or len(listings) != 1:
        raise ValueError('WPRO capture requires exactly one listing and plan')
    plan = json.loads(read_artifact(plans[0]))
    if plan['country_code'] != country_code or plan['listing_sha256'] != listings[0].sha256 or plan['publications'] != discover_publications(read_artifact(listings[0])):
        raise ValueError('WPRO plan country/listing differs')
    coverage = [r['publication'] for r in plan['reports']] + [r['publication'] for r in plan['failed_reports']]
    if sorted(coverage) != sorted(plan['publications']):
        raise ValueError('WPRO plan omitted or duplicated listed report')
    pages = {a.source_url: a for a in captures if a.metadata_.get('endpoint') == 'publication'}
    pdfs = {a.source_url: a for a in captures if a.metadata_.get('endpoint') == 'pdf'}
    if len(pages) != sum(a.metadata_.get('endpoint') == 'publication' for a in captures) or len(pdfs) != sum(a.metadata_.get('endpoint') == 'pdf' for a in captures):
        raise ValueError('Duplicate WPRO publication or PDF artifacts')
    if len(pdfs) != len(plan['reports']):
        raise ValueError('WPRO PDF capture count differs from plan')
    reports, values, errors, conflicts = [], {}, [], []
    as_of = datetime.fromisoformat(plan['retrieved_at']).date()
    for report in plan['reports']:
        page, pdf = pages[report['publication']], pdfs[report['pdf']]
        if discover_pdf(read_artifact(page)) != pdf.source_url or pdf.sha256 != report['sha256']:
            raise ValueError('WPRO PDF differs from captured linked publication')
        try:
            rows, audit = parse_report(read_artifact(pdf), country_code=country_code, as_of=as_of,
                provenance={'DownloadURL': pdf.source_url, 'RawSHA256': pdf.sha256,
                            'RetrievedAt': pdf.metadata_['retrieved_at'], 'SourcePublicationURL': report['publication']})
            reports.append(audit)
            for row in rows:
                key = (row['Date'], row['SourceDiseaseCode'])
                previous = values.get(key)
                if previous is not None:
                    if previous['SourcePublishedAt'] == row['SourcePublishedAt'] and previous['Cases'] != row['Cases']:
                        conflicts.append(key)
                        continue
                    if previous['SourcePublishedAt'] > row['SourcePublishedAt']:
                        continue
                values[key] = row
        except Exception as exc:
            errors.append({'publication': report['publication'], 'error': str(exc)})
    for key in conflicts:
        values.pop(key, None)
    result = updater.prepare_rows(list(values.values()), source=source, **options)
    result.source_audit.update({'country_code': country_code, 'discovered_reports': len(plan['publications']),
        'captured_reports': len(plan['reports']), 'parsed_reports': reports, 'parse_failures': errors,
        'failed_downloads': plan['failed_reports'], 'conflicts': conflicts, 'source_observations': len(result.rows),
        'archive_scope': 'reports currently linked by the WHO surveillance landing page', 'coverage_complete': False,
        'coverage_note': 'Historical IRIS archive and graph-only points remain outside this numerical contract; missing weeks are unknown.',
        'refresh_policy': 'Rediscover current landing-page reports, refetch linked originals and upsert reviewed explicit intervals.'})
    return result
