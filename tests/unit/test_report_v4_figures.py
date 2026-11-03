from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.core.report_scope import require_report_country
from src.generation.report_v4.figures import (
    attach_figures,
    figure_candidates,
    figure_contract_issues,
    validate_figure_plan,
)
from src.generation.report_v4.peer_review import sanitize_analysis
from src.services.report_service import ReportService


def inputs():
    packet = {
        "data_signature": "frozen-data", "period": {"start": "2026-01-01", "end": "2026-01-31"},
        "diseases": [{
            "disease_id": "A", "name_en": "Disease A", "name_zh": "甲病",
            "figure_series": [
                {"period": "2026-01-05", "period_end": "2026-01-12", "cases": 12},
                {"period": "2026-01-12", "period_end": "2026-01-19", "cases": 0},
                {"period": "2026-01-19", "period_end": "2026-01-26", "cases": 20},
            ],
        }],
        "attention_ranking": [{"disease_id": "A", "name_en": "Disease A", "attention_score": 70}, {"disease_id": "B", "name_en": "Disease B", "attention_score": 30}],
    }
    document = {"metrics": {"data_signature": "frozen-data"}, "sections": [
        {"type": "signal_evidence", "order": 1, "evidence_refs": ["disease:A.latest_cases", "disease:B.latest_cases"], "figures": []},
        {"type": "disease_context", "order": 2, "evidence_refs": ["disease:A.total_cases"], "figures": []},
    ]}
    return packet, document


def selection(candidate="epidemic_curve:A"):
    return {"candidate_id": candidate, "rationale": "Clarifies the pattern discussed in this section."}


def test_candidates_are_bound_to_section_evidence_and_available_data():
    packet, document = inputs()
    assert {row["candidate_id"] for row in figure_candidates(packet, document)} == {"epidemic_curve:A", "period_comparison_bar:A", "attention_ranking_bar", "attention_dot", "period_cases_bar:A", "cumulative_cases_line:A"}
    document["sections"][1]["evidence_refs"] = ["disease:OTHER.total_cases"]
    assert [row["candidate_id"] for row in figure_candidates(packet, document)] == ["attention_ranking_bar", "attention_dot"]


def test_missing_periods_stay_gaps_and_are_not_adjacent_comparisons():
    packet, document = inputs()
    series = packet["diseases"][0]["figure_series"]
    series[-1]["period"] = "2026-01-26"
    candidates = figure_candidates(packet, document)
    assert not any(row["figure_type"] == "period_comparison_bar" for row in candidates)
    curve = candidates[0]["data"]["points"]
    assert curve[-2] == {"period": "2026-01-19", "cases": None}
    assert curve[1]["cases"] == 0


def test_missing_or_partial_counts_cannot_be_used_in_comparison():
    for update in ({"cases": None}, {"quality_status": "partial_report_window"}):
        packet, document = inputs()
        packet["diseases"][0]["figure_series"][-1].update(update)
        assert not any(row["figure_type"] == "period_comparison_bar" for row in figure_candidates(packet, document))


@pytest.mark.parametrize("plan", [
    [selection("unknown")],
    [selection(), selection("period_comparison_bar:A")],
    [selection(), selection("attention_ranking_bar"), selection()],
    [{**selection(), "option": {"series": [999]}}],
    [{**selection(), "section_type": "decision_summary"}],
    [{**selection(), "rationale": "<script>bad</script>"}],
])
def test_guardrails_reject_misplacement_duplicates_excess_and_executable_input(plan):
    packet, document = inputs()
    candidates = figure_candidates(packet, document)
    _, notes = validate_figure_plan(plan, candidates)
    assert notes
    clean, _ = sanitize_analysis({"analysis": {locale: "A careful interpretation grounded in surveillance evidence." for locale in ("zh", "en", "fr")}, "figure_plan": plan}, task={"figure_candidates": candidates})
    assert not clean["_usable"]


def test_attachment_orders_figures_and_preserves_data_and_section_identity():
    packet, document = inputs()
    original = deepcopy(packet)
    attach_figures(document, packet, [selection(), selection("attention_ranking_bar")])
    assert packet == original
    assert [row["section_type"] for row in document["figures"]] == ["signal_evidence", "disease_context"]
    assert [row["number"] for row in document["figures"]] == [1, 2]
    assert document["figure_data"]["datasets"]["epidemic_curve:A"]["points"][1]["cases"] == 0
    assert not figure_contract_issues(document)
    document["sections"][0]["figures"] = [document["figures"][1]]
    assert "Figure placed in a different section" in figure_contract_issues(document)


def test_empty_plan_has_no_rule_based_chart_filling():
    packet, document = inputs()
    attach_figures(document, packet, [])
    assert document["figures"] == []
    assert document["figure_data"]["datasets"] == {}


