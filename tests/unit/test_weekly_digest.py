from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from src.services.data_release import weekly_digest as digest
from src.services.data_release import research_subscription as research

WEEK = "2026-W45"
NOW = datetime(2026, 11, 9, 1, tzinfo=timezone.utc)
SITE = "https://site.example.test"
REPORTS = "https://reports.example.test"
IDENTITY = {"release_id": "release-1", "build_digest": "a" * 64}


def _notice():
    return {"alert_id": "n-1", "notice_id": "notice-1", "outbreak_id": "OB-1",
            "published_date": "2026-11-04", "url": "https://example.test/source",
            "title_en": "Example notice", "summary_en": "Documented facts.",
            "summary_zh": "已确认信息。", "country_code": "JP", "disease_slug": "influenza",
            "status": "new", "ai": {"verdict": "accept", "review_mode": "cross_model"}}


def _brief():
    return {
        "week": WEEK, "url": f"/research/weekly/{WEEK}/", "brief_status": "editorially_reviewed",
        "byline": {"reviewer": {"name": "Example Editor", "role": "Editor",
                                  "reviewed_at": "2020-08-16T10:00:00Z"}},
        "methodology": {"en": "Published evidence.", "zh": "已发表证据。"},
        "cited_findings": [{"article_id": "a-1", "slug": "a-1", "title": "Research", "finding_en": "Findings.",
                            "finding_zh": "发现。", "source_url": "/research/articles/a-1/",
                            "provenance": "published_bilingual_structured_summary"}],
        "articles": [{"article_id": "a-1", "editorial_status": "published",
                      "content_tier": "quality_gated_multilingual_evidence",
                      "diseases": [{"slug": "influenza"}], "countries": [{"code": "JP"}]}],
    }


@pytest.fixture
def published(tmp_path, monkeypatch):
    astro = tmp_path / "astro"
    page = astro / "dist/situation/index.html"
    page.parent.mkdir(parents=True)
    page.write_text("published")
    outbreak = astro / "dist/situation/outbreaks/OB-1.json"
    outbreak.parent.mkdir(parents=True)
    outbreak.write_text(json.dumps({"schema_version": "gids.situation_alerts.v1", "alerts": [_notice()]}))
    brief = astro / "src/data/research/weekly" / f"{WEEK}.json"
    brief.parent.mkdir(parents=True)
    brief.write_text(json.dumps(_brief()))
    sha = hashlib.sha256(brief.read_bytes()).hexdigest()
    for locale in ("", "zh"):
        target = astro / "dist" / locale / "research/weekly" / WEEK / "index.html"
        target.parent.mkdir(parents=True)
        target.write_text(f'<span data-research-brief-sha256="{sha}"></span>')
    row = {"id": 2, "status": "APPROVED", "country_code": "JP", "period_start": "2026-10-12",
           "period_end": "2026-10-18", "metadata": {"quality_gate": {"passed": True},
           "peer_review": {"status": "published", "verdict": "accept", "review_mode": "cross_model"},
           "disease_directory": [{"slug": "influenza"}]}}
    report = digest.reports._report(row, REPORTS)
    state_dir = tmp_path / "exports/subscription-outbox/pilot/reports"
    state_dir.mkdir(parents=True)
    (state_dir / "observations.json").write_text(json.dumps({"schema_version": 1,
        "baseline_at": "2026-11-01T00:00:00+00:00",
        "observations": {"2": {"week": WEEK, "fingerprint": research._fingerprint(report)}}}))
    monkeypatch.setattr(research, "_verify_built_artifact", lambda *_args: None)
    monkeypatch.setattr(research, "_verify_live_release", lambda *_args, **_kwargs: None)
    # Unit fixtures simulate a successful independent full research scan.
    # Production must use the real scanner and cannot publish without binding.
    def scan_research(*, astro_dir, public_base_url, **_kwargs):
        items = digest._research_items(astro_dir, public_base_url, WEEK, {"research:a-1"})
        return items, {"papers_origin": "https://papers.example.test", "papers_manifest_sha256": "c" * 64,
                       "papers_generation_sha256": "b" * 64,
                       "papers_manifest_url": "https://papers.example.test/releases/sha256/" + "b" * 64 + "/papers-manifest.json",
                       "evidence_manifest_sha256": "d" * 64}
    monkeypatch.setattr(digest, "scan_public_research", scan_research)
    monkeypatch.setattr(digest.reports, "_verified_catalogue", lambda *_args: ([row], {}))
    verified = []
    monkeypatch.setattr(digest.reports, "_verify_pages", lambda items, *_args: verified.extend(items))
    return {"root": tmp_path, "astro_dir": astro, "reports_dir": tmp_path / "reports", "identity": IDENTITY,
            "public_base_url": SITE, "reports_base_url": REPORTS, "week": WEEK,
            "now": NOW, "mode": "pilot"}, verified


