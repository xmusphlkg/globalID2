"""Projection receipts must match the deployed source and mapping rules."""

from pathlib import Path

import pytest

from src.generation import site_data_dependencies as dependencies


ROOT = Path(dependencies.__file__).resolve().parents[2]


def test_dependency_version_reads_existing_deployed_sources():
    """A removed or renamed dependency must fail here before publication."""
    version = dependencies.dependency_version()
    assert len(version) == 64
    assert int(version, 16) >= 0
    assert dependencies.dependency_version() == version


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/generation/site_series_projection.py",
        "src/core/reporting_period.py",
        "src/services/disease_series_policy.py",
        "configs/disease_ontology.json",
        "configs/mapping/us.csv",
    ],
)
def test_rule_change_invalidates_completed_receipt(tmp_path, monkeypatch, relative_path):
    public = tmp_path / "public"
    public.mkdir()
    dependencies.save_receipt(
        {"country_dependency_inputs": {"US": {"metadata": "old", "series": {}}},
         "disease_exports": []},
        tmp_path,
        public,
    )
    assert dependencies.load_receipt(tmp_path)["countries"] == {
        "US": {"metadata": "old", "series": {}}
    }
    changed_path = ROOT / relative_path
    assert changed_path.is_file()
    read_bytes = Path.read_bytes

    def changed_bytes(path):
        body = read_bytes(path)
        return body + b"\nchanged projection rule\n" if path == changed_path else body

    monkeypatch.setattr(Path, "read_bytes", changed_bytes)
    assert dependencies.load_receipt(tmp_path) == {"countries": {}, "diseases": {}}


def test_missing_required_source_cannot_promote_a_completed_receipt(tmp_path, monkeypatch):
    missing_path = ROOT / "src/core/reporting_period.py"
    read_bytes = Path.read_bytes

    def missing_source(path):
        if path == missing_path:
            raise FileNotFoundError(str(path))
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", missing_source)
    with pytest.raises(FileNotFoundError, match="reporting_period.py"):
        dependencies.save_receipt(
            {"country_dependency_inputs": {"US": {}}, "disease_exports": []},
            tmp_path,
            tmp_path / "public",
        )
    assert not (tmp_path / dependencies.RECEIPT_NAME).exists()
