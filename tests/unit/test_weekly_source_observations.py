from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from src.services.data_release import weekly_source_observations as observations
from src.services.data_release import research_subscription as research


IDENTITY = {"release_id": "release-1", "build_digest": "a" * 64,
            "source_commit": "abc", "deployment_branch": "main"}


def row(identifier: int) -> dict:
    return {"id": identifier, "status": "APPROVED", "country_code": "JP",
            "period_start": "2026-10-12", "period_end": "2026-10-18",
            "metadata": {"quality_gate": {"passed": True}, "peer_review": {
                "status": "published", "verdict": "accept", "review_mode": "cross_model"},
                "disease_directory": [{"slug": "influenza"}]}}


def setup(tmp_path, monkeypatch):
    astro = tmp_path / "astro"
    (astro / "dist/situation/outbreaks").mkdir(parents=True)
    (astro / "dist/situation/index.html").write_text("page")
    data = {"rows": [row(1)], "manifest": {"index": {"sha256": "b" * 64}}, "fail": False}
    monkeypatch.setattr(research, "_verify_built_artifact", lambda *_: None)
    monkeypatch.setattr(research, "_verify_live_release", lambda *_a, **_k: None)
    monkeypatch.setattr(observations.reports, "_verified_catalogue", lambda *_: (data["rows"], data["manifest"]))
    def verify_pages(*_):
        if data["fail"]:
            raise observations.reports.ReportSubscriptionError("public_page_not_published")
    monkeypatch.setattr(observations.reports, "_verify_pages", verify_pages)
    def scan(when):
        monkeypatch.setattr(observations, "_now", lambda: when)
        return observations.record_public_scan(root=tmp_path, source="reports", mode="pilot", reports_dir=tmp_path,
            astro_dir=astro, identity=IDENTITY, public_base_url="https://site.example.test",
            reports_base_url="https://reports.example.test")
    return data, scan


def test_verified_scans_record_first_observation_and_bind_closure_to_public_context(tmp_path, monkeypatch):
    data, scan = setup(tmp_path, monkeypatch)
    scan(datetime(2026, 11, 1, tzinfo=timezone.utc))
    data["rows"] = [row(1), row(2)]
    scan(datetime(2026, 11, 4, tzinfo=timezone.utc))
    with pytest.raises(observations.ObservationError, match="week_not_closed"):
        observations.completion_proof(root=tmp_path, source="reports", mode="pilot", week="2026-W45")
    scan(datetime(2026, 11, 9, tzinfo=timezone.utc))
    proof = observations.completion_proof(root=tmp_path, source="reports", mode="pilot", week="2026-W45")
    assert proof["expected_item_ids"] == ["report:2"]
    context = {**proof["provenance"], "source": "reports", "week": "2026-W45",
               "week_end_exclusive": "2026-11-09T00:00:00+00:00", "source_items_sha256": proof["selected_sha256"]}
    authority = observations.completion_authority(tmp_path, "pilot")
    assert authority(context, proof)
    assert not authority({**context, "reports_base_url": "https://other.example.test"}, proof)
    assert not authority({**context, "pages_release_id": "other"}, proof)
    assert not authority(context, {**proof, "expected_item_ids": []})
    assert not authority({**context, "source": "research"}, proof)

    # An item first observed after the boundary belongs to the next week.
    data["rows"].append(row(3))
    scan(datetime(2026, 11, 10, tzinfo=timezone.utc))
    assert observations.completion_proof(root=tmp_path, source="reports", mode="pilot", week="2026-W45")["expected_item_ids"] == ["report:2"]
    data["rows"] = [row(1), row(3)]
    scan(datetime(2026, 11, 11, tzinfo=timezone.utc))
    with pytest.raises(observations.ObservationError, match="changed_or_removed"):
        observations.completion_proof(root=tmp_path, source="reports", mode="pilot", week="2026-W45")