def test_durable_exports_retain_chart_values_and_escape_labels():
    from src.generation.formatter import ReportFormatter

    packet, document = inputs()
    attach_figures(document, packet, [selection()])
    document["figures"][0]["title"]["zh"] = "<script>alert('bad')</script>"
    html = str(ReportFormatter._figure_table(document["figures"][0], document, "zh"))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "2026-01-12</td><td>0" in html


def test_only_japan_is_allowed_and_legacy_reviews_are_not_reused():
    assert require_report_country(" jp ") == "JP"
    with pytest.raises(ValueError, match="Japan"):
        require_report_country("US")
    legacy = SimpleNamespace(metadata_={"report_layout": "report_v4", "report_document_v4": {}, "quality_gate": {"passed": True}})
    assert not ReportService._is_reusable_v4_report(legacy)


def rich_inputs():
    from datetime import date, timedelta
    packet, document = inputs()
    start = date(2026, 1, 5)
    packet['period'] = {'start': start.isoformat(), 'end': '2026-03-15'}
    packet['diseases'][0]['figure_series'] = [
        {'period': (start + timedelta(weeks=i)).isoformat(), 'period_end': (start + timedelta(weeks=i+1)).isoformat(), 'cases': i * 10}
        for i in range(10)
    ]
    packet['diseases'][0]['historical_figure_series'] = [
        {'period': (start + timedelta(weeks=i)).isoformat(), 'period_end': (start + timedelta(weeks=i+1)).isoformat(), 'cases': 10 + i}
        for i in range(-4, 10)
    ]
    packet['data_quality'] = {'missing_rates': {'cases': 0, 'deaths': 1, 'incidence_rate': .25}}
    document['sections'].append({'type': 'data_notes', 'order': 3, 'evidence_refs': ['data_quality:missing_rates'], 'figures': []})
    return packet, document


def test_rich_catalog_statistics_are_calculated_before_ai_and_have_durable_tables():
    from src.generation.report_v4.figures import CATALOG
    from src.generation.formatter import ReportFormatter
    packet, document = rich_inputs()
    candidates = {r['figure_type']: r for r in figure_candidates(packet, document)}
    assert set(candidates) == {r['figure_type'] for r in CATALOG}
    assert len(candidates) == 12
    assert candidates['cumulative_cases_line']['data']['points'][-1]['cases'] == 450
    assert [r['cases'] for r in candidates['period_change_bar']['data']['points']] == [10] * 9
    assert candidates['rolling_average_line']['data']['points'][0]['mean'] is None
    assert candidates['rolling_average_line']['data']['points'][2]['mean'] == 10
    assert candidates['case_distribution_boxplot']['data']['summary'] == [0, 22.5, 45, 67.5, 90]
    assert sum(r['count'] for r in candidates['case_distribution_histogram']['data']['bins']) == 10
    assert candidates['missingness_bar']['data']['rows'][1]['score'] == 100
    for candidate in candidates.values():
        draft = deepcopy(document)
        attach_figures(draft, packet, [selection(candidate['candidate_id'])])
        assert not figure_contract_issues(draft)
        table = str(ReportFormatter._figure_table(draft['figures'][0], draft, 'en'))
        assert '<td>' in table
        assert '<td>—</td>' not in table or candidate['figure_type'] in {'rolling_average_line', 'historical_context_line'}


def test_missing_intervals_partial_counts_and_truncation_disable_derived_statistics():
    from datetime import date, timedelta
    for mode in ('missing', 'gap', 'partial', 'truncated'):
        packet, document = rich_inputs()
        series = packet['diseases'][0]['figure_series']
        if mode == 'missing': series[3]['cases'] = None
        elif mode == 'gap': series[3]['period_end'] = '2026-02-01'
        elif mode == 'partial': series[3]['quality_status'] = 'partial_report_window'
        else:
            series[:] = [{'period': (date(2025, 1, 1) + timedelta(days=i)).isoformat(), 'period_end': (date(2025, 1, 1) + timedelta(days=i+1)).isoformat(), 'cases': i} for i in range(60)]
        kinds = {r['figure_type'] for r in figure_candidates(packet, document)}
        assert not kinds.intersection({'cumulative_cases_line', 'rolling_average_line', 'case_distribution_boxplot', 'case_distribution_histogram', 'period_change_bar'})


