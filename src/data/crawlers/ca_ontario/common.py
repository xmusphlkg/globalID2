"""Small normalizers shared by Ontario discovery and Power BI responses."""
from __future__ import annotations

import json
import re
from typing import Any


def _norm_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\ufeff", "").split()).strip()


def _norm_header(value: object) -> str:
    text = _norm_text(value).casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def _json_object(value: object, *, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{label} must be a JSON object")
