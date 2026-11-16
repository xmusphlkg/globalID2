from src.services.cn_province_month_quality import detect_previous_month_copy


def row(month: str, name: str, value: int):
    return {"time": month + "-01", "series_code": "SER_CN_PROV_REPORT_" + name, "value": value}


def test_flags_anhui_style_prior_month_copy_without_treating_zeros_as_evidence():
    previous = [row("2023-11", name, value) for name, value in {
        "INFLUENZA": 52087, "HFMD": 1768, "INFECTIOUS_DIARRHEA": 5451,
        "ACUTE_HEMORRHAGIC_CONJUNCTIVITIS": 301, "MUMPS": 418,
        "LEPROSY": 0, "RUBELLA": 4, "TYPHUS": 9,
    }.items()]
    candidate = [dict(item, time="2023-12-01") for item in previous]
    finding = detect_previous_month_copy(candidate, previous)
    assert finding["reason"] == "suspected_previous_month_copy"
    assert finding["matched_count"] == 5
    assert {item["value"] for item in finding["matches"]} == {52087, 1768, 5451, 301, 418}


def test_ordinary_stable_zero_and_small_counts_do_not_trigger_review():
    previous = [row("2026-09", f"D{i}", value) for i, value in enumerate([0, 0, 1, 2, 3, 99, 101])]
    candidate = [dict(item, time="2026-10-01") for item in previous]
    assert detect_previous_month_copy(candidate, previous) is None


def test_death_series_and_nonadjacent_month_cannot_trigger_review():
    previous = [row("2026-08", f"D{i}", 500 + i) for i in range(5)]
    candidate = [dict(item, time="2026-10-01") for item in previous]
    assert detect_previous_month_copy(candidate, previous) is None
    previous = [row("2026-09", f"DEATHS_D{i}", 500 + i) for i in range(5)]
    candidate = [dict(item, time="2026-10-01") for item in previous]
    assert detect_previous_month_copy(candidate, previous) is None


def test_ambiguous_candidate_does_not_claim_copy():
    previous = [row("2026-09", f"D{i}", 500 + i) for i in range(5)]
    candidate = [dict(item, time="2026-10-01") for item in previous]
    candidate.append(dict(candidate[0], value=999))
    assert detect_previous_month_copy(candidate, previous) is None
