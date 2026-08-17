from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.automation import dispatch_research_digest as dispatcher


def _brief() -> dict:
    return {
        "week": "2026-W33",
        "url": "/research/weekly/2026-W33/",
        "brief_status": "automatically_compiled_not_editorially_reviewed",
        "methodology": {
            "en": "Findings come from published bilingual summaries.",
            "zh": "研究发现来自已发布的双语摘要。",
        },
        "cited_findings": [{
            "article_id": "lit-one",
            "title": "A systematic review",
            "finding_en": "The review found a measurable effect with important limitations.",
            "finding_zh": "系统综述发现了可测量的效应，同时存在重要局限。",
            "source_url": "/research/articles/a-systematic-review/",
            "doi": "10.1000/example",
            "provenance": "published_bilingual_structured_summary",
        }],
        "articles": [{
            "article_id": "lit-one",
            "editorial_status": "published",
            "content_tier": "quality_gated_bilingual_evidence",
            "study_type": "Systematic review",
            "peer_review_status": "peer_reviewed",
            "diseases": [{"slug": "malaria"}],
            "countries": [{"code": "CN"}],
            "topics": [{"name": "Surveillance"}],
        }],
    }


def _write_brief(tmp_path: Path, brief: dict | None = None) -> Path:
    path = tmp_path / "2026-W33.json"
    path.write_text(json.dumps(brief or _brief()), encoding="utf-8")
    return path


class _Response:
    def __init__(self, payload: dict, status: int = 201):
        self.status = status
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def test_builds_bilingual_cited_weekly_campaign_without_recipient_data():
    payload = dispatcher.build_campaign_payload(_brief())

    assert payload["idempotency_key"] == "research-digest:2026-W33:r1"
    assert payload["list_codes"] == ["research_digest"]
    assert payload["frequency"] == "weekly"
    assert payload["target_locales"] == ["en", "zh"]
    assert payload["diseases"] == ["malaria"]
    assert payload["countries"] == ["CN"]
    assert payload["research_topics"] == ["surveillance"]
    assert payload["study_types"] == ["systematic-review"]
    assert payload["peer_review_statuses"] == ["peer-reviewed"]
    for locale in ("en", "zh"):
        markdown = payload["contents"][locale]["markdown"]
        assert "https://globalinfectiousdisease.com/research/articles/a-systematic-review/" in markdown
        assert "https://doi.org/10.1000/example" in markdown
        assert "https://globalinfectiousdisease.com/research/weekly/2026-W33/" in markdown
    serialized = json.dumps(payload)
    assert "@" not in serialized
    assert "unsubscribe" not in serialized


def test_rejects_ungrounded_or_non_bilingual_findings():
    missing_source = _brief()
    missing_source["cited_findings"][0]["source_url"] = "https://publisher.example/article"
    with pytest.raises(dispatcher.DispatchError, match="finding_public_article_source_required"):
        dispatcher.build_campaign_payload(missing_source)

    missing_chinese = deepcopy(_brief())
    missing_chinese["cited_findings"][0]["finding_zh"] = ""
    with pytest.raises(dispatcher.DispatchError, match="finding_zh_required"):
        dispatcher.build_campaign_payload(missing_chinese)

    thin_record = deepcopy(_brief())
    thin_record["articles"][0]["content_tier"] = "metadata_only"
    with pytest.raises(dispatcher.DispatchError, match="not_public_bilingual_evidence"):
        dispatcher.build_campaign_payload(thin_record)


def test_editorially_reviewed_brief_emits_only_the_validated_public_byline():
    brief = _brief()
    brief["brief_status"] = "editorially_reviewed"
    brief["byline"] = {
        "name_en": "GIDS Research Radar automated compiler",
        "name_zh": "GIDS Research Radar 自动编译器",
        "reviewer": {
            "name": "Dr Jane Q. Public",
            "role": "Infectious disease editor",
            "reviewed_at": "2020-08-16T10:00:00Z",
            "institution": "Example School of Public Health",
            "note_en": "Checked against the cited source records.",
            "note_zh": "已依据所引来源记录完成核对。",
        },
    }

    payload = dispatcher.build_campaign_payload(brief)
    markdown_en = payload["contents"]["en"]["markdown"]
    markdown_zh = payload["contents"]["zh"]["markdown"]
    for expected in (
        "editorially reviewed by Dr Jane Q. Public",
        "Infectious disease editor",
        "Example School of Public Health",
        "2020-08-16",
        "Checked against the cited source records",
    ):
        assert expected in markdown_en
    assert "由 Dr Jane Q. Public" in markdown_zh
    assert "完成编辑审核" in markdown_zh
    assert "已依据所引来源记录完成核对" in markdown_zh
    serialized = json.dumps(payload)
    assert "internal_reviewer_id" not in serialized
    assert "reviewer_email" not in serialized


