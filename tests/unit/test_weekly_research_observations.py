from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.data_release import weekly_research_observations as scan


IDENTITY = {"release_id": "release-1", "build_digest": "a" * 64, "papers_origin": "https://papers.example.test",
            "papers_manifest_sha256": "b" * 64}


def fixture(tmp_path: Path, monkeypatch, *, reviewed=False):
    astro = tmp_path / "astro"
    source = astro / "src/data/research/weekly"
    source.mkdir(parents=True)
    finding = {"article_id": "one", "slug": "one", "finding_en": "Finding", "finding_zh": "发现"}
    brief = {"week": "2026-W45", "brief_status": "ai_reviewed" if reviewed else "automatically_compiled_not_editorially_reviewed",
             "cited_findings": [finding]}
    raw = json.dumps(brief, ensure_ascii=False).encode()
    (source / "2026-W45.json").write_bytes(raw)
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    manifest = {"schema_version": "gids.research-evidence.v1", "weekly_inventory_complete": True,
      "weekly_briefs": [{"week": "2026-W45", "brief_status": brief["brief_status"],
                         "brief_sha256": hashlib.sha256(raw).hexdigest(), "cited_article_ids": ["one"]}],
      "articles": [{"article_id": "one", "slug": "one", "editorial_status": "published", "integrity_status": "current",
                    "peer_review_status": "peer_reviewed", "indexable": True, "detail_available": True,
                    "content_tier": "quality_gated_bilingual_evidence", "summaries": {
                      "en": {"main_findings_sha256": digest("Finding")}, "zh": {"main_findings_sha256": digest("发现")}}}],
      "integrity_events": []}
    target = astro / "dist/research/evidence-manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(manifest))
    state = {"manifest": manifest, "verified_ids": [], "fail_binding": False, "remote_differs": False}
    def save(): target.write_text(json.dumps(state["manifest"]))
    monkeypatch.setattr(scan.research, "_verify_built_artifact", lambda *_a, **_k: None)
    monkeypatch.setattr(scan.research, "_verify_live_release", lambda *_a, **_k: None)
    monkeypatch.setattr(scan.reports, "_get", lambda *_a, **_k: b"wrong" if state["remote_differs"] else target.read_bytes())
    monkeypatch.setattr(scan.research, "_payload_for_week", lambda *_: {"content_items": [{"id": "one", "countries": ["CN"],
        "diseases": ["influenza"], "localized_markdown": {"en": "Finding", "zh": "发现"}}]})
    def verify(**kwargs):
        if state["fail_binding"]:
            raise scan.ResearchPublicationError("papers_release_binding_required")
        state["verified_ids"] = [item["article_id"] for item in kwargs["findings"]]
        return SimpleNamespace(papers_origin="https://papers.example.test", papers_manifest_sha256="b" * 64,
                               papers_generation_sha256="f" * 64, papers_manifest_url="https://papers.example.test/releases/sha256/" + "f" * 64 + "/papers-manifest.json",
                               articles=tuple((item, "c", "d", "e") for item in state["verified_ids"]))
    monkeypatch.setattr(scan, "verify_research_publication", verify)
    monkeypatch.setattr(scan, "recheck_research_publication", lambda previous, **kwargs: verify(**kwargs))
    def run():
        return scan.scan_public_research(astro_dir=astro, identity=IDENTITY, public_base_url="https://site.example.test",
                                         papers_base_url="https://papers.example.test")
    return state, save, run, source


def test_empty_reviewed_inventory_still_requires_papers_binding_and_all_source_files(tmp_path, monkeypatch):
    state, save, run, source = fixture(tmp_path, monkeypatch)
    items, evidence = run()
    assert items == [] and state["verified_ids"] == [] and evidence["weekly_inventory_count"] == 1
    state["fail_binding"] = True
    with pytest.raises(scan.ResearchObservationError, match="papers_release_binding_required"):
        run()
    state["fail_binding"] = False
    (source / "2026-W44.json").write_text("{}")
    with pytest.raises(scan.ResearchObservationError, match="inventory_mismatch"):
        run()
    (source / "2026-W44.json").unlink()
    (source / "2026-W45.json").unlink()
    source.rmdir()
    with pytest.raises(scan.ResearchObservationError, match="inventory_unavailable"):
        run()
    source.mkdir()
    # Restore the expected source for the next independent validation.
    brief = {"week": "2026-W45", "brief_status": "automatically_compiled_not_editorially_reviewed",
             "cited_findings": [{"article_id": "one", "slug": "one", "finding_en": "Finding", "finding_zh": "发现"}]}
    (source / "2026-W45.json").write_text(json.dumps(brief, ensure_ascii=False))
    state["manifest"]["weekly_inventory_complete"] = False
    save()
    with pytest.raises(scan.ResearchObservationError, match="inventory_incomplete"):
        run()


def test_reviewed_finding_requires_current_manifest_summary_and_papers_pages(tmp_path, monkeypatch):
    state, save, run, _ = fixture(tmp_path, monkeypatch, reviewed=True)
    items, _ = run()
    assert [item["id"] for item in items] == ["research:one"]
    assert state["verified_ids"] == ["one"]
    state["manifest"]["articles"][0]["summaries"]["zh"]["main_findings_sha256"] = "a" * 64
    save()
    with pytest.raises(scan.ResearchObservationError, match="summary_version_mismatch"):
        run()
    state["manifest"]["articles"][0]["summaries"]["zh"]["main_findings_sha256"] = hashlib.sha256("发现".encode()).hexdigest()
    state["manifest"]["integrity_events"] = [{"article_id": "one", "event_type": "correction"}]
    save()
    with pytest.raises(scan.ResearchObservationError, match="article_not_current"):
        run()


def test_scanner_requires_verifier_to_cover_the_complete_reviewed_article_set(tmp_path, monkeypatch):
    _state, _save, run, _ = fixture(tmp_path, monkeypatch, reviewed=True)
    incomplete = SimpleNamespace(papers_origin="https://papers.example.test", papers_manifest_sha256="b" * 64, articles=())
    monkeypatch.setattr(scan, "verify_research_publication", lambda **_kwargs: incomplete)
    with pytest.raises(scan.ResearchObservationError, match="verified_article_inventory_mismatch"):
        run()


def test_sharded_manifest_verifies_every_public_shard(tmp_path, monkeypatch):
    state, save, run, _ = fixture(tmp_path, monkeypatch, reviewed=True)
    root = tmp_path / 'astro/dist/research'
    rows = state['manifest'].pop('articles')
    raw = json.dumps(rows).encode()
    digest = hashlib.sha256(raw).hexdigest()
    name = f'evidence-shards/{digest}.json'
    shard = root / name
    shard.parent.mkdir()
    shard.write_bytes(raw)
    state['manifest']['schema_version'] = 'gids.research-evidence.v2'
    state['manifest']['article_shards'] = [{'path': name, 'sha256': digest, 'count': len(rows)}]
    save()
    monkeypatch.setattr(scan.reports, '_get', lambda url, *_a, **_k: (root / 'evidence-manifest.json').read_bytes() if url.endswith('evidence-manifest.json') else shard.read_bytes())
    items, _ = run()
    assert [item['id'] for item in items] == ['research:one']
    shard.write_bytes(b'[]')
    with pytest.raises(scan.ResearchObservationError, match='shard_mismatch'):
        run()
