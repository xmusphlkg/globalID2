from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.configure_half_hour_ingestion import assign_minutes, next_slot


def test_phases_balance_shared_upstreams_and_separate_country_sources():
    jobs = [
        SimpleNamespace(job_id=f"ecdc-{n}", source="ecdc", country_code=f"C{n}")
        for n in range(30)
    ]
    jobs += [
        SimpleNamespace(job_id=f"phf-{n}", source="phf", country_code=f"N{n}")
        for n in range(19)
    ]
    jobs += [SimpleNamespace(job_id="other-c0", source="local", country_code="C0")]
    phases = assign_minutes(jobs)
    assert len({phases[j.job_id] for j in jobs[:30]}) == 30
    assert len({phases[j.job_id] for j in jobs[30:49]}) == 19
    gap = abs(phases["ecdc-0"] - phases["other-c0"])
    assert min(gap, 30 - gap) >= 5
    assert phases == assign_minutes(list(reversed(jobs)))


def test_next_slot_is_within_one_cycle_and_strictly_future():
    for minute in range(60):
        now = datetime(2026, 1, 1, 12, minute, 0, tzinfo=timezone.utc)
        for phase in range(30):
            run = next_slot(now, phase)
            assert 0 < (run - now).total_seconds() <= 1800
            assert run.minute % 30 == phase
