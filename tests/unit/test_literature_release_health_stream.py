from __future__ import annotations

import io
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from src.literature import release_health_stream as stream
from src.literature.health_content_checks import _blocker_categories, _release_checks
from src.literature.health_snapshot import HealthThresholds
from src.literature.release_validation import validate_public_research_payload
from tests.unit.test_literature_health import _healthy_snapshot, _release_payload


def _read(tmp_path, payload):
    path = tmp_path / "release.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return stream.read_streamed_release(path)


def test_streamed_release_matches_in_memory_health_checks(tmp_path):
    payload = _release_payload()
    metrics, status = _read(tmp_path, payload)
    assert status == "ok"
    original = _healthy_snapshot()
    streamed = replace(original, release_payload=None, release_metrics=metrics)
    assert _release_checks(streamed, HealthThresholds()) == _release_checks(original, HealthThresholds())
    assert "articles" not in metrics
    assert "cited_findings" not in metrics["latest_brief"]


def test_streamed_release_preserves_all_validation_categories_and_cross_record_checks(tmp_path):
    payload = _release_payload()
    first = payload["articles"][0]
    first.update(abstract_text="private", editorial_status="review", integrity_status="retracted", classification_version=0)
    first["related_articles"] = [{"article_id": first["article_id"]}]
    first["summary"] = {"en": {"main_findings": "one"}}
    second = deepcopy(first)
    second["published_at"] = "2026-08-17"
    payload["articles"].append(second)
    payload["articles"].append({})
    payload["articles"].append({})
    payload["preprints"] = [deepcopy(first)]
    payload["preprints"][0]["peer_review_status"] = "preprint"
    payload["integrity_alerts"] = [{"event_id": "event", "raw_payload": "private"}]
    payload["weekly_briefs"][0].update(article_count=99, articles=[], brief_status="editorially_reviewed")
    payload["weekly_briefs"][0]["cited_findings"] = [{"provenance": "invented"}]
    payload["surveillance_evidence"] = {
        "signals": [
            {"signal_id": "needs-gap", "exact_articles": []},
            {"signal_id": "has-gap", "exact_articles": []},
            {"signal_id": "old", "exact_articles": [{"article_id": "old-a", "recency_status": "historical", "evidence_age_days": 731}]},
        ],
        "evidence_gaps": [{"signal_id": "has-gap"}],
    }
    metrics, status = _read(tmp_path, payload)
    assert status == "ok"
    blockers = validate_public_research_payload(payload)
    assert metrics["blocker_count"] == len(blockers)
    assert metrics["blocker_categories"] == _blocker_categories(blockers)
    original = replace(_healthy_snapshot(), release_payload=payload)
    streamed = replace(original, release_payload=None, release_metrics=metrics)
    assert _release_checks(streamed, HealthThresholds()) == _release_checks(original, HealthThresholds())


@pytest.mark.parametrize("raw", [
    '{"articles": [', '{"articles": []} trailing', '[]',
    '{"articles": {}}', '{"articles": [1]}',
    '{"articles": [], "articles": []}',
    '{"surveillance_evidence": {"signals": {}, "evidence_gaps": []}}',
    '{"articles.item": {"article_id": "spoof"}}',
])
def test_streamed_release_fails_closed_for_invalid_files(tmp_path, raw):
    path = tmp_path / "release.json"
    path.write_text(raw)
    metrics, status = stream.read_streamed_release(path)
    assert metrics is None
    assert status in {"invalid_json", "invalid_shape"}


def test_limits_apply_to_record_depth_and_ignored_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(stream, "_MAX_RECORD_BYTES", 40)
    metrics, status = _read(tmp_path, _release_payload())
    assert metrics is None and status == "record_too_large"
    monkeypatch.setattr(stream, "_MAX_RECORD_BYTES", 10000)
    monkeypatch.setattr(stream, "_MAX_DEPTH", 4)
    metrics, status = _read(tmp_path, {"ignored": [[[[[1]]]]]})
    assert metrics is None and status == "record_too_large"
    monkeypatch.setattr(stream, "_MAX_STRING_BYTES", 100)
    metrics, status = _read(tmp_path, {"ignored": "a" * 101})
    assert metrics is None and status == "record_too_large"


def test_string_budget_guard_handles_escaped_chunk_boundaries(monkeypatch):
    raw = json.dumps({"a": 'a\\"b' * 8, "b": "你好"}, ensure_ascii=False).encode()
    monkeypatch.setattr(stream, "_MAX_STRING_BYTES", 1000)
    reader = stream._BoundedReader(io.BytesIO(raw))
    chunks = []
    while chunk := reader.read(1):
        chunks.append(chunk)
    assert b"".join(chunks) == raw
    assert not reader.in_string


def test_many_records_are_reduced_to_compact_metrics(tmp_path, monkeypatch):
    payload = _release_payload()
    article = payload["articles"][0]
    payload["articles"] = [{**article, "article_id": f"a{i}", "doi": f"10.1/{i}"} for i in range(1000)]
    metrics, status = _read(tmp_path, payload)
    assert status == "ok"
    assert metrics["article_count"] == 1000
    assert metrics["bilingual_count"] == 1000
    assert metrics["blocker_count"] == 0
    assert len(json.dumps(metrics)) < 1000


def test_total_input_budget_and_file_replacement_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "release.json"
    path.write_text(json.dumps(_release_payload()))
    monkeypatch.setattr(stream, "_MAX_STREAM_BYTES", 10)
    assert stream.read_streamed_release(path) == (None, "too_large")
    monkeypatch.setattr(stream, "_MAX_STREAM_BYTES", 1024 * 1024)
    original_scan = stream._scan

    def replace_during_scan(source, db):
        metrics = original_scan(source, db)
        replacement = tmp_path / "replacement.json"
        replacement.write_text(path.read_text())
        replacement.replace(path)
        return metrics

    monkeypatch.setattr(stream, "_scan", replace_during_scan)
    assert stream.read_streamed_release(path) == (None, "changed_during_read")


def test_stream_reader_consumes_and_validates_discarded_fields(tmp_path):
    path = tmp_path / "release.json"
    path.write_text(json.dumps(_release_payload())[:-1] + ', "unused": [1, broken]}')
    assert stream.read_streamed_release(path) == (None, "invalid_json")
