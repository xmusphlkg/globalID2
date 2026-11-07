"""Offline tests for the ECDC CDTR archive listing."""

from __future__ import annotations

from src.services.global_sources.ecdc_cdtr import normalize_archive

ARCHIVE_PAGE = """
<div class="ecdc-card__body">
  <p class="ecdc-card__title"><a href="/en/publications-data/communicable-disease-threats-report-21-27-march-2024-week-13">
    Communicable disease threats report, 21-27 March 2024, week 13</a></p>
  <time datetime="2024-03-28T12:00:00+01:00">28 March 2024</time>
</div>
<a href="/en/publications-and-data/monitoring/weekly-threats-reports">Weekly threats reports (CDTR) archive</a>
"""


def test_archive_keeps_a_weekly_issue_whose_title_names_no_disease():
    events = normalize_archive(ARCHIVE_PAGE)
    assert len(events) == 1
    event = events[0]
    assert event["native_id"] == "communicable-disease-threats-report-21-27-march-2024-week-13"
    assert event["published_date"] == "2024-03-28"
    assert event["diseases"] == []
    assert event["record_type"] == "weekly_threat_report"
    assert event["document_ref"]["page_url"].endswith("week-13")
