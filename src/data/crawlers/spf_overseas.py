"""Reviewed SPF ODISSÉ annual notification datasets for three overseas territories."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import urlencode

from .base import BaseCrawler
from src.core.remaining_country_sources import source_contract

DEFAULT_SCOPE = 'spf_odisse_annual'
BASE_URL = 'https://odisse.santepubliquefrance.fr/api/explore/v2.1/catalog/datasets/'
TERRITORIES = {'GP': ('971', '01', 'Guadeloupe'), 'MQ': ('972', '02', 'Martinique'), 'GF': ('973', '03', 'Guyane')}
HISTORY_START_YEAR = 1995


@dataclass(frozen=True)
class Dataset:
    code: str
    title: str
    geography: str
    count: str
    rate: str
    strata: tuple[str, ...] = ()


DATASETS = {
    'hepatite-a-donnees-declaration-obligatoire': Dataset('hepatitis_a', 'Hépatite A : Données du signalement obligatoire (Département)', 'dep', 'nbcas', 'taux'),
    'legionellose-donnees-declaration-obligatoire': Dataset('legionellosis', 'Légionellose : Données de la déclaration obligatoire (Département)', 'dep', 'legio', 'taux'),
    'rougeole-donnees-declaration-obligatoire': Dataset('measles', 'Rougeole : Données de la déclaration obligatoire', 'dep', 'rou', 'tx', ('mdo_cl_age_rougeole',)),
    'tuberculose-donnees-declaration-obligatoire_dep': Dataset('tuberculosis', 'Tuberculose : Données du signalement obligatoire (Département)', 'departement', 'nombre_de_cas_notifies_de_tuberculose', 'taux_brut_de_cas_notifies_de_tuberculose'),
    'infection-invasive-a-meningocoque-notifications-region': Dataset('invasive_meningococcal', 'Infection Invasive à Méningocoque : Notifications (Région)', 'reg', 'nombre_de_cas', 'tx', ('classe_age', 'meningocoque')),
}


def country_spec(country_code):
    try:
        return TERRITORIES[str(country_code).upper()]
    except KeyError:
        raise ValueError(f'Unreviewed SPF territory {country_code!r}') from None


def metadata_url(dataset):
    if dataset not in DATASETS:
        raise ValueError('Unreviewed SPF dataset')
    return BASE_URL + dataset


def records_url(dataset, country_code, *, offset=0, limit=100):
    spec = DATASETS[dataset]
    dep, reg, _ = country_spec(country_code)
    code = reg if spec.geography == 'reg' else dep
    return metadata_url(dataset) + '/records?' + urlencode({
        'where': f'{spec.geography}="{code}"', 'limit': limit, 'offset': offset,
        'order_by': ','.join(('annee',) + spec.strata),
    })


def validate_metadata(dataset, metadata):
    spec = DATASETS[dataset]
    if metadata.get('dataset_id') != dataset:
        raise ValueError('SPF dataset identity changed')
    meta = metadata['metas']['default']
    if meta.get('publisher') != 'Santé publique France' or meta.get('title') != spec.title:
        raise ValueError('SPF publisher or disease title changed')
    if meta.get('license') != 'Licence ouverte 2.0' or meta.get('update_frequency') != 'ANNUAL':
        raise ValueError('SPF reuse/cadence contract changed')
    expected = {'annee', spec.geography, spec.count, spec.rate, 'reglib', *spec.strata}
    if not expected.issubset({f['name'] for f in metadata['fields']}):
        raise ValueError('SPF required dataset fields changed')
    description = re.sub('<[^>]*>', ' ', meta.get('description', '')).lower()
    if 'signalement' not in description and 'déclaration' not in description:
        raise ValueError('SPF notification definition is missing')
    return meta


def page_document(payload):
    data = json.loads(payload)
    if not isinstance(data, dict) or type(data.get('total_count')) is not int or data['total_count'] < 0 or not isinstance(data.get('results'), list):
        raise ValueError('SPF response is not a successful counted record page')
    return data


def count_value(value):
    if value is None or value in ('', 'NA', 'N/A', 'ND', '-'):
        return '', 'source_missing'
    if isinstance(value, str) and (value == '*' or re.fullmatch(r'<\s*\d+', value)):
        return value, 'source_suppressed'
    if isinstance(value, bool):
        raise ValueError('Boolean SPF count')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Unreviewed SPF count notation') from None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError('SPF notification counts must be nonnegative integers')
    return str(int(number)), 'reported'


def parse_records(records, *, country_code, metadata, as_of):
    """records contain (dataset id, row, captured provenance); never sum strata."""
    dep, reg, name = country_spec(country_code)
    country_code = country_code.upper()
    for dataset in DATASETS:
        validate_metadata(dataset, metadata[dataset])
    seen, rows = set(), []
    for dataset, raw, provenance in records:
        spec = DATASETS[dataset]
        if not {'annee', spec.geography, spec.count, spec.rate, 'reglib', 'reg', *spec.strata}.issubset(raw):
            raise ValueError('SPF required record fields are missing')
        if raw.get('reg') != reg:
            raise ValueError('SPF region code disagrees with territory')
        if raw.get(spec.geography) != (reg if spec.geography == 'reg' else dep) or raw.get('reglib') != name:
            raise ValueError('SPF record does not match the requested territory')
        if spec.geography != 'reg' and raw.get('libgeo') != name:
            raise ValueError('SPF department label disagrees with territory')
        year_text = str(raw.get('annee', ''))
        if not re.fullmatch(r'\d{4}', year_text) or not HISTORY_START_YEAR <= int(year_text) < as_of.year:
            raise ValueError('SPF year must be a reviewed closed calendar year')
        year = int(year_text)
        strata = {key: raw[key] for key in spec.strata}
        if any(not isinstance(v, str) or not v.strip() for v in strata.values()):
            raise ValueError('SPF stratum label is missing')
        identity = (dataset, year, tuple(strata.items()))
        if identity in seen:
            raise ValueError('Duplicate SPF territory/year/stratum record')
        seen.add(identity)
        value, status = count_value(raw.get(spec.count))
        # TB department changed from reporting location to residence in 2019.
        basis = 'notification_department' if spec.code == 'tuberculosis' and year <= 2018 else 'residence_department'
        if spec.code == 'measles':
            basis = 'residence_or_notifier_department_rash_year'
        elif spec.code == 'invasive_meningococcal':
            basis = 'residence_or_notifier_region'
        suffix = '__' + hashlib.sha256(json.dumps(strata, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16] if strata else ''
        source_code = spec.code + suffix
        meta = metadata[dataset]['metas']['default']
        rate = raw.get(spec.rate)
        if rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not Decimal(str(rate)).is_finite() or rate < 0):
            raise ValueError('Unreviewed SPF notification rate')
        rows.append({
            'Date': f'{year}-01-01', 'Year': str(year), 'Month': '1',
            'PeriodStart': f'{year}-01-01', 'PeriodEnd': f'{year}-12-31',
            'DiseaseCode': '__source_native__', 'SourceDiseaseCode': source_code,
            'RawDiseaseLabel': spec.title + ('; ' + '; '.join(f'{k}: {v}' for k, v in strata.items()) if strata else ''),
            'Cases': value, 'MissingValueStatus': status, 'Frequency': 'annual', 'PeriodType': 'annual',
            'Measure': 'case_notifications', 'Unit': 'count', 'ReportingBasis': basis,
            'DefinitionVersion': f'spf_odisse_{spec.code}_{basis}_v1',
            'Geography': name, 'GeographyKey': f'country:{country_code}:national',
            'Source': source_contract(country_code)['label_en'],
            'SourceURL': 'https://odisse.santepubliquefrance.fr/explore/dataset/' + dataset + '/',
            'SourcePublishedAt': meta['modified'], 'SourceDataset': dataset,
            'SourceIncidenceRatePer100000': rate, 'SourcePopulation': raw.get('population'),
            'Dimensions': json.dumps({'dataset': dataset, **strata}, sort_keys=True, ensure_ascii=False),
            'DatasetStatus': 'closed_revisable', 'IsProvisional': 'false',
            'RevisionSemantics': 'authoritative_revision', 'AuthoritativeRevision': 'true',
            'PublicReleaseEnabled': 'false', 'AggregationPolicy': 'non_additive', **provenance,
        })
    if not rows:
        raise ValueError('No verified SPF territory observations')
    return rows


class SPFOverseasCrawler(BaseCrawler):
    def __init__(self):
        super().__init__(timeout=60, delay=0.1)

    async def crawl(self, **kwargs):
        raise NotImplementedError('Use staged SPF capture')

    def parse(self, response):
        raise NotImplementedError('SPF parsing requires captured metadata and page provenance')
