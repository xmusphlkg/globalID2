import pytest

from src.services.data_release.surveillance_publish import (
    capture_surveillance_baseline, publish_surveillance_data,
)
from tests.unit.test_surveillance_publish import CONFIG, Store, generation


PREFIX = "site-data/research-publication"


def test_research_fenced_upload_reuses_committed_objects_without_readback(tmp_path):
    source, run = tmp_path / "source", tmp_path / "run"
    relative, _ = generation(source)
    run.mkdir()
    (run / "current.json").write_bytes((source / "current.json").read_bytes())

    class AuditStore(Store):
        def get_object(self, key):
            assert "/objects/" not in key
            return super().get_object(key)

    store = AuditStore()
    capture_surveillance_baseline(run, CONFIG, prefix=PREFIX, client=store)
    # The object may already exist after an interrupted attempt. It is trusted
    # as immutable rather than downloaded and checked again.
    store.objects[f"{PREFIX}/{relative}"] = (source / relative).read_bytes()
    result = publish_surveillance_data(run, CONFIG, prefix=PREFIX, client=store,
        objects_dir=source / "objects", require_starting_baseline=True, trust_frozen_objects=True)
    assert result["uploaded"] == 0 and result["unchanged"] == 1
    assert store.writes[-1][0] == f"{PREFIX}/current.json"


def test_trusted_mode_cannot_bypass_other_channels_or_baseline(tmp_path):
    generation(tmp_path)
    with pytest.raises(ValueError, match="fenced research"):
        publish_surveillance_data(tmp_path, CONFIG, client=Store(), trust_frozen_objects=True)
    with pytest.raises(ValueError, match="fenced research"):
        publish_surveillance_data(tmp_path, CONFIG, prefix=PREFIX, client=Store(), trust_frozen_objects=True)
