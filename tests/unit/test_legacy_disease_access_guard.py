from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_legacy_disease_access import (
    AccessCounts,
    BaselineError,
    check_against_baseline,
    count_legacy_accesses,
    load_baseline,
    scan_repository,
    validate_baseline,
)

ROOT = Path(__file__).resolve().parents[2]


def _baseline(files: dict | None = None) -> dict:
    return {
        "version": 1,
        "classifications": {
            "runtime_read": {
                "description": "Temporary production read pending series migration."
            }
        },
        "files": files or {},
    }


def _allowance(symbol: int, table: int) -> dict:
    return {
        "classification": "runtime_read",
        "reason": "Reviewed temporary migration dependency.",
        "max_references": {
            "DiseaseRecord": symbol,
            "disease_records": table,
        },
    }


def test_counter_tracks_aliases_and_sql_but_ignores_comments_and_docstrings() -> None:
    source = '''
"""DiseaseRecord and disease_records are discussed in this docstring."""
# DiseaseRecord and disease_records in comments are not dependencies.
from src.domain import DiseaseRecord as LegacyRecord

def query_legacy():
    model = LegacyRecord
    return text("SELECT * FROM disease_records JOIN disease_records old USING (time)")
'''

    counts = count_legacy_accesses(source)

    assert counts == AccessCounts(
        disease_record_symbol=2,
        disease_records_table=2,
    )


def test_scan_repository_covers_production_roots_and_excludes_guard_itself(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "src" / "reader.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        "from src.domain import DiseaseRecord\nvalue = DiseaseRecord\n",
        encoding="utf-8",
    )
    self_file = tmp_path / "scripts" / "check_legacy_disease_access.py"
    self_file.parent.mkdir(parents=True)
    self_file.write_text(
        'TABLE = "disease_records"\n',
        encoding="utf-8",
    )

    result = scan_repository(tmp_path)

    assert result == {
        "src/reader.py": AccessCounts(
            disease_record_symbol=2,
            disease_records_table=0,
        )
    }


def test_ratchet_rejects_new_files_and_per_token_count_increases() -> None:
    baseline = _baseline({"src/existing.py": _allowance(symbol=2, table=1)})

    decreased = {
        "src/existing.py": AccessCounts(
            disease_record_symbol=1,
            disease_records_table=1,
        )
    }
    assert check_against_baseline(decreased, baseline) == []

    increased = {
        "src/existing.py": AccessCounts(
            disease_record_symbol=3,
            disease_records_table=1,
        ),
        "dashboard/api/new_reader.py": AccessCounts(
            disease_record_symbol=0,
            disease_records_table=1,
        ),
    }
    violations = check_against_baseline(increased, baseline)

    assert len(violations) == 2
    assert any("increased from allowed 2 to 3" in item.message for item in violations)
    assert any(
        "new direct legacy disease access" in item.message for item in violations
    )


def test_baseline_requires_classification_description_and_migration_reason() -> None:
    missing_description = _baseline()
    missing_description["classifications"]["runtime_read"]["description"] = ""
    with pytest.raises(BaselineError, match="non-empty description"):
        validate_baseline(missing_description)

    missing_reason = _baseline({"scripts/migrate.py": _allowance(symbol=0, table=1)})
    missing_reason["files"]["scripts/migrate.py"]["reason"] = ""
    with pytest.raises(BaselineError, match="migration reason"):
        validate_baseline(missing_reason)


def test_checked_in_baseline_is_valid_json_and_current_tree_does_not_exceed_it() -> (
    None
):
    baseline_path = ROOT / "configs" / "legacy_disease_access_baseline.json"
    # Keep a direct JSON parse assertion so syntax errors are diagnosed before
    # the more descriptive structural validation performed by load_baseline.
    assert isinstance(json.loads(baseline_path.read_text(encoding="utf-8")), dict)

    baseline = load_baseline(baseline_path)
    current = scan_repository(ROOT)
    violations = check_against_baseline(current, baseline)

    assert not violations, "\n".join(str(item) for item in violations)