def test_historical_context_requires_focus_past_and_current_observations_and_preserves_binding():
    packet, document = rich_inputs()
    attach_figures(document, packet, [selection('historical_context_line:A')])
    figure = document['figures'][0]
    assert figure['data_scope'] == 'historical_context'
    dataset = document['figure_data']['datasets'][figure['data_key']]
    assert dataset['report_start'] == '2026-01-05'
    assert dataset['points'][0]['period'] < dataset['report_start']
    dataset['points'][0]['cases'] = 10000
    assert 'Figure dataset changed after evidence binding' in figure_contract_issues(document)
    packet, document = rich_inputs()
    document['sections'][1]['evidence_refs'] = []
    assert not any(r['figure_type'] == 'historical_context_line' for r in figure_candidates(packet, document))
    packet, document = rich_inputs()
    packet['diseases'][0]['historical_figure_series'] = packet['diseases'][0]['figure_series']
    assert not any(r['figure_type'] == 'historical_context_line' for r in figure_candidates(packet, document))


def test_history_preparation_is_focused_bounded_and_segments_sources_at_same_grain():
    import pandas as pd
    from src.generation.report_v4.pipeline import attach_historical_series
    times = pd.date_range('2024-01-01', periods=120, freq='7D', tz='UTC')
    frame = pd.DataFrame([{'disease_id': did, 'time': time, 'cases': 10, 'metadata': {'frequency': 'weekly'}, 'data_source': 'official'} for did in (1, 2) for time in times])
    current = frame[frame.time == times[-2]]
    packet, document = inputs()
    packet['diseases'].append({'disease_id': 'B'})
    diseases = {1: {'code': 'A'}, 2: {'code': 'B'}}
    attach_historical_series(packet, document, current, frame, diseases, times[-2] + pd.Timedelta(days=6))
    history = packet['diseases'][0]['historical_figure_series']
    assert len(history) == 52
    assert history[-1]['period'] == times[-2].date().isoformat()
    assert 'historical_figure_series' not in packet['diseases'][1]
    frame.loc[frame.time < times[-2], 'data_source'] = 'different-source'
    packet['diseases'][0].pop('historical_figure_series')
    attach_historical_series(packet, document, current, frame, diseases, times[-2] + pd.Timedelta(days=6))
    assert len(packet['diseases'][0]['historical_figure_series']) == 52
    assert len({r['segment'] for r in packet['diseases'][0]['historical_figure_series']}) == 2


def test_history_segments_scope_changes_and_rejects_duplicate_cohorts():
    import pandas as pd
    from src.generation.report_v4.pipeline import attach_historical_series
    times = pd.date_range('2026-01-05', periods=5, freq='7D', tz='UTC')
    frame = pd.DataFrame([{'disease_id': 1, 'time': t, 'cases': 10, 'data_source': 'official', 'metadata': {'frequency': 'weekly', 'surveillance_type': 'sentinel'}} for t in times])
    current = frame.tail(1).copy()
    packet, document = inputs()
    frame.at[0, 'metadata'] = {'frequency': 'weekly', 'surveillance_type': 'all_case'}
    attach_historical_series(packet, document, current, frame, {1: {'code': 'A'}}, times[-1] + pd.Timedelta(days=6))
    assert len(packet['diseases'][0]['historical_figure_series']) == 5
    assert len({r['segment'] for r in packet['diseases'][0]['historical_figure_series']}) == 2
    packet['diseases'][0].pop('historical_figure_series')
    duplicated = pd.concat([frame, frame.tail(1)])
    attach_historical_series(packet, document, current, duplicated, {1: {'code': 'A'}}, times[-1] + pd.Timedelta(days=6))
    assert all(r['period'] != times[-1].date().isoformat() for r in packet['diseases'][0].get('historical_figure_series', []))


def test_ai_prompt_deduplicates_alternative_views_and_retains_verified_history():
    from src.generation.report_v4.peer_review import build_review_task
    packet, document = rich_inputs()
    task = build_review_task(evidence_packet=packet, deterministic_document=document)
    candidates = {c['figure_type']: c for c in task['figure_candidates']}
    assert candidates['epidemic_curve']['data_ref'] == candidates['period_cases_bar']['data_ref']
    assert candidates['attention_dot']['data_ref'] == candidates['attention_ranking_bar']['data_ref']
    assert len(task['figure_datasets']) < len(task['figure_candidates'])
    assert task['figure_datasets'][candidates['historical_context_line']['data_ref']]['scope'] == 'historical_context'


def test_background_follows_priority_order_not_database_identifier_order():
    from src.generation.report_v4.composer import _disease_context
    diseases = [{'disease_id': f'D{i}', 'name_en': f'Disease {i}', 'name_zh': f'疾病{i}', 'metrics': {}} for i in range(8)]
    section = _disease_context(diseases, [{'disease_id': f'D{i}'} for i in reversed(range(8))])
    assert section.evidence_refs == [f'disease:D{i}.total_cases' for i in (7, 6, 5, 4, 3)]
