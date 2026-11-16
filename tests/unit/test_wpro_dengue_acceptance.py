"""Independent acceptance of source-stated WPRO intervals at year boundaries."""
from datetime import date

import pytest

from src.data.crawlers import wpro_dengue as source


def test_native_extraction_shares_the_application_pdfium_lock():
    # CR, WPRO and situation-notice workers may run in different threads in
    # one process. Separate connector locks cannot protect PDFium's globals.
    from src.services.situation_notices.documents import _PDFIUM_LOCK
    assert source._PDFIUM_LOCK is _PDFIUM_LOCK


@pytest.mark.parametrize('period,start,end', [
    ('From 25 to 31 December 2025', date(2025, 12, 25), date(2025, 12, 31)),
    ('From 29 December to 4 January 2026', date(2025, 12, 29), date(2026, 1, 4)),
    ('From 29 December 2025 to 4 January 2026', date(2025, 12, 29), date(2026, 1, 4)),
    ('From 26 November to 2 December 2025', date(2025, 11, 26), date(2025, 12, 2)),
])
def test_source_week_can_precede_publication_year_without_iso_inference(period, start, end):
    actual_start, actual_end, label = source.parse_period(period, 2026)
    assert (actual_start, actual_end) == (start, end)
    assert label == period


@pytest.mark.parametrize('period', [
    'From 1 to 8 January 2026',  # eight days
    'From 29 December 2024 to 4 January 2026',
    'From 1 to 7 January 2027',  # future source year
    'From 29 to 4 January 2026',  # missing start month cannot imply December
    'From 1 to 7 January',  # source year absent
])
def test_unverified_or_future_source_intervals_fail_closed(period):
    with pytest.raises(ValueError):
        source.parse_period(period, 2026)


def narrative(country, period, *, published='8 January 2026'):
    _, heading, agency = source.COUNTRIES[country]
    return f'Dengue Situation Update 100\n{published}\n{heading}\n{period}, 123 dengue cases were reported.\nFigure 1: Cases\n{agency}\n'


@pytest.mark.parametrize('country', ['LA', 'VN'])
@pytest.mark.parametrize('period,end', [
    ('From 25 to 31 December 2025', '2025-12-31'),
    ('From 29 December to 4 January 2026', '2026-01-04'),
])
def test_january_publication_retains_actual_previous_year_or_cross_year_observation(monkeypatch, country, period, end):
    monkeypatch.setattr(source, 'extract_text', lambda _: narrative(country, period))
    rows, audit = source.parse_report(b'%PDF offline narrative', country_code=country,
        provenance={'RawSHA256': 'source-hash'}, as_of=date(2026, 1, 8))
    assert len(rows) == 1 and audit['observation_end'] == end
    row = rows[0]
    assert row['Date'] == row['PeriodEnd'] == end
    assert row['Year'] == end[:4] and row['SourcePublishedAt'] == '2026-01-08'
    assert row['Cases'] == '123' and row['PeriodType'] == 'weekly'
    assert row['GeographyKey'] == f'country:{country}:national'
    assert row['SourceEpiWeek'] == ''  # no invented week number/calendar


def test_explicit_interval_after_publication_still_fails(monkeypatch):
    monkeypatch.setattr(source, 'extract_text', lambda _: narrative('LA', 'From 7 to 13 January 2026'))
    with pytest.raises(ValueError, match='after publication'):
        source.parse_report(b'%PDF offline narrative', country_code='LA', provenance={}, as_of=date(2026, 1, 8))


@pytest.mark.parametrize('period,cumulative,expected', [
    ('From 1 to 7 November 2026', 'Cumulatively, a total of 900 cases had been reported in 2025.', False),
    ('From 25 to 31 December 2025', 'Cumulatively, a total of 900 cases had been reported for this year.', False),
    ('From 25 to 31 December 2025', 'Cumulatively, a total of 900 cases had been reported in 2025.', True),
    ('From 29 December to 4 January 2026', 'Cumulatively, a total of 900 cases, including 2 deaths had been reported in 2026.', True),
])
def test_cumulative_year_must_match_source_interval_not_comparison_or_publication_year(monkeypatch, period, cumulative, expected):
    published = '8 November 2026' if 'November' in period else '8 January 2026'
    text = narrative('LA', period, published=published).replace('\nFigure 1:', ' ' + cumulative + '\nFigure 1:')
    monkeypatch.setattr(source, 'extract_text', lambda _: text)
    rows, audit = source.parse_report(b'%PDF offline narrative', country_code='LA', provenance={}, as_of=date(2026, 11, 16))
    cumulative_rows = [row for row in rows if row['PeriodType'] == 'cumulative']
    assert bool(cumulative_rows) is expected
    assert audit['cumulative_year_mismatch'] is (not expected)
    assert any(row['PeriodType'] == 'weekly' for row in rows)
    for row in cumulative_rows:
        assert row['PeriodStart'] == row['SourceCumulativeYear'] + '-01-01'
        assert row['SourceDateBasis'] == 'publication_dated_cumulative_snapshot'
        assert row['SourceCumulativeCutoffVerified'] == 'false'
        assert row['PeriodEnd'] == row['Date']