def _authority(seen, incomplete=None):
    def verify(context, proof):
        seen.append((context, proof))
        return context["source"] != incomplete and proof.get("receipt") == "verified-source-receipt" and len(context["source_items_sha256"]) == 64
    return verify


def _prepare(kwargs, *, seen=None, incomplete=None, proofs=None):
    return digest.prepare_weekly_digest(**kwargs, completion_proofs=proofs if proofs is not None else {
        "reports": {"receipt": "verified-source-receipt", "expected_item_ids": ["report:2"]},
        "notices": {"receipt": "verified-source-receipt", "expected_item_ids": ["notice:n-1"]},
        "research": {"receipt": "verified-source-receipt", "expected_item_ids": ["research:a-1"]}},
        completion_authority=_authority(seen if seen is not None else [], incomplete))


def test_all_sources_are_bilingual_personalized_and_staged_immutably(published):
    kwargs, verified = published
    seen = []
    record = _prepare(kwargs, seen=seen)
    payload = record["snapshot"]["payload"]
    assert record["status"] == "staged"
    assert len(verified) == 1
    assert {entry[0]["source"] for entry in seen} == set(digest.SOURCES)
    assert all(entry[0]["week_end_exclusive"] == "2026-11-09T00:00:00+00:00" for entry in seen)
    assert record["snapshot"]["source_provenance"]["pages_build_digest"] == "a" * 64
    assert payload["idempotency_key"] == "pilot:weekly-digest:2026-W45:r1"
    assert payload["list_codes"] == ["weekly_digest"] and payload["frequency"] == "weekly"
    assert [item["id"] for item in payload["content_items"]] == ["notice:n-1", "report:2", "research:a-1"]
    assert all(item["countries"] == ["JP"] and item["diseases"] == ["influenza"] for item in payload["content_items"])
    for locale in ("en", "zh"):
        assert payload["personalized_templates"][locale].count(digest.MARKER) == 1
        assert all(item["localized_markdown"][locale] for item in payload["content_items"])
    assert _prepare(kwargs) == record
    stored = kwargs["root"] / "exports/subscription-outbox/pilot/weekly/2026-W45.json"
    assert json.loads(stored.read_text()) == record


@pytest.mark.parametrize("source", digest.SOURCES)
def test_any_incomplete_source_fails_closed(published, source):
    kwargs, _ = published
    with pytest.raises(digest.WeeklyDigestError, match=f"{source}_week_not_complete"):
        _prepare(kwargs, incomplete=source)
    assert not (kwargs["root"] / "exports/subscription-outbox/pilot/weekly/2026-W45.json").exists()


def test_late_notice_and_correction_block_automatic_replay(published):
    kwargs, _ = published
    _prepare(kwargs)
    file = kwargs["astro_dir"] / "dist/situation/outbreaks/OB-1.json"
    value = json.loads(file.read_text())
    value["alerts"][0]["summary_zh"] = "更正后的摘要。"
    file.write_text(json.dumps(value))
    with pytest.raises(digest.WeeklyDigestError, match="weekly_digest_changed_requires_review"):
        _prepare(kwargs)


def test_bad_outbox_never_replays(published):
    kwargs, _ = published
    _prepare(kwargs)
    file = kwargs["root"] / "exports/subscription-outbox/pilot/weekly/2026-W45.json"
    value = json.loads(file.read_text())
    value["snapshot"]["payload"]["frequency"] = "instant"
    file.write_text(json.dumps(value))
    with pytest.raises(digest.WeeklyDigestError, match="weekly_digest_outbox_invalid"):
        _prepare(kwargs)


def test_unreviewed_research_and_partial_week_are_rejected(published):
    kwargs, _ = published
    path = kwargs["astro_dir"] / "src/data/research/weekly" / f"{WEEK}.json"
    brief = json.loads(path.read_text())
    brief["brief_status"] = "automatically_compiled_not_editorially_reviewed"
    path.write_text(json.dumps(brief))
    with pytest.raises(digest.WeeklyDigestError, match="research_brief_review_required"):
        _prepare(kwargs)
    with pytest.raises(digest.WeeklyDigestError, match="week_not_complete"):
        _prepare({**kwargs, "now": datetime(2026, 11, 8, 23, 59, tzinfo=timezone.utc)})


