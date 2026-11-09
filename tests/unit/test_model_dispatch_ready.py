import json
from pathlib import Path

from src.services import model_dispatch_ready as ready


def test_dispatch_readiness_requires_same_live_worker_process(tmp_path, monkeypatch):
    marker = tmp_path / "ready.json"
    monkeypatch.setattr(ready, "READY_FILE", marker)
    monkeypatch.setattr(ready, "_start_time", lambda pid: "100")
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"python\0-m\0src.services.task_worker\0")
    assert not ready.worker_ready()
    marker.write_text(json.dumps({"pid": 42, "start_time": "99"}))
    assert not ready.worker_ready()
    marker.write_text(json.dumps({"pid": 42, "start_time": "100"}))
    assert ready.worker_ready()
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"unrelated process")
    assert not ready.worker_ready()
