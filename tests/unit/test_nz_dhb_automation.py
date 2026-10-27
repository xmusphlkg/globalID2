from scripts.configure_nz_dhb_automation import desired_jobs
from src.data.crawlers.nz import DHB_JURISDICTIONS


def test_all_nz_annual_dhb_scopes_have_staggered_refresh_jobs() -> None:
    jobs = desired_jobs()

    assert len(jobs) == len(DHB_JURISDICTIONS) == 19
    assert {item["country_code"] for item in jobs.values()} == set(
        DHB_JURISDICTIONS.values()
    )
    assert len({item["daily_time"] for item in jobs.values()}) == 19
    assert all(
        item["source"] == "phf_dhb_annual"
        and item["enabled"]
        and item["process"]
        and item["timezone"] == "Pacific/Auckland"
        for item in jobs.values()
    )
