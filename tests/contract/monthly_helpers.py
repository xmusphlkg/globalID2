import hashlib
from pathlib import Path
from types import SimpleNamespace
from src.data.storage.series_observation_store import SeriesObservationStore
from tests.contract.compare import assert_observations_match


def artifact(payload, *, code, kind, url, metadata=None):
    directory = Path("data/raw") / code.lower() / "contract"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}.bin"
    path.write_bytes(payload)
    return SimpleNamespace(relative_path=str(path), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), kind=kind, metadata_=metadata or {}, source_url=url)


def assert_same_facts(actual, expected, code, source_id, *, fixture_code=None):
    assert actual.rows == expected.rows
    store = SeriesObservationStore()
    before = store.build_observations(expected.rows, code, source_id=source_id)
    after = store.build_observations(actual.rows, code, source_id=source_id)
    if not before.observations:
        # Source-native categories use the registry holding-series policy.
        # Their complete source identity must remain unchanged too.
        old_selection = store.select_registry_rows(expected.rows, code, source_id=source_id)
        new_selection = store.select_registry_rows(actual.rows, code, source_id=source_id)
        assert old_selection.unregistered_rows
        assert new_selection.unregistered_rows == old_selection.unregistered_rows
    assert_observations_match(after.observations, before.observations)
    assert_golden(expected.rows, before.observations, fixture_code=fixture_code or code)


def assert_golden(rows, observations, *, fixture_code):
    """Freeze the pre-switch source rows and public observation contract.

    Regeneration is an explicit test-maintenance operation. Normal test runs
    never create or update baselines, so changing a shared parser cannot make
    the pre/post comparison silently agree on newly changed values.
    """
    import json
    import os
    from datetime import date, datetime
    from decimal import Decimal
    path = Path(__file__).resolve().parents[1] / "fixtures/ingestion" / fixture_code / "golden.json"
    def encode(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        raise TypeError(type(value).__name__)
    keys = ("time", "series_code", "geography_key", "dimension_key", "value", "suppressed", "quality_status")
    snapshot = {"rows": rows, "observations": [{**{key: row[key] for key in keys}, "metadata": {"missing_value_status": (row.get("metadata") or {}).get("missing_value_status")}} for row in observations]}
    if os.getenv("UPDATE_INGESTION_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=encode) + "\n")
    baseline = json.loads(path.read_text())
    assert rows == baseline["rows"]
    assert_observations_match(observations, baseline["observations"])