def test_failed_scan_and_missing_research_never_create_a_receipt(tmp_path, monkeypatch):
    data, scan = setup(tmp_path, monkeypatch)
    data["fail"] = True
    with pytest.raises(observations.ObservationError, match="public_page_not_published"):
        scan(datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert not (tmp_path / "exports/subscription-observations/pilot/reports.json").exists()
    with pytest.raises(observations.ObservationError, match="ledger_unreadable"):
        observations.completion_proof(root=tmp_path, source="research", mode="pilot", week="2026-W45")


def test_notice_scanner_and_weekly_digest_use_the_same_items(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    astro = tmp_path / "astro"
    (astro / "dist/situation/outbreaks").rmdir()
    path = tmp_path / "outbreak.json"
    pointer = b"verified-public-pointer"
    monkeypatch.setattr(observations.notices, "_situation_pointer", lambda *_: pointer)
    monkeypatch.setattr(observations.notices, "validate_manifest", lambda *_: ({}, {}))
    monkeypatch.setattr(observations.notices, "_published_outbreaks", lambda *_: ([json.loads(path.read_text())], pointer))
    def notice(identifier):
        return {"alert_id": identifier, "notice_id": "notice-" + identifier, "outbreak_id": "OB-1",
                "published_date": "2026-11-03", "url": "https://who.int/example", "title_en": "Notice",
                "summary_en": "Published information.", "summary_zh": "已发布信息。", "country_code": "CN",
                "disease_slug": "influenza", "status": "new", "ai": {"verdict": "accept", "review_mode": "cross_model"}}
    def write(alerts):
        path.write_text(json.dumps({"schema_version": "gids.situation_alerts.v1", "alerts": alerts}))
    def scan(when):
        monkeypatch.setattr(observations, "_now", lambda: when)
        return observations.record_public_scan(root=tmp_path, source="notices", mode="pilot", reports_dir=tmp_path,
            astro_dir=astro, identity=IDENTITY, public_base_url="https://site.example.test",
            reports_base_url="https://reports.example.test")
    write([notice("one")])
    scan(datetime(2026, 11, 1, tzinfo=timezone.utc))
    write([notice("one"), notice("two")])
    scan(datetime(2026, 11, 4, tzinfo=timezone.utc))
    scan(datetime(2026, 11, 9, tzinfo=timezone.utc))
    proof = observations.completion_proof(root=tmp_path, source="notices", mode="pilot", week="2026-W45")
    from src.services.data_release.weekly_digest import _notice_items
    items = _notice_items([json.loads(path.read_text())], "2026-W45", "https://site.example.test", "release-1", "pilot", {"notice:two"})
    assert proof["expected_item_ids"] == ["notice:two"]
    assert proof["selected_sha256"] == research._fingerprint(items)
    context = {**proof["provenance"], "source": "notices", "week": "2026-W45",
               "week_end_exclusive": "2026-11-09T00:00:00+00:00", "source_items_sha256": research._fingerprint(items)}
    authority = observations.completion_authority(tmp_path, "pilot")
    assert authority(context, proof)
    assert not authority({**context, "situation_snapshot": {"kind": "public_pointer", "sha256": "a" * 64}}, proof)


def test_research_receipt_requires_verified_scan_and_binds_papers_and_inventory(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    astro = tmp_path / "astro"
    state = {"fail": True}
    def scanner(**_kwargs):
        if state["fail"]:
            raise observations.ResearchObservationError("papers_release_binding_required")
        return [], {"papers_origin": "https://papers.example.test", "papers_manifest_sha256": "c" * 64,
                    "papers_generation_sha256": "f" * 64, "papers_manifest_url": "https://papers.example.test/releases/sha256/" + "f" * 64 + "/papers-manifest.json",
                    "evidence_manifest_sha256": "d" * 64}
    monkeypatch.setattr(observations, "scan_public_research", scanner)
    def scan(when):
        monkeypatch.setattr(observations, "_now", lambda: when)
        return observations.record_public_scan(root=tmp_path, source="research", mode="pilot", reports_dir=tmp_path,
            astro_dir=astro, identity=IDENTITY, public_base_url="https://site.example.test",
            reports_base_url="https://reports.example.test", papers_base_url="https://papers.example.test")
    with pytest.raises(observations.ObservationError, match="papers_release_binding_required"):
        scan(datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert not (tmp_path / "exports/subscription-observations/pilot/research.json").exists()
    state["fail"] = False
    scan(datetime(2026, 11, 1, tzinfo=timezone.utc))
    scan(datetime(2026, 11, 9, tzinfo=timezone.utc))
    proof = observations.completion_proof(root=tmp_path, source="research", mode="pilot", week="2026-W45")
    assert proof["expected_item_ids"] == []
    context = {**proof["provenance"], "source": "research", "week": "2026-W45",
               "week_end_exclusive": "2026-11-09T00:00:00+00:00", "source_items_sha256": proof["selected_sha256"]}
    authority = observations.completion_authority(tmp_path, "pilot")
    assert authority(context, proof)
    assert not authority({**context, "papers_manifest_sha256": "f" * 64}, proof)
    assert not authority({**context, "research_evidence_manifest_sha256": "e" * 64}, proof)
