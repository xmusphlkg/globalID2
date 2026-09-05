import ast
import inspect
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
    ]


def test_direct_download_default_workers_respect_tight_cgroup_memory() -> None:
    gib = 1024 * 1024 * 1024

    assert direct_download_files._default_export_workers(16, 4 * gib) == 1
    assert direct_download_files._default_export_workers(16, 8 * gib) == 2
    assert direct_download_files._default_export_workers(16, 16 * gib) == 4


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
