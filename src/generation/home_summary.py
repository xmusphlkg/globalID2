"""Small public landing-page projections; detailed datasets remain independent."""
from datetime import date
import unicodedata
from urllib.parse import urljoin, urlsplit


def surveillance_home_summary(meta: dict) -> dict:
    def subdivision(row):
        return row.get('location_type') == 'subdivision' or bool(row.get('parent_code')) or '-' in str(row.get('code', ''))

    def available(row):
        count = row.get('record_count')
        if count is None:
            count = row.get('disease_count') or 0
        return row.get('data_available') is not False and (float(count) > 0 or row.get('data_available') is True)

    rows = [r for r in meta.get('countries', []) if available(r)]
    national = [r for r in rows if not subdivision(r)]
    authorities = set()
    for row in national:
        info = row.get('source_info') or {}
        for group in info if isinstance(info, list) else [info]:
            sources = group.get('sources') or ([{'label': group.get('primary_label'), 'url': group['primary_url']}] if group.get('primary_url') else [])
            for source in sources:
                label = source.get('label') or source.get('scope') or group.get('primary_label')
                url = source.get('url')
                if not isinstance(url, str) or not url.strip() or urlsplit(urljoin('https://local.invalid', url)).scheme not in ('http', 'https'):
                    url = None
                if label or url:
                    authorities.add(label or url)

    def compact(row):
        value = {k: row.get(k) for k in ('code', 'name', 'name_en', 'name_zh', 'name_fr', 'disease_count', 'total_cases', 'date_range')}
        info = row.get('source_info') or {}
        sources = info.get('sources') or [] if isinstance(info, dict) else []
        value['source_info'] = {'sources': [{'cadence': sources[0].get('cadence')}] if sources else []}
        return value

    return {'schema_version': 1, 'generated_at': meta.get('generated_at'),
            'national_count': len(national), 'subdivision_count': len(rows) - len(national),
            'total_diseases': meta.get('total_diseases'), 'official_source_count': len(authorities),
            'recent_countries': [compact(r) for r in sorted(national, key=lambda r: str((r.get('date_range') or {}).get('end') or ''), reverse=True)[:3]],
            'featured_countries': [compact(r) for r in sorted(national, key=lambda r: float(r.get('record_count') or 0), reverse=True)[:6]]}


def situation_home_summary(mapping: dict) -> dict:
    """Match the map's default 30-day window and place deduplication."""
    as_of = date.fromisoformat(mapping['as_of'][:10])
    rows = []
    for point in mapping.get('points', []):
        try:
            age = (as_of - date.fromisoformat(point['published_date'][:10])).days
        except (ValueError, KeyError, TypeError):
            continue
        if 0 <= age <= 30:
            rows.append(point)
    rows.sort(key=lambda p: str(p.get('alert_id') or ''))
    rows.sort(key=lambda p: p['published_date'], reverse=True)
    normalize = lambda s: ' '.join(unicodedata.normalize('NFKC', s or '').strip().split()).lower()
    seen, ids = set(), set()
    for row in rows:
        key = (row.get('outbreak_id') or row.get('alert_id'), row.get('disease_id') or normalize(row.get('disease_label')),
               row.get('country_code'), normalize(row.get('admin_area')), row.get('published_date'), row.get('status'))
        if row.get('alert_id') in ids or key in seen:
            continue
        ids.add(row.get('alert_id'))
        seen.add(key)
    return {**{k: mapping.get(k) for k in ('schema_version', 'generated_at', 'as_of', 'active_outbreaks', 'sources_ok', 'source_count')},
            'recent_places': len(seen)}
