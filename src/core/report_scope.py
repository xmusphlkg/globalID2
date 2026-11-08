"""Country boundary for the report pilot, shared by every generation entry point."""

REPORT_PILOT_COUNTRY = "JP"


def require_report_country(country_code: str) -> str:
    code = str(country_code or "").strip().upper()
    if code != REPORT_PILOT_COUNTRY:
        raise ValueError("Report generation is currently limited to the Japan (JP) pilot")
    return code
