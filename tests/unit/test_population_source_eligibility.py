"""Do not turn sentinel counts or source rates into total-population incidence."""
import sqlite3

import pytest

from src.generation import site_data_queries as queries


def rate_for(*, value=10, metric='case_notifications', basis='notification', unit='count', dimension='all', excluded=False, legacy=False, population=100000, native_rate=None):
    condition = queries._population_count_condition('value', 'metric', 'basis', 'unit', 'dimension', excluded='excluded', legacy_defaults=legacy)
    # Execute the production SQL predicate against semantic examples. Its basic
    # SQL operators are portable, so this needs neither a production DB nor mocks
    # that merely repeat an expected rate from the implementation.
    with sqlite3.connect(':memory:') as db:
        return db.execute(f'''
            WITH inputs(value, metric, basis, unit, dimension, excluded, population, native_rate)
            AS (VALUES(:value, :metric, :basis, :unit, :dimension, :excluded, :population, :native_rate))
            SELECT CASE WHEN population > 0 AND {condition}
                        THEN value * 100000.0 / population ELSE native_rate END,
                   CASE WHEN population > 0 AND {condition} THEN population ELSE NULL END
            FROM inputs
        ''', locals()).fetchone()


@pytest.mark.parametrize('changes', [
    {'metric': 'sentinel_case_notifications', 'basis': 'sentinel_surveillance'},
    {'metric': 'case_notifications', 'basis': 'sentinel_surveillance'},
    {'metric': 'case_notifications', 'basis': 'sentinel'},
    {'metric': 'test_positivity', 'basis': 'laboratory_surveillance', 'unit': 'percent'},
    {'metric': 'incidence_rate', 'unit': 'per_100000'},
    {'metric': 'cases_per_sentinel', 'unit': 'count'},
    {'metric': 'deaths'},
    {'dimension': 'age:0-4'},
    {'value': -1}, {'value': 1.5}, {'value': float('inf')}, {'value': None},
    {'excluded': True}, {'metric': None}, {'basis': 'unreviewed_unknown'},
    {'basis': 'unverified_archive_basis'}, {'metric': 'cases', 'basis': 'cumulative'},
])
def test_ineligible_sources_never_use_total_resident_population(changes):
    assert rate_for(**changes) == (None, None)


def test_true_zero_and_reviewed_all_people_notifications_are_valid():
    assert rate_for() == (10, 100000)
    assert rate_for(value=0) == (0, 100000)
    assert rate_for(population=0) == (None, None)
    assert rate_for(basis='national_notifiable_disease_registry') == (10, 100000)
    assert rate_for(basis='ecdc_member_state_reported_aggregate_surveillance') == (10, 100000)
    assert rate_for(basis='provincial_statutory_infectious_disease_monthly_report') == (10, 100000)
    assert rate_for(basis='national_notifiable_disease_surveillance') == (10, 100000)
    assert rate_for(basis='national_health_profile') == (10, 100000)


def test_explicit_sentinel_native_rate_is_preserved_without_a_population_denominator():
    assert rate_for(value=100, metric='sentinel_case_notifications', basis='sentinel_surveillance', native_rate=2.3) == (2.3, None)
    assert rate_for(metric=None, basis=None, legacy=True) == (10, 100000)
    assert rate_for(metric='test_positivity', basis=None, legacy=True, native_rate=0.25) == (0.25, None)


class CaptureSession:
    def __init__(self):
        self.statements = []
    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize('geography', ['JP', 'JP-13'])
async def test_legacy_query_guards_population_math_and_keeps_native_rate(geography):
    session = CaptureSession()
    await queries.fetch_disease_records_direct(session, geography, True)
    statement = session.statements[0]
    assert "(c.code = 'JP' OR c.code LIKE 'JP-%')" in statement
    assert "sentinel_case_notifications" in statement and "dr.cases" in statement
    assert "dr.metadata::jsonb ->> 'reporting_basis'" in statement
    assert "dr.raw_data::jsonb ->> 'SurveillanceType'" in statement
    assert "ELSE dr.incidence_rate" in statement
    assert "THEN 'original_db'" in statement
    assert "AND NOT (" in statement


@pytest.mark.asyncio
async def test_registry_uses_the_same_count_guard_for_rates_and_population_provenance():
    session = CaptureSession()
    await queries.fetch_disease_series_records(session, 'JP-13', True)
    statement = session.statements[0]
    # rate, rate source, population value and population source share the guard.
    assert statement.count("lower(COALESCE(NULLIF(trim((dss.metric_type))") == 4
    assert statement.count("AND NOT (dss.country_code = 'JP'") == 4
    assert "dso.dimension_key" in statement and "dso.unit" in statement
    assert "registry_country.code = :population_code" in statement
