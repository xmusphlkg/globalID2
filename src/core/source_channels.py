"""Saved official update channels used by ingestion jobs and the control panel."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlparse

from src.core.source_scopes import canonicalize_task_source

CHANNELS_PATH = Path(__file__).resolve().parents[2] / 'configs/surveillance_channels.json'


def load_source_channels() -> list[dict]:
    document = json.loads(CHANNELS_PATH.read_text(encoding='utf-8'))
    if document.get('schema_version') != 1:
        raise ValueError('Unsupported surveillance channel schema')
    channels = document['channels']
    seen = set()
    for channel in channels:
        key = (channel['country_code'], channel['scope'])
        if key in seen or canonicalize_task_source(channel['scope'], country_code=key[0]) != key[1]:
            raise ValueError(f'Duplicate or noncanonical source channel: {key}')
        seen.add(key)
        for url in [channel['official_url'], channel['archive_url'], *channel['discovery_urls']]:
            parsed = urlparse(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Official update channels require credential-free HTTPS URLs')
        if not 1 <= int(channel['refresh_years']) <= 24:
            raise ValueError('Invalid source revision window')
    return channels


def source_channel(country_code: str | None, scope: str | None) -> dict | None:
    code = str(country_code or '').upper()
    canonical = canonicalize_task_source(scope, country_code=code)
    return next((deepcopy(channel) for channel in load_source_channels()
                 if (channel['country_code'], channel['scope']) == (code, canonical)), None)
