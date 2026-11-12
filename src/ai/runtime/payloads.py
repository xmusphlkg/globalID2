"""Content-addressed request payloads kept outside the active queue row."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def request_digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def write_request(task_uuid: str, content: dict[str, Any]) -> tuple[str, str]:
    # Prompt/document text remains on disk rather than copied into queue JSON.
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    directory = ROOT / "data" / "raw" / "ai" / task_uuid
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as pending:
            pending_path = Path(pending.name)
            try:
                pending.write(encoded)
                pending.flush()
                pending_path.replace(path)
            finally:
                pending_path.unlink(missing_ok=True)
    return str(path.relative_to(ROOT)), digest


def read_request(path: str) -> dict[str, Any]:
    resolved = (ROOT / path).resolve()
    resolved.relative_to((ROOT / "data" / "raw" / "ai").resolve())
    return json.loads(resolved.read_text())
