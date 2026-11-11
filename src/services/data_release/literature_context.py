"""Capture Research Radar's inputs from already published data generations."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
import time
from typing import Any

from src.core import get_logger
from src.generation.literature_publication import _atomic_write
from src.generation.site_data_catalogue import enrich_diseases_with_ontology, load_standard_diseases
from src.knowledge.catalogue import should_generate_public_disease_page
from src.ontology import load_disease_ontology
from .surveillance_publish import validate_manifest

logger = get_logger(__name__)


def public_disease_catalogue(root: Path) -> dict[str, dict]:
    diseases = [item for item in load_standard_diseases(root / 'configs/standard_diseases.csv')
                if should_generate_public_disease_page(item)]
    enrich_diseases_with_ontology(diseases, load_disease_ontology())
    return {item['disease_id']: item for item in diseases}


def _published_object(client, prefix: str, local: Path, manifest: dict, logical: str) -> Any:
    entry = manifest['files'].get(logical)
    if not entry:
        raise ValueError(f'Published {prefix} generation lacks {logical}')
    relative = entry['url']
    path = local / relative
    if path.is_file():
        body = path.read_bytes()
    else:
        body = client.get_object(f'{prefix}/{relative}')
        if body is None:
            raise ValueError(f'Published {prefix} object is unavailable: {logical}')
    return json.loads(body)


def _situation_snapshot(document: dict, release_id: str) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get('outbreaks'), list):
        raise ValueError('Published Situation map has no outbreak list')
    anchor = date.fromisoformat(str(document.get('as_of') or document.get('generated_at') or '')[:10])
    cutoff = anchor - timedelta(days=120)
    latest: dict[str, dict] = {}
    country_names: dict[str, str] = {}
    for point in document.get('points') or []:
        if not isinstance(point, dict):
            continue
        code = point.get('country_code')
        if code and point.get('country_name_en'):
            country_names[code] = point['country_name_en']
        key = point.get('outbreak_id')
        if key and (key not in latest or (str(point.get('published_date') or ''), str(point.get('alert_id') or ''))
                    > (str(latest[key].get('published_date') or ''), str(latest[key].get('alert_id') or ''))):
            latest[key] = point
    emerging = []
    for outbreak in document['outbreaks']:
        if not isinstance(outbreak, dict) or not outbreak.get('outbreak_id') or not outbreak.get('last_published'):
            continue
        published = date.fromisoformat(str(outbreak['last_published'])[:10])
        if published < cutoff or published > anchor:
            continue
        point = latest.get(outbreak['outbreak_id']) or {}
        name = outbreak.get('disease_name_en') or outbreak.get('disease_label') or outbreak.get('disease_id')
        emerging.append({'id': outbreak['outbreak_id'], 'kind': 'official_event', 'title': name,
            'disease_id': outbreak.get('disease_id'), 'disease_name': name,
            'geographies': [{'code': code, 'name': country_names.get(code, code)} for code in outbreak.get('country_codes') or []],
            'published_at': published.isoformat(), 'source': point.get('source_id'), 'risk': {}, 'status': outbreak.get('status')})
    if not emerging:
        return {}
    return {'schema_version': 'situation_alerts.v1', 'public_enabled': True, 'snapshot_id': release_id,
            'generated_at': document.get('generated_at'), 'data_through': max(item['published_at'] for item in emerging),
            'method_version': 'published-situation-map.v1', 'increasing': [], 'unusual': [], 'emerging': emerging}


def capture_literature_context(*, client, root: Path, cache: Path, diseases: dict[str, dict] | None = None,
                               workers: int = 8) -> dict:
    """Pin small remote pointers, then derive coverage from their named objects.

    Unchanged disease objects reuse derived local receipts. No paper files or
    remote objects are read back for verification.
    """
    if not 1 <= workers <= 16:
        raise ValueError('Context workers must be 1–16')
    started = time.monotonic()
    root, cache = Path(root), Path(cache)
    diseases = diseases if diseases is not None else public_disease_catalogue(root)
    manifests = {}
    for channel in ('surveillance', 'situation'):
        body = client.get_object(f'site-data/{channel}/current.json')
        if body is None:
            raise ValueError(f'Published {channel} pointer is unavailable')
        manifests[channel], _ = validate_manifest(body)
    surveillance = manifests['surveillance']

    def coverage(item):
        disease_id, _ = item
        logical = f'diseases/{disease_id.lower()}.json'
        descriptor = surveillance['files'].get(logical)
        if descriptor is None:
            raise ValueError(f'Published surveillance lacks {disease_id}')
        receipt = cache / 'coverage' / f"{descriptor['sha256']}.json"
        try:
            cached = json.loads(receipt.read_bytes())
            if isinstance(cached, list) and all(isinstance(code, str) for code in cached):
                return disease_id, cached, True
        except (OSError, ValueError):
            pass
        value = _published_object(client, 'site-data/surveillance', root / 'exports/surveillance-data', surveillance, logical)
        if not isinstance(value, dict) or not isinstance(value.get('series'), list):
            raise ValueError(f'Published surveillance series is unavailable: {disease_id}')
        codes = sorted({str(series['cc']) for series in value['series'] if isinstance(series, dict) and series.get('cc')})
        _atomic_write(receipt, json.dumps(codes, separators=(',', ':')).encode())
        return disease_id, codes, False

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='research-context') as pool:
        results = list(pool.map(coverage, diseases.items()))
    situation = manifests['situation']
    map_data = _published_object(client, 'site-data/situation', root / 'exports/situation-data', situation, 'map.json')
    context = {'diseases': diseases, 'surveillance_coverage': {did: codes for did, codes, _ in results},
        'situation': _situation_snapshot(map_data, situation['release_id']),
        'publication_inputs': {name: manifest['release_id'] for name, manifest in manifests.items()}}
    logger.info('Research context captured: diseases={} cached_coverage={} situation_outbreaks={} seconds={:.2f}',
                len(results), sum(reused for _, _, reused in results), len(context['situation'].get('emerging', [])),
                time.monotonic() - started)
    return context