def test_historical_brief_cannot_override_missing_current_integrity_proof(published, monkeypatch):
    kwargs, _ = published
    # Old brief still says "published"; the new catalogue might have removed
    # the item for retraction, concern, correction or editorial withdrawal.
    monkeypatch.setattr(digest, "scan_public_research", lambda **_kwargs: ([], {
        "papers_origin": "https://papers.example.test", "papers_manifest_sha256": "c" * 64,
        "papers_generation_sha256": "b" * 64,
        "papers_manifest_url": "https://papers.example.test/releases/sha256/" + "b" * 64 + "/papers-manifest.json",
        "evidence_manifest_sha256": "d" * 64}))
    with pytest.raises(digest.WeeklyDigestError, match="research_current_publication_verification_required"):
        _prepare(kwargs)
    assert not (kwargs["root"] / "exports/subscription-outbox/pilot/weekly/2026-W45.json").exists()


def test_missing_papers_binding_blocks_even_an_empty_research_week(published, monkeypatch):
    kwargs, _ = published
    def missing_binding(**_kwargs):
        raise digest.ResearchObservationError("papers_release_binding_required")
    monkeypatch.setattr(digest, "scan_public_research", missing_binding)
    with pytest.raises(digest.WeeklyDigestError, match="papers_release_binding_required"):
        _prepare(kwargs)


def test_report_baseline_and_changed_sources_fail_closed(published):
    kwargs, _ = published
    state_path = kwargs["root"] / "exports/subscription-outbox/pilot/reports/observations.json"
    state = json.loads(state_path.read_text())
    state["baseline_at"] = "2026-11-05T00:00:00+00:00"
    state_path.write_text(json.dumps(state))
    with pytest.raises(digest.WeeklyDigestError, match="report_week_not_fully_observed"):
        _prepare(kwargs)
    state["baseline_at"] = "2026-11-01T00:00:00+00:00"
    state["observations"]["2"]["fingerprint"] = "changed"
    state_path.write_text(json.dumps(state))
    with pytest.raises(digest.WeeklyDigestError, match="report_changed_or_removed_requires_review"):
        _prepare(kwargs)


def test_empty_week_is_recorded_without_campaign_only_after_all_attestations(published, monkeypatch):
    kwargs, _ = published
    monkeypatch.setattr(digest, "_report_items", lambda *_args: [])
    monkeypatch.setattr(digest, "_notice_items", lambda *_args: [])
    monkeypatch.setattr(digest, "_research_items", lambda *_args: [])
    seen = []
    proofs = {source: {"receipt": "verified-source-receipt", "expected_item_ids": []} for source in digest.SOURCES}
    record = _prepare(kwargs, seen=seen, proofs=proofs)
    assert record["status"] == "empty"
    assert record["snapshot"]["payload"] is None
    assert len(seen) == 3


def test_missing_proof_rejected_before_publication_queries(published):
    kwargs, _ = published
    with pytest.raises(digest.WeeklyDigestError, match="completion_authority_required"):
        _prepare(kwargs, proofs={"research": "verified-source-receipt"})


def test_late_notice_is_assembled_from_first_publication_receipt(published):
    kwargs, _ = published
    file = kwargs["astro_dir"] / "dist/situation/outbreaks/OB-1.json"
    value = json.loads(file.read_text())
    value["alerts"][0]["published_date"] = "2026-10-28"
    file.write_text(json.dumps(value))
    # Original notice date precedes the week, but it was first observed publicly
    # this week according to the trusted source receipt.
    record = _prepare(kwargs)
    notice = next(item for item in record["snapshot"]["payload"]["content_items"] if item["id"] == "notice:n-1")
    assert "2026-10-28" in notice["localized_markdown"]["en"]


def test_late_research_is_assembled_from_verified_older_brief(published):
    kwargs, _ = published
    astro = kwargs["astro_dir"]
    current = astro / "src/data/research/weekly" / f"{WEEK}.json"
    brief = json.loads(current.read_text())
    brief["week"] = "2026-W44"
    brief["url"] = "/research/weekly/2026-W44/"
    current.unlink()
    older = current.with_name("2026-W44.json")
    older.write_text(json.dumps(brief))
    sha = hashlib.sha256(older.read_bytes()).hexdigest()
    for locale in ("", "zh"):
        page = astro / "dist" / locale / "research/weekly/2026-W44/index.html"
        page.parent.mkdir(parents=True)
        page.write_text(f'<span data-research-brief-sha256="{sha}"></span>')
    record = _prepare(kwargs)
    assert "research:a-1" in [item["id"] for item in record["snapshot"]["payload"]["content_items"]]


