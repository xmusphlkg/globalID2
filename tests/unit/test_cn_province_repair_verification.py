"""Independent verification rejects missing, altered or inadequately archived facts."""
from copy import deepcopy
from decimal import Decimal

import pytest

from scripts.verify_cn_province_repair import fact_key, verify_snapshot


def fact(series="SER_CN_PROV_REPORT_HEPATITIS_A", value=3, raw=None):
    return {"time":"2026-09-01", "series_code":series, "geography_key":"country:CN-SX:national",
            "dimension_key":"all", "value":value, "unit":"count", "suppressed":False,
            "suppression_reason":None, "quality_status":"raw", "dimensions":{}, "metadata":{},
            "raw_data":raw or {"SourceURL":"https://official.example/original"},
            "metric_type":"case_notifications", "source_system":"SRC_CN_PROV_MONTHLY_REPORT",
            "source_label":"Hepatitis A", "updated_at":"2026-11-15 00:00:00"}


def sample():
    old=fact(value=1)
    untouched=fact("SER_CN_PROV_REPORT_HEPATITIS_B",5)
    guard=[{"geography_key":"country:CN:national", "source_system":"national", "observations":10,
            "total_value":100, "first_date":"2004-01-01", "last_date":"2026-09-01"}]
    before={"observations":[old,untouched],"protected_scope_totals":guard}
    new=fact(value=3,raw={"RepairPlanSHA256":"plan", "SourceURL":"https://official.example/corrected"})
    after={"observations":[new,deepcopy(untouched)],"protected_scope_totals":deepcopy(guard)}
    plan={"snapshot_sha256":"snapshot","changes":[{"identity":fact_key(old), "operation":"update", "new_value":3}]}
    receipt={"plan_sha256":"plan", "finished_at":"2026-11-16T00:00:00Z", "provinces":[{"province":"CN-SX","planned":1}]}
    return before,plan,receipt,after,[deepcopy(old)]


def verify(values):
    return verify_snapshot(*values,plan_sha256="plan",snapshot_sha256="snapshot",province_codes=["CN-SX","CN-XZ"])


def test_complete_verified_repair_includes_per_province_metric_and_month_coverage():
    values=sample()
    values[0]['observations'][0]['value']=str(Decimal('1.0000'))
    result=verify(values)
    assert result['status']=='passed'
    assert result['checks']['original_revisions_preserved']==1
    sx=next(p for p in result['provinces'] if p['province']=='CN-SX')
    assert sx['after']['metrics']['case_notifications']['months_with_known_value_since_2022']==1
    assert next(p for p in result['provinces'] if p['province']=='CN-XZ')['after']['observations']==0


@pytest.mark.parametrize('alteration,expected',[
    (lambda x:x[3]['observations'][0].update(value=99),'planned_value_mismatch'),
    (lambda x:x[3]['observations'][0]['raw_data'].update(RepairPlanSHA256='other'),'planned_observation_marker_mismatch'),
    (lambda x:x[3]['observations'][1]['raw_data'].update(SourceURL='different'),'unplanned_old_observation_changed'),
    (lambda x:x[3]['protected_scope_totals'][0].update(total_value=101),'protected_scope_totals_changed'),
    (lambda x:x[4][0]['raw_data'].update(SourceURL='wrong-old-evidence'),'original_state_missing_from_immutable_revisions'),
    (lambda x:x[3]['observations'].append(fact('unexpected_metric',7)),'unplanned_new_observation'),
    (lambda x:x[2].pop('finished_at'),'apply_receipt_incomplete'),
])
def test_verification_refuses_semantic_and_provenance_mismatches(alteration,expected):
    values=sample()
    alteration(values)
    result=verify(values)
    assert result['status']=='failed'
    assert expected in result['failure_counts']


def test_withdrawal_remains_unknown_and_does_not_count_as_known_month():
    values=sample()
    values[1]['changes'][0].update(operation='withdraw_unsupported_narrative',new_value=None)
    values[3]['observations'][0].update(value=None)
    values[3]['observations'][0]['raw_data']['MissingValueStatus']='source_missing'
    result=verify(values)
    assert result['status']=='passed'
    metric=result['provinces'][0]['after']['metrics']['case_notifications']
    assert metric['observations']==2 and metric['known_values']==1 and metric['unknown_values']==1
