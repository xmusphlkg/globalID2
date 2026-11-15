"""History preparation preserves source-specific missing values and traceability."""
import json
from datetime import datetime, timezone

import pytest

from src.services.database_rebuild_import import prepare_history_row


@pytest.mark.parametrize('cases,expected', [(None, None), ('-10', None), (0, 0), (14, 14)])
def test_nndss_history_preserves_missing_cases_and_unreported_deaths(cases, expected):
    row = {'date': '2026-11-01', 'disease': 'Measles', 'cases': cases,
           'deaths': None, 'Source': 'US CDC NNDSS', 'ReportingArea': 'US RESIDENTS'}
    prepared, unmapped = prepare_history_row(
        row, 7, columns=row.keys(), field_columns=('date', 'disease', None, 'cases', 'deaths'),
        country_code='US', country_id=1, mapping_dict={'measles': 2},
        source_filename='history.csv', normalize_report_time=lambda _: datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    assert unmapped is None
    if expected is None:
        assert prepared is None
        return
    assert prepared['cases'] == expected
    assert prepared['deaths'] is None
    assert prepared['disease_id'] == 2
    metadata = json.loads(prepared['metadata'])
    assert metadata['population_scope'] == 'us_residents_excluding_territories'
    assert metadata['reporting_area'] == 'US RESIDENTS'
    assert metadata['row_index'] == 7
    assert json.loads(prepared['raw_data'])['cases'] == cases


def test_unmapped_history_row_reports_its_label_without_writing():
    row = {'date': '2026-11-01', 'disease': 'Unmapped', 'cases': 5, 'deaths': None}
    assert prepare_history_row(
        row, 0, columns=row.keys(), field_columns=('date', 'disease', None, 'cases', 'deaths'),
        country_code='US', country_id=1, mapping_dict={}, source_filename='history.csv',
        normalize_report_time=lambda _: pytest.fail('An unmapped observation has no date conversion'),
    ) == (None, 'Unmapped')