def test_missing_or_malformed_first_publication_evidence_fails(published):
    kwargs, _ = published
    proofs = {source: {"receipt": "verified-source-receipt", "expected_item_ids": []} for source in digest.SOURCES}
    proofs["reports"] = {"receipt": "verified-source-receipt"}
    with pytest.raises(digest.WeeklyDigestError, match="reports_first_publication_evidence_required"):
        _prepare(kwargs, proofs=proofs)


def test_oversized_issue_is_rejected_before_staging():
    item = {"id": "report:1", "countries": [], "diseases": [],
            "localized_markdown": {"en": "x" * 20001, "zh": "内容"}}
    with pytest.raises(digest.WeeklyDigestError, match="weekly_digest_item_invalid"):
        digest._payload(WEEK, [item], "pilot")


@pytest.mark.parametrize("change", ["mode", "public_origin", "release", "build"])
def test_completion_receipts_cannot_cross_environments_or_publication_identities(published, change):
    kwargs, _ = published
    seen = []
    _prepare(kwargs, seen=seen)
    ids = {"reports": ["report:2"], "notices": ["notice:n-1"], "research": ["research:a-1"]}
    proofs = {context["source"]: {"expected_item_ids": ids[context["source"]], "context": context}
              for context, _proof in seen}
    changed = dict(kwargs)
    if change == "mode":
        import shutil
        pilot_state = kwargs["root"] / "exports/subscription-outbox/pilot/reports/observations.json"
        full_state = kwargs["root"] / "exports/subscription-outbox/full/reports/observations.json"
        full_state.parent.mkdir(parents=True)
        shutil.copyfile(pilot_state, full_state)
        changed["mode"] = "full"
    elif change == "public_origin":
        changed["public_base_url"] = "https://another.example.test"
    elif change == "release":
        changed["identity"] = {**IDENTITY, "release_id": "another-release"}
    else:
        changed["identity"] = {**IDENTITY, "build_digest": "b" * 64}
    def authority(context, proof):
        return context == proof["context"]
    with pytest.raises(digest.WeeklyDigestError, match="week_not_complete"):
        digest.prepare_weekly_digest(**changed, completion_proofs=proofs, completion_authority=authority)


