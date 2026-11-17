"""SCB published year-end county resident totals, discovered through PxWeb.

Historical tables lack a total-sex/marital-status cell: their exhaustive,
non-overlapping two sex x four marital-status cells are added once. New CKM
publications expose official total cells; those totals are used directly,
without adding perturbed component counts. National totals never allocate data.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from itertools import product
import re

from scripts.import_subdivision_population import SubdivisionPopulation

SCB_DIRECTORY = 'https://api.scb.se/OV0104/v1/doris/en/ssd/BE/BE0101/BE0101A'
SCB_COUNTIES = dict(zip(
    '01 03 04 05 06 07 08 09 10 12 13 14 17 18 19 20 21 22 23 24 25'.split(),
    'AB C D E F G H I K M N O S T U W X Y Z AC BD'.split(), strict=True,
))
EXPECTED_COUNTY_LABELS = dict(zip(SCB_COUNTIES, [
    'Stockholm', 'Uppsala', 'Södermanland', 'Östergötland', 'Jönköping',
    'Kronoberg', 'Kalmar', 'Gotland', 'Blekinge', 'Skåne', 'Halland',
    'Västra Götaland', 'Värmland', 'Örebro', 'Västmanland', 'Dalarna',
    'Gävleborg', 'Västernorrland', 'Jämtland', 'Västerbotten', 'Norrbotten',
], strict=True))


def population_query(metadata: dict, *, start_year: int = 2000, end_year: int | None = None) -> dict:
    """Select only published annual population, all ages and all residents."""
    end_year = end_year if end_year is not None else date.today().year - 1
    variables = {v['code']: v for v in metadata['variables']}
    if set(variables) != {'Region', 'Civilstand', 'Alder', 'Kon', 'ContentsCode', 'Tid'}:
        raise ValueError('SCB population dimensions changed')
    regions = dict(zip(variables['Region']['values'], variables['Region']['valueTexts'], strict=True))
    if any(regions.get(code, '').removesuffix(' county') != label for code, label in EXPECTED_COUNTY_LABELS.items()):
        raise ValueError('SCB county registry changed')
    choices = {'Region': list(SCB_COUNTIES)}
    labels = {code: dict(zip(variable['values'], variable['valueTexts'], strict=True)) for code, variable in variables.items()}
    old = 'SC' not in labels['Civilstand']
    if old:
        if labels['Civilstand'] != {'OG': 'single', 'G': 'married', 'ÄNKL': 'widowers/widows', 'SK': 'divorced'} or labels['Kon'] != {'1': 'men', '2': 'women'}:
            raise ValueError('SCB exhaustive demographic categories changed')
        choices.update(Civilstand=list(labels['Civilstand']), Kon=['1', '2'], Alder=['tot'])
    else:
        if labels['Civilstand'].get('SC') != 'total, all mariatal statuses' or labels['Alder'].get('TotSA') != 'total, all ages' or labels['Kon'].get('TotSa') != 'total, all men and women':
            raise ValueError('SCB total demographic selectors changed')
        choices.update(Civilstand=['SC'], Kon=['TotSa'], Alder=['TotSA'])
    population_codes = [code for code, label in labels['ContentsCode'].items() if label == 'Population']
    if len(population_codes) != 1 or any(value not in labels['Alder'] for value in choices['Alder']):
        raise ValueError('SCB population observation changed')
    choices['ContentsCode'] = population_codes
    choices['Tid'] = [year for year in variables['Tid']['values'] if re.fullmatch(r'\d{4}', year) and start_year <= int(year) <= end_year]
    if not choices['Tid']:
        raise ValueError('SCB has no completed annual population years')
    return {'query': [{'code': code, 'selection': {'filter': 'item', 'values': choices[code]}} for code in variables], 'response': {'format': 'json-stat2'}}


def _ordered_categories(dimension: dict) -> list[str]:
    index = dimension['category']['index']
    if isinstance(index, list):
        if len(index) != len(set(index)):
            raise ValueError('SCB category indices are not unique')
        return index
    if sorted(index.values()) != list(range(len(index))):
        raise ValueError('SCB category indices are not contiguous and unique')
    return sorted(index, key=index.get)


def parse_population(data: dict, query: dict, source_url: str) -> list[SubdivisionPopulation]:
    ids = data.get('id') or []
    selected = {q['code']: q['selection']['values'] for q in query['query']}
    if set(ids) != set(selected) or data.get('class') != 'dataset':
        raise ValueError('SCB response dimensions changed')
    categories = [_ordered_categories(data['dimension'][code]) for code in ids]
    if any(set(values) != set(selected[code]) for code, values in zip(ids, categories, strict=True)):
        raise ValueError('SCB returned unrequested demographic groups or periods')
    if data.get('size') != [len(values) for values in categories]:
        raise ValueError('SCB response sizes changed')
    units = data['dimension']['ContentsCode']['category'].get('unit', {})
    if any(unit.get('base') not in {'number', 'persons', 'Number', 'number of persons'} or unit.get('decimals') != 0 for unit in units.values()) or not units:
        raise ValueError('SCB population units changed')
    content_extension = data['dimension']['ContentsCode'].get('extension', {})
    reference_periods = content_extension.get('refperiod', {})
    if any(reference_periods.get(code) != '31 December each year' for code in selected['ContentsCode']):
        raise ValueError('SCB population reference period changed')
    values = data.get('value', [])
    cells = list(product(*categories))
    if isinstance(values, list) and len(values) != len(cells):
        raise ValueError('SCB population response is incomplete')
    expected_cells = len(selected['Civilstand']) * len(selected['Kon'])
    grouped = {}
    for index, categories_for_cell in enumerate(cells):
        fields = dict(zip(ids, categories_for_cell, strict=True))
        value = values.get(str(index)) if isinstance(values, dict) else values[index]
        if value is None or isinstance(value, bool):
            raise ValueError('SCB population total contains missing cells')
        numeric = Decimal(str(value))
        if not numeric.is_finite() or numeric < 0 or numeric != numeric.to_integral_value():
            raise ValueError('Invalid SCB population value')
        region, year = fields['Region'], int(fields['Tid'])
        total, count = grouped.get((region, year), (0, 0))
        grouped[(region, year)] = (total + int(numeric), count + 1)
    rows = []
    for (region, year), (value, count) in sorted(grouped.items()):
        if count != expected_cells or value <= 0:
            raise ValueError('SCB population demographic coverage is incomplete')
        rows.append(SubdivisionPopulation('SE-' + SCB_COUNTIES[region], year, value, 'SCB', f'{year}-12-31', source_url, 'year_end_resident_population_ckm' if 'TotSa' in selected['Kon'] else 'year_end_resident_population'))
    if len(rows) != len(SCB_COUNTIES) * len(selected['Tid']):
        raise ValueError('SCB annual county coverage is incomplete')
    return rows


def fetch_se(source) -> list[SubdivisionPopulation]:
    directory = source.fetch('scb_population_catalogue.json', SCB_DIRECTORY).json()
    tables = [item for item in directory if item.get('type') == 't' and item.get('id', '').startswith('Befolkning') and item.get('text', '').startswith('Population by region, marital status, age')]
    if not tables:
        raise ValueError('SCB annual population catalogue changed')
    rows = {}
    ckm_years = set()
    for table in sorted(tables, key=lambda item: (item.get('updated', ''), item['id'])):
        url = SCB_DIRECTORY + '/' + table['id']
        metadata = source.fetch(f"scb_{table['id']}_metadata.json", url).json()
        query = population_query(metadata)
        selected = {item['code']: item['selection']['values'] for item in query['query']}
        declared_years = {int(year) for year in re.findall(r'\b(?:19|20)\d{2}\b', table['text'])}
        if declared_years and max(map(int, selected['Tid'])) != max(declared_years):
            raise ValueError('SCB metadata does not include the catalogue latest annual period')
        if selected['Kon'] == ['TotSa']:
            ckm_years.update(map(int, selected['Tid']))
        data = source.fetch(f"scb_{table['id']}_population.json", url, body=query).json()
        for row in parse_population(data, query, url):
            rows[(row.country_code, row.year)] = row
    # 2025 CKM totals were verified when this adapter was introduced. A stale
    # historical-only catalogue must never be reported as a healthy refresh.
    if not ckm_years or max(ckm_years) < 2025:
        raise ValueError('SCB current CKM annual publication is missing; refusing historical-only refresh')
    return sorted(rows.values(), key=lambda row: (row.country_code, row.year))
