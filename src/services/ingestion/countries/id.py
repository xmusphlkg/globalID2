"""Live-discovered Indonesian annual profiles, with offline integrity replay."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse, quote

import requests
from src.data.crawlers.id import (
    CHANNEL_URL, DEFAULT_SCOPE, HISTORY_START_YEAR, PARSER_SHA256,
    IndonesiaContractError, IndonesiaKemkesProfileCrawler,
    discover_profiles, discover_downloads, parse_profile_pdf,
)
from src.data.processors.id import IndonesiaFetchResult
from src.services.ingestion.artifacts import read_artifact


def shortlink_target(payload):
    try:
        content = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise IndonesiaContractError('Official shortlink did not return valid JSON') from exc
    target = content.get('original_url', '')
    if content.get('is_public') is not True or content.get('is_active') is not True:
        raise IndonesiaContractError('Official shortlink is not active and public')
    if not re.fullmatch(r'https://drive\.kemkes\.go\.id:5001/sharing/[A-Za-z0-9_-]+', target):
        raise IndonesiaContractError('Unverified official public drive target')
    return target


def share_download(payload, *, share_url):
    text = payload.decode('utf-8')
    extra = re.search(r'SYNO\.SDS\.ExtraSession\s*=\s*(\{.*?\})\s*;', text, re.S)
    if not extra:
        raise IndonesiaContractError('Official public share has no file metadata')
    try:
        metadata = json.loads(extra[1])
    except ValueError as exc:
        raise IndonesiaContractError('Official public share metadata is invalid') from exc
    name = metadata.get('filename', '')
    if metadata.get('status') != 0 or metadata.get('is_folder') is not False or not re.fullmatch(r'[^/\\]+\.pdf', name, re.I):
        raise IndonesiaContractError('Official public share is not an available single PDF')
    parts = urlparse(share_url); sharing_id = parts.path.rsplit('/', 1)[1]
    return f'{parts.scheme}://{parts.netloc}/fsdownload/{sharing_id}/{quote(name, safe="")}'


async def collect(writer, *, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
                  end_year=None, crawler_type=IndonesiaKemkesProfileCrawler, **options):
    from src.data.processors.id import IndonesiaKemkesProfileUpdater
    IndonesiaKemkesProfileUpdater.normalize_source(source)
    crawler = crawler_type(); errors = []; references = []; captured = []; failed_urls = set()

    async def request(url, *, endpoint, year=None):
        # Network failures are coverage gaps; failed durable writes propagate.
        try:
            response = await asyncio.to_thread(crawler.get, url)
        except requests.RequestException as exc:
            failed_urls.add(url)
            if exc.response is not None:
                await writer.write(exc.response.content, source_url=url, kind='html_report',
                    metadata={'endpoint': 'failed_response', 'year': year,
                              'status_code': exc.response.status_code, 'failed_endpoint': endpoint,
                              'retrieved_at': datetime.now(timezone.utc).isoformat()})
            errors.append({'url': url, 'year': year, 'stage': endpoint, 'error': str(exc)})
            return None
        await writer.write(response.content, source_url=url,
            kind='cda_pdf' if endpoint == 'profile_pdf' else 'html_report',
            metadata={'endpoint': endpoint, 'year': year,
                'resolved_url': response.url, 'status_code': response.status_code,
                'etag': response.headers.get('ETag'), 'last_modified': response.headers.get('Last-Modified'),
                'retrieval_mode': response.headers.get('X-GIDS-Retrieval-Mode', 'live'),
                'retrieved_at': datetime.now(timezone.utc).isoformat()})
        return response.content

    try:
        index = await request(CHANNEL_URL, endpoint='index')
        if index is None:
            raise IndonesiaContractError('Live source directory could not be captured')
        targets = discover_profiles(index, start_year=start_year, end_year=end_year)
        for target in targets:
            page = await request(target['page_url'], endpoint='annual_page', year=target['year'])
            if page is None:
                continue
            try:
                downloads = discover_downloads(page, reference=target)
            except IndonesiaContractError as exc:
                errors.append({**target, 'stage': 'annual_page_contract', 'error': str(exc)}); continue
            if len(downloads) != 1:
                errors.append({**target, 'stage': 'annual_page_contract', 'error': 'Exactly one official profile download required'}); continue
            reference = downloads[0]; references.append(reference)
            url = reference['url']; resolution = []
            try:
                if reference['download_kind'] == 'linked_google_drive':
                    file_id = re.search(r'/file/d/([A-Za-z0-9_-]+)', url)[1]
                    url = 'https://drive.google.com/uc?export=download&id=' + file_id
                elif reference['download_kind'] == 'official_shortlink':
                    short_code = urlparse(url).path.strip('/')
                    resolver = f'https://s.kemkes.go.id/slink/public/links/{short_code}/'
                    payload = await request(resolver, endpoint='shortlink_resolver', year=target['year'])
                    if payload is None: continue
                    resolution.append({'endpoint':'shortlink_resolver','url':resolver,'sha256':hashlib.sha256(payload).hexdigest()})
                    share_url = shortlink_target(payload)
                    if await request(share_url, endpoint='public_share_view', year=target['year']) is None: continue
                    sharing_id = urlparse(share_url).path.rsplit('/',1)[1]
                    share_metadata = (f'https://drive.kemkes.go.id:5001/sharing/webapi/entry.cgi?api=SYNO.Core.Sharing.Session&version=1&method=get&sharing_id=%22{sharing_id}%22&sharing_status=%22none%22&v=1647950553')
                    payload = await request(share_metadata, endpoint='public_share_metadata', year=target['year'])
                    if payload is None: continue
                    resolution.append({'endpoint':'public_share_metadata','url':share_metadata,'sha256':hashlib.sha256(payload).hexdigest()})
                    url = share_download(payload, share_url=share_url)
                payload = await request(url, endpoint='profile_pdf', year=target['year'])
                if payload is None: continue
                if not payload.startswith(b'%PDF') or b'%%EOF' not in payload[-65536:]:
                    raise IndonesiaContractError('Official download is not a complete PDF')
                captured.append({'reference':reference, 'request_url':url,
                    'sha256':hashlib.sha256(payload).hexdigest(), 'resolution':resolution})
            except IndonesiaContractError as exc:
                errors.append({**reference, 'stage':'download_contract','error':str(exc)})
        await writer.write(json.dumps({'targets':targets, 'references':references,
            'captured':captured, 'errors':errors, 'requested_start_year':int(start_year),
            'requested_end_year':end_year, 'retrieved_at':datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False, sort_keys=True).encode(), source_url=CHANNEL_URL,
            kind='archive_json', metadata={'endpoint':'collection_audit'})
        return writer.artifacts
    finally:
        crawler.session.close()


def _parse_one(item, *, cache_directory=None):
    artifact, reference = item
    payload = read_artifact(artifact)
    path = None
    if cache_directory:
        key = hashlib.sha256(json.dumps([artifact.sha256, reference, PARSER_SHA256], sort_keys=True).encode()).hexdigest()
        path = Path(cache_directory) / (key + '.json')
        if path.exists():
            value = json.loads(path.read_text())
            if value['parser_sha256'] != PARSER_SHA256 or value['raw_sha256'] != artifact.sha256:
                raise IndonesiaContractError('Profile parse cache provenance mismatch')
            return value['result']
    try:
        rows, audit = parse_profile_pdf(payload, reference=reference)
        result = rows, audit, None
    except (IndonesiaContractError, ValueError, RuntimeError) as exc:
        result = [], None, {'year':reference['year'], 'url':reference['url'], 'stage':'parse', 'error':str(exc)}
    if path:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'parser_sha256':PARSER_SHA256,'raw_sha256':artifact.sha256,'result':result},ensure_ascii=False))
        temporary.replace(path)
    return result


def organize(artifacts, *, updater, source=DEFAULT_SCOPE, start_year=HISTORY_START_YEAR,
             end_year=None, parse_cache_directory=None, **options):
    updater.normalize_source(source)
    def endpoint(name): return [a for a in artifacts if a.metadata_.get('endpoint') == name]
    indices = endpoint('index'); audits = endpoint('collection_audit')
    if len(indices) != 1 or len(audits) != 1:
        raise IndonesiaContractError('One captured directory and collection audit required')
    collection = json.loads(read_artifact(audits[0]))
    expected = discover_profiles(read_artifact(indices[0]), start_year=start_year, end_year=end_year)
    if collection['targets'] != expected or collection['requested_start_year'] != int(start_year) or collection['requested_end_year'] != end_year:
        raise IndonesiaContractError('Captured discovery plan differs from requested source window')
    targets = {t['page_url']: t for t in expected}; references = []
    for page in endpoint('annual_page'):
        if page.source_url not in targets:
            raise IndonesiaContractError('Unlinked captured annual page')
        discovered = discover_downloads(read_artifact(page), reference=targets[page.source_url])
        if len(discovered) == 1: references.extend(discovered)
    if references != collection['references']:
        raise IndonesiaContractError('Captured downloads differ from official annual pages')
    allowed = {json.dumps(r, sort_keys=True) for r in references}
    pdfs = endpoint('profile_pdf'); selected = []; seen = set()
    for item in collection['captured']:
        reference = item['reference']
        if json.dumps(reference,sort_keys=True) not in allowed:
            raise IndonesiaContractError('Unlinked PDF reference')
        candidates = [a for a in pdfs if a.source_url == item['request_url'] and a.sha256 == item['sha256']]
        if len(candidates) != 1 or (item['request_url'], item['sha256']) in seen:
            raise IndonesiaContractError('Captured PDF does not match collection manifest')
        seen.add((item['request_url'],item['sha256']))
        if reference['download_kind'] == 'direct_pdf':
            expected_url = reference['url']
        elif reference['download_kind'] == 'linked_google_drive':
            file_id = re.search(r'/file/d/([A-Za-z0-9_-]+)',reference['url'])[1]
            expected_url = 'https://drive.google.com/uc?export=download&id=' + file_id
        else:
            chain = item['resolution']
            if [c['endpoint'] for c in chain] != ['shortlink_resolver','public_share_metadata']:
                raise IndonesiaContractError('Missing captured official shortlink resolution')
            resolved = []
            for link in chain:
                matches=[a for a in artifacts if a.source_url==link['url'] and a.sha256==link['sha256'] and a.metadata_.get('endpoint')==link['endpoint']]
                if len(matches)!=1: raise IndonesiaContractError('Resolution artifact mismatch')
                resolved.append(read_artifact(matches[0]))
            share_url = shortlink_target(resolved[0]); expected_url = share_download(resolved[1], share_url=share_url)
        if item['request_url'] != expected_url:
            raise IndonesiaContractError('PDF request differs from captured official link')
        selected.append((candidates[0],reference))
    # A complete non-PDF response can be archived for diagnosis, but can never
    # silently become a successful parsed book.
    if any((a.source_url,a.sha256) not in seen and a.metadata_.get('status_code') == 200 for a in pdfs):
        unclaimed=[a for a in pdfs if (a.source_url,a.sha256) not in seen]
        if any(not any(e.get('stage')=='download_contract' and e.get('year')==a.metadata_.get('year') for e in collection['errors']) for a in unclaimed):
            raise IndonesiaContractError('Unmanifested PDF response')
    rows=[]; reports=[]; errors=[e for e in collection['errors'] if not e.get('resolved_by_retry')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for current, audit, error in pool.map(lambda item:_parse_one(item,cache_directory=parse_cache_directory), selected):
            if error: errors.append(error)
            else: rows.extend(current); reports.append(audit)
    if not rows: raise IndonesiaContractError('No independently validated annual profile values')
    rows.sort(key=lambda row:(row['Date'],row['SourceReportYear'],row['SourceDiseaseCode']))
    audit={'status':'partial','published_profiles':len(expected),'captured_profiles':len(selected),
        'validated_profiles':len(reports),'rows':len(rows),'observed_values':sum(r['Cases']!='' for r in rows),
        'published_years':[t['year'] for t in expected],'report_years':sorted(r['year'] for r in reports),
        'statistic_years':sorted({int(r['Year']) for r in rows}), 'reports':reports,'failed_reports':errors,
        'collection_attempt_errors':collection['errors'],
        'parser_sha256':PARSER_SHA256,'public_release_enabled':False,'license_review_status':'unverified',
        'authoritative_replacement':False,'full_raw_books_preserved':True,
        'numerical_coverage':'partial; unreadable, overlapping and unsupported tables quarantined'}
    return IndonesiaFetchResult(rows,max(date.fromisoformat(r['Date']) for r in rows),updater.output_csv,
        [f'[ID] {len(selected)}/{len(expected)} annual books archived; {len(rows)} verified national source metrics; numerical coverage partial'],audit)