def test_coordinator_binds_real_research_scan_to_source_authority(published, monkeypatch):
    """Exercise actual scanner, Pages/Papers verifier and ledger authority together."""
    from src.services.data_release import weekly_research_observations as scan
    from src.services.data_release import weekly_source_observations as ledger
    from src.services.data_release.pipeline import _built_site_digest
    from src.services.data_release.papers_immutable import ImmutablePapersFile, _candidate_identity

    kwargs, _ = published
    root, astro = kwargs["root"], kwargs["astro_dir"]
    papers = "https://papers.example.test"
    article = {"article_id": "a-1", "slug": "a-1", "indexable": True,
               "editorial_status": "published", "integrity_status": "current",
               "content_tier": "quality_gated_multilingual_evidence",
               "summary": {"en": {"main_findings": "Findings."},
                           "zh": {"main_findings": "发现。"}}}
    article_path = astro / "src/data/research/articles/a-1.json"
    article_path.parent.mkdir(parents=True)
    article_path.write_text(json.dumps(article, ensure_ascii=False))
    article_sha = hashlib.sha256(article_path.read_bytes()).hexdigest()
    logical_pages = {}
    manifest_files = []
    for prefix in ("", "zh/"):
        path = prefix + "research/articles/a-1/"
        body = f'<article data-research-article-id="a-1" data-research-article-sha256="{article_sha}"></article>'.encode()
        logical_pages[path] = body
        manifest_files.append(ImmutablePapersFile(path + "index.html", path, len(body), hashlib.sha256(body).hexdigest()))
    generation, papers_manifest = _candidate_identity(papers, tuple(manifest_files))
    generation_prefix = f"releases/sha256/{generation}/"
    pages = {papers + "/" + generation_prefix + path: body for path, body in logical_pages.items()}
    brief_path = astro / "src/data/research/weekly" / f"{WEEK}.json"
    evidence = {"schema_version": "gids.research-evidence.v1", "weekly_inventory_complete": True,
                "weekly_briefs": [{"week": WEEK, "brief_status": "editorially_reviewed",
                                    "brief_sha256": hashlib.sha256(brief_path.read_bytes()).hexdigest(),
                                    "cited_article_ids": ["a-1"]}],
                "articles": [{"article_id": "a-1", "slug": "a-1", "editorial_status": "published",
                              "integrity_status": "current", "peer_review_status": "peer_reviewed",
                              "indexable": True, "detail_available": True, "content_tier": "quality_gated_multilingual_evidence",
                              "summaries": {locale: {"main_findings_sha256": hashlib.sha256(value.encode()).hexdigest()}
                                            for locale, value in {"en": "Findings.", "zh": "发现。"}.items()}}],
                "integrity_events": []}
    evidence_path = astro / "dist/research/evidence-manifest.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False))
    identity = {"release_id": "release-1", "build_digest": _built_site_digest(astro / "dist"),
                "source_commit": "abc", "deployment_branch": "main", "papers_origin": papers,
                "papers_manifest_sha256": hashlib.sha256(papers_manifest).hexdigest(),
                "papers_generation_sha256": generation,
                "papers_manifest_url": papers + "/" + generation_prefix + "papers-manifest.json"}
    remote = {SITE + "/release.json": json.dumps(identity).encode(),
              SITE + "/research/evidence-manifest.json": evidence_path.read_bytes(),
              identity["papers_manifest_url"]: papers_manifest, **pages}

    class Response:
        status = 200
        headers = {}
        def __init__(self, url): self.url = url
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def geturl(self): return self.url
        def read(self, n): return remote[self.url][:n]

    def opener(request, *, timeout):
        return Response(request.full_url)

    monkeypatch.setattr(digest, "scan_public_research", scan.scan_public_research)
    scanned, evidence_info = scan.scan_public_research(
        astro_dir=astro, identity=identity, public_base_url=SITE, papers_base_url=papers, opener=opener)
    assert [item["id"] for item in scanned] == ["research:a-1"]
    rows, manifest = digest.reports._verified_catalogue(None, None, None)
    state = json.loads((root / "exports/subscription-outbox/pilot/reports/observations.json").read_text())
    report_items = digest._report_items(rows, state, manifest, kwargs["reports_dir"], REPORTS, WEEK,
        datetime(2026, 11, 2, tzinfo=timezone.utc), opener)
    outbreak = json.loads((astro / "dist/situation/outbreaks/OB-1.json").read_text())
    notice_items = digest._notice_items([outbreak], WEEK, SITE, identity["release_id"], "pilot", {"notice:n-1"})
    provenance = {"mode": "pilot", "public_base_url": SITE, "reports_base_url": REPORTS,
                  "pages_release_id": identity["release_id"], "pages_build_digest": identity["build_digest"],
                  "reports_manifest_sha256": research._fingerprint(manifest),
                  "situation_snapshot": {"kind": "pages_build", "sha256": identity["build_digest"]}}
    directory = root / "exports/subscription-observations/pilot"
    directory.mkdir(parents=True)
    for source, items in {"reports": report_items, "notices": notice_items, "research": scanned}.items():
        source_provenance = dict(provenance)
        if source == "research":
            source_provenance.update({"papers_origin": evidence_info["papers_origin"],
                "papers_manifest_sha256": evidence_info["papers_manifest_sha256"],
                "papers_generation_sha256": evidence_info["papers_generation_sha256"],
                "papers_manifest_url": evidence_info["papers_manifest_url"],
                "research_evidence_manifest_sha256": evidence_info["evidence_manifest_sha256"]})
        entries = {item["id"]: {"first_seen": "2026-11-04T00:00:00+00:00", "present": True,
                                "item": item, "initial_sha256": research._fingerprint(item)} for item in items}
        (directory / f"{source}.json").write_text(json.dumps({"schema_version": 1, "source": source, "mode": "pilot",
            "baseline_at": "2026-11-01T00:00:00+00:00", "scanned_at": "2026-11-09T00:30:00+00:00",
            "items": entries, "evidence": {}, "provenance": source_provenance,
            "snapshot_sha256": research._fingerprint(items)}))
    proofs = {source: ledger.completion_proof(root=root, source=source, mode="pilot", week=WEEK) for source in digest.SOURCES}
    arguments = {**kwargs, "identity": identity, "papers_base_url": papers, "opener": opener}
    result = digest.prepare_weekly_digest(**arguments, completion_proofs=proofs, completion_authority=ledger.completion_authority(root, "pilot"))
    assert result["status"] == "staged"
    assert result["snapshot"]["source_provenance"]["papers_manifest_sha256"] == identity["papers_manifest_sha256"]
    assert result["snapshot"]["source_provenance"]["research_evidence_manifest_sha256"] == hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    # A proof from another release cannot authorize these current source bytes.
    altered = dict(proofs)
    altered["research"] = {**proofs["research"], "provenance": {**proofs["research"]["provenance"], "papers_manifest_sha256": "f" * 64}}
    with pytest.raises(digest.WeeklyDigestError, match="research_week_not_complete"):
        digest.prepare_weekly_digest(**arguments, completion_proofs=altered, completion_authority=ledger.completion_authority(root, "pilot"))
