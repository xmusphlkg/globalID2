import ast
import asyncio
import inspect
import json
import pytest
from types import SimpleNamespace

from scripts import generate_site_data as legacy_api
from src.generation import (
    direct_download_files,
    site_data_catalogue,
    site_data_export,
    site_data_knowledge,
)


def _called_names(function: object) -> list[str]:
    tree = ast.parse(inspect.getsource(function))
    calls: list[str] = []

    class CallCollector(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            self.generic_visit(node)

    CallCollector().visit(tree)
    return calls


def test_moved_site_data_symbols_are_direct_legacy_reexports() -> None:
    assert legacy_api.build_disease_knowledge_fields is (
        site_data_knowledge.build_disease_knowledge_fields
    )
    assert legacy_api.apply_disease_knowledge_fields is (
        site_data_knowledge.apply_disease_knowledge_fields
    )
    assert legacy_api.apply_country_brief_fields is (
        site_data_knowledge.apply_country_brief_fields
    )
    assert legacy_api.load_standard_diseases is site_data_catalogue.load_standard_diseases
    assert legacy_api.enrich_diseases_with_ontology is (
        site_data_catalogue.enrich_diseases_with_ontology
    )
    assert legacy_api.validate_record_catalogue_coverage is (
        site_data_catalogue.validate_record_catalogue_coverage
    )
    assert legacy_api.export is site_data_export.export


def test_export_public_signature_remains_stable() -> None:
    assert list(inspect.signature(site_data_export.export).parameters) == [
        "output_dir",
        "manifest_output",
        "allow_empty_export",
        "public_site_data_dir",
        "direct_download_output_dir",
        "direct_download_url_base",
        "direct_download_max_file_bytes",
        "direct_download_workers",
        "incremental_country_codes",
        "recorded_at_as_of",
        "incremental_site",
    ]


def test_publish_hash_manifest_avoids_rereading_country_snapshots(tmp_path, monkeypatch) -> None:
    (tmp_path / "meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "publish-hashes.json").write_text(
        json.dumps(
            {
                "projection_version": "public-series-v1",
                "countries": {"SG": "abc123"},
            }
        ),
        encoding="utf-8",
    )

    def unexpected(*_args, **_kwargs):
        raise AssertionError("country snapshot should stay unread")

    monkeypatch.setattr(site_data_export, "_country_hash_from_snapshot", unexpected)

    assert site_data_export._local_publish_hashes(tmp_path, ["SG"]) == {"SG": "abc123"}


def test_incremental_export_requires_snapshot_without_unrequested_full_rebuild(tmp_path, monkeypatch) -> None:
    attempts = []

    async def collect(*args, **kwargs):
        attempts.append((args[2], kwargs.get("incremental_site", False)))
        if kwargs.get("incremental_site"):
            raise RuntimeError("Incremental site export is missing the existing country snapshot for XX.")
        return {"incremental": False, "country_download_entries": [], "disease_download_entries": []}

    monkeypatch.setattr(site_data_export, "collect_site_export_context", collect)
    monkeypatch.setattr(site_data_export, "write_site_export_artifacts", lambda *args: None)
    monkeypatch.setattr(site_data_export, "build_direct_download_files", lambda *args, **kwargs: {})
    with pytest.raises(RuntimeError, match="Incremental site export is missing"):
        asyncio.run(site_data_export.export(tmp_path / "data", tmp_path / "manifest.json", incremental_site=True))

    assert attempts == [(None, True)]


def test_direct_download_default_workers_respect_tight_cgroup_memory() -> None:
    gib = 1024 * 1024 * 1024

    assert direct_download_files._default_export_workers(16, 4 * gib) == 1
    assert direct_download_files._default_export_workers(16, 8 * gib) == 2
    assert direct_download_files._default_export_workers(16, 12 * gib) == 4
    assert direct_download_files._default_export_workers(16, 16 * gib) == 6
    assert direct_download_files._default_export_workers(16, 24 * gib) == 8
    assert direct_download_files._default_export_workers(4, 16 * gib) == 4


def test_direct_download_reads_the_service_cgroup_memory_guardrail(tmp_path) -> None:
    gib = 1024 * 1024 * 1024
    cgroup_root = tmp_path / "cgroup"
    service_cgroup = cgroup_root / "system.slice" / "globalid-dashboard-worker.service"
    service_cgroup.mkdir(parents=True)
    (service_cgroup / "memory.high").write_text(str(4 * gib), encoding="utf-8")
    (service_cgroup / "memory.max").write_text(str(6 * gib), encoding="utf-8")
    membership = tmp_path / "self.cgroup"
    membership.write_text(
        "0::/system.slice/globalid-dashboard-worker.service\n",
        encoding="utf-8",
    )

    assert direct_download_files._read_cgroup_memory_limit_bytes(
        cgroup_root=cgroup_root,
        membership_path=membership,
    ) == 4 * gib


def test_unchanged_download_parts_can_be_reused_without_reading_bytes(tmp_path) -> None:
    directory = tmp_path / "countries" / "sg"
    directory.mkdir(parents=True)
    payload = b"curve"
    for suffix in ("csv", "json", "xlsx"):
        (directory / f"current.{suffix}").write_bytes(payload)
    entry = {
        "parts": [
            {
                "files": {
                    suffix: {
                        "relative_path": f"countries/sg/current.{suffix}",
                        "bytes": len(payload),
                        "sha256": "not-the-real-hash",
                    }
                    for suffix in ("csv", "json", "xlsx")
                }
            }
        ]
    }

    assert direct_download_files._entry_files_available(
        entry,
        tmp_path,
        "countries",
        "sg",
        verify_hash=False,
    )
    assert not direct_download_files._entry_files_available(
        entry,
        tmp_path,
        "countries",
        "sg",
    )


def test_direct_download_default_workers_fall_back_to_cpu_without_memory_limit() -> None:
    assert direct_download_files._default_export_workers(16, None) == 4
    assert direct_download_files._default_export_workers(1, None) == 1


def test_export_side_effect_sequence_is_explicit_and_stable() -> None:
    """Guard packaging and file-write order while the orchestrator is refactored."""
    calls = _called_names(site_data_export.export)
    expected = [
        "collect_site_export_context",
        "write_site_export_artifacts",
        "build_direct_download_files",
        "write_pretty_json",  # frontend download manifest
    ]
    position = 0
    for name in calls:
        if position < len(expected) and name == expected[position]:
            position += 1
    assert position == len(expected), calls


def test_context_phase_performs_no_filesystem_writes() -> None:
    calls = set(_called_names(site_data_export.collect_site_export_context))
    assert calls.isdisjoint(
        {"prepare_site_output_dirs", "write_pretty_json", "write_compact_json"}
    )


def test_iceland_source_catalogue_keeps_only_observed_feeds() -> None:
    source_info = {
        "primary_scope": "is_doh_annual",
        "sources": [
            {"scope": "is_doh_annual", "label": "Annual", "url": "annual"},
            {"scope": "is_doh_sti", "label": "STI", "url": "sti"},
            {"scope": "is_doh_respiratory", "label": "Resp", "url": "resp"},
            {"scope": "is_doh_history", "label": "History", "url": "history"},
        ],
    }
    country_data = {
        "disease_series": {
            "D094": {
                "source_series": [
                    {"source_series_code": "sti:chlamydia:monthly-diagnoses"}
                ]
            },
            "D038": {
                "source_series": [
                    {
                        "series_code": "SER_IS_DOH_RESPIRATORY_INFLUENZA_WEEKLY"
                    }
                ]
            },
        }
    }

    result = site_data_export._retain_observed_iceland_sources(
        source_info,
        country_data,
    )

    assert [source["scope"] for source in result["sources"]] == [
        "is_doh_sti",
        "is_doh_respiratory",
    ]
    assert result["primary_scope"] == "is_doh_sti"


def test_site_artifact_write_order_remains_stable() -> None:
    calls = _called_names(site_data_export.write_site_export_artifacts)
    expected = [
        "prepare_site_output_dirs",
        "write_pretty_json",  # disease index
        "write_pretty_json",  # ontology build data
        "write_pretty_json",  # ontology public data
        "write_pretty_json",  # country build data
        "write_compact_json",  # country public data
        "write_pretty_json",  # disease build data
        "write_pretty_json",  # disease knowledge data
        "write_compact_json",  # disease public data
        "write_pretty_json",  # reports index
        "write_pretty_json",  # report detail
        "write_pretty_json",  # meta
        "write_pretty_json",  # about
    ]
    position = 0
    for name in calls:
        if position < len(expected) and name == expected[position]:
            position += 1
    assert position == len(expected), calls


def test_site_artifact_writer_reads_download_index_counts_from_context(
    monkeypatch, tmp_path
) -> None:
    writes: list[object] = []
    monkeypatch.setattr(site_data_export, "prepare_site_output_dirs", lambda *_: None)
    monkeypatch.setattr(
        site_data_export, "write_pretty_json", lambda path, value: writes.append(path)
    )
    monkeypatch.setattr(
        site_data_export, "write_compact_json", lambda path, value: writes.append(path)
    )
    monkeypatch.setattr(
        site_data_export,
        "build_disease_knowledge_fields",
        lambda *_: {"knowledge_display_mode": "blocked", "knowledge_completeness": 0},
    )
    monkeypatch.setattr(site_data_export, "build_about_snapshot", lambda **_: {})

    site_data_export.write_site_export_artifacts(
        {
            "all_records_by_country": {},
            "countries_simple": [],
            "country_download_entries": [{"country_code": "CN"}],
            "country_exports": [],
            "disease_download_entries": [{"disease_id": "D001"}],
            "disease_exports": [
                {"disease_id": "D001", "disease_data": {}, "site_data": {}}
            ],
            "disease_knowledge_briefs": {},
            "diseases": [{"disease_id": "D001"}],
            "diseases_by_id": {"D001": {"disease_id": "D001"}},
            "generated_at": "2026-08-05T00:00:00Z",
            "ontology": SimpleNamespace(concept_ids=["D001"], series_ids=[]),
            "ontology_document": {
                "registry_id": "registry",
                "schema_version": 1,
                "default_rollup_policy": "sum",
            },
            "report_details": {},
            "reports": [],
        },
        tmp_path / "build",
        tmp_path / "public",
    )

    assert tmp_path / "build" / "about.json" in writes


def test_incremental_export_reuses_unchanged_browser_projections(tmp_path) -> None:
    output = tmp_path / "build"
    public = tmp_path / "public"
    country_path = public / "countries" / "xx.json"
    disease_path = public / "diseases" / "d_test.json"
    country_path.parent.mkdir(parents=True)
    disease_path.parent.mkdir(parents=True)
    country_path.write_text('{"old":true}', encoding="utf-8")
    (country_path.parent / "xx-source-series.json").write_text('{}', encoding="utf-8")
    disease_path.write_text('{"old":true}', encoding="utf-8")

    site_data_export.write_incremental_site_export_artifacts(
        {
            "incremental_country_codes": [],
            "changed_disease_ids": [],
            "country_exports": [
                {"code": "XX", "country_data": {"disease_series": {}}, "site_data": {"v": 1}}
            ],
            "disease_exports": [
                {"disease_id": "D_TEST", "site_data": {"v": 1, "series": []}}
            ],
            "countries_simple": [],
            "diseases": [],
            "reports": [],
            "generated_at": "2026-10-11T00:00:00Z",
            "literature_export": {},
        },
        output,
        public,
    )

    assert json.loads(country_path.read_text(encoding="utf-8")) == {"old": True}
    assert json.loads(disease_path.read_text(encoding="utf-8")) == {"old": True}
    assert (output / "research" / "index.json").is_file()
    assert not (output / "countries" / "xx.json").exists()
    assert not (output / "diseases" / "d_test.json").exists()