@pytest.mark.parametrize(("mutation", "error"), [
    (
        {"brief_status": "editorially_reviewed", "byline": {"reviewer": {"name": "Dr Jane Q. Public"}}},
        "invalid_reviewed_brief_reviewer",
    ),
    (
        {"brief_status": "editorially_reviewed", "byline": {"reviewer": None}},
        "reviewed_brief_reviewer_required",
    ),
    (
        {
            "brief_status": "automatically_compiled_not_editorially_reviewed",
            "byline": {"reviewer": {
                "name": "Dr Jane Q. Public", "role": "Editor", "reviewed_at": "2020-08-16T10:00:00Z",
            }},
        },
        "unreviewed_brief_exposes_reviewer",
    ),
    (
        {
            "brief_status": "editorially_reviewed",
            "byline": {"reviewer": {
                "name": "Dr Jane Q. Public", "role": "Editor", "reviewed_at": "2020-08-16T10:00:00Z",
                "internal_reviewer_id": "operator-17",
            }},
        },
        "brief_reviewer_contains_non_public_fields",
    ),
    (
        {"brief_status": "review_pending"},
        "unsupported_brief_status",
    ),
])
def test_digest_dispatch_rejects_inconsistent_partial_or_leaky_review_state(mutation, error):
    brief = _brief()
    brief.update(mutation)

    with pytest.raises(dispatcher.DispatchError, match=error):
        dispatcher.build_campaign_payload(brief)


def test_default_dry_run_never_calls_worker(tmp_path, capsys):
    path = _write_brief(tmp_path)

    def opener(*_args, **_kwargs):
        raise AssertionError("dry-run must not perform network activity")

    result = dispatcher.main(["--brief", str(path)], environment={}, opener=opener)

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry_run"
    assert output["source_count"] == 1
    assert output["payload"]["contents"]["zh"]["subject"].startswith("Research Radar")


def test_strict_dry_run_requires_worker_configuration(tmp_path, capsys):
    result = dispatcher.main(
        ["--brief", str(_write_brief(tmp_path)), "--strict-config"],
        environment={},
    )
    assert result == 2
    output = capsys.readouterr()
    assert "configuration_missing" in output.err
    assert "SUBSCRIPTIONS__ADMIN_API_TOKEN" in output.err


def test_apply_posts_once_with_bearer_and_emits_only_safe_campaign_projection(tmp_path, capsys):
    calls = []
    secret = "unit-test-secret"

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return _Response({
            "ok": True,
            "duplicate": False,
            "campaign": {
                "id": "campaign-1",
                "status": "queued",
                "audience_count": 2,
                "progress": {"total": 2, "queued": 2, "sent": 0, "failed": 0, "skipped": 0},
                "deliveries": [{"email_masked": "re****@example.test"}],
            },
        })

    result = dispatcher.main(
        ["--brief", str(_write_brief(tmp_path)), "--apply"],
        environment={
            "RESEARCH_DIGEST_WORKER_URL": "https://subscriptions.example.test",
            "SUBSCRIPTIONS__ADMIN_API_TOKEN": secret,
        },
        opener=opener,
    )

    assert result == 0
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == "https://subscriptions.example.test/api/admin/notifications"
    assert request.get_header("Authorization") == f"Bearer {secret}"
    assert timeout == 15
    posted = json.loads(request.data)
    assert posted["idempotency_key"] == "research-digest:2026-W33:r1"
    output_text = capsys.readouterr().out
    assert secret not in output_text
    assert "email" not in output_text
    assert "example.test" not in output_text
    output = json.loads(output_text)
    assert output == {
        "audience_count": 2,
        "campaign_id": "campaign-1",
        "duplicate": False,
        "progress": {"failed": 0, "queued": 2, "sent": 0, "skipped": 0, "total": 2},
        "source_count": 1,
        "status": "queued",
        "status_message": "queued",
    }


def test_revision_is_explicit_escape_hatch_for_corrected_brief():
    first = dispatcher.build_campaign_payload(_brief())
    replay = dispatcher.build_campaign_payload(_brief())
    revision = dispatcher.build_campaign_payload(_brief(), revision="r2")
    assert first["idempotency_key"] == replay["idempotency_key"]
    assert revision["idempotency_key"] != first["idempotency_key"]


def test_process_requires_apply_without_opening_network(tmp_path, capsys):
    def opener(*_args, **_kwargs):
        raise AssertionError("invalid CLI combination must not use network")

    result = dispatcher.main(
        ["--brief", str(_write_brief(tmp_path)), "--process"],
        environment={},
        opener=opener,
    )
    assert result == 2
    assert "process_requires_apply" in capsys.readouterr().err


def test_apply_and_process_uses_bounded_campaign_endpoint(tmp_path, capsys):
    calls = []
    responses = [
        _Response({
            "ok": True,
            "duplicate": True,
            "campaign": {
                "id": "campaign-1",
                "status": "queued",
                "audience_count": 1,
                "progress": {"total": 1, "queued": 1, "sent": 0, "failed": 0, "skipped": 0},
            },
        }, status=200),
        _Response({
            "ok": True,
            "campaign_id": "campaign-1",
            "processed": 1,
            "status": "sent",
            "progress": {"total": 1, "queued": 0, "sent": 1, "failed": 0, "skipped": 0},
        }, status=200),
    ]

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return responses.pop(0)

    result = dispatcher.main(
        ["--brief", str(_write_brief(tmp_path)), "--apply", "--process", "--batch-size", "10", "--max-batches", "2"],
        environment={
            "RESEARCH_DIGEST_WORKER_URL": "https://subscriptions.example.test",
            "SUBSCRIPTIONS__ADMIN_API_TOKEN": "unit-test-secret",
        },
        opener=opener,
    )

    assert result == 0
    assert [call[0].full_url for call in calls] == [
        "https://subscriptions.example.test/api/admin/notifications",
        "https://subscriptions.example.test/api/admin/notifications/campaign-1/process",
    ]
    assert json.loads(calls[1][0].data) == {"batch_size": 10}
    output = json.loads(capsys.readouterr().out)
    assert output["duplicate"] is True
    assert output["status"] == "sent"
    assert output["progress"]["sent"] == 1
