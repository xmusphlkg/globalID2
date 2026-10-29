"""Documents sent to a model: the original file when the route accepts it, extracted text otherwise.

Many providers read a PDF directly and keep its tables and layout; others only
take text. A caller hands over one :class:`Attachment` carrying both forms and
this module decides, per model route, which one travels:

1. ``extra_params.runtime_native_files`` on the route is an explicit operator
   override (``true`` or ``false``);
2. a route that recently rejected a file is sent text until the block expires;
3. otherwise a conservative capability guess: Anthropic routes and OpenAI
   models documented to read PDFs get the file, every other route gets text.

A guess can be wrong, so a native request that the provider rejects as a file
problem is retried once as text and the route is remembered as text-only for a
while. Callers never need to know which form was used beyond the
``input_mode`` the agent reports.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

PDF = "application/pdf"
NATIVE_INPUT = "native_file"
TEXT_INPUT = "text"

#: Above this size even capable providers tend to refuse; send text instead.
MAX_NATIVE_BYTES = 20 * 1024 * 1024
#: How long a route that rejected a file stays on text before it is tried again.
BLOCK_SECONDS = 6 * 60 * 60

_OPENAI_PDF_MODELS = re.compile(r"^(gpt-4o|gpt-4\.1|gpt-5|o1|o3|o4)", re.I)
_REJECTION_STATUSES = {400, 413, 415, 422}
_REJECTION_WORDS = re.compile(
    r"(file|document|pdf|input_file|content[ _-]?type|media[ _-]?type|unsupported|multimodal|too large|page)",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Attachment:
    """One document. ``text`` is always present; ``data`` is the original file."""

    name: str
    text: str
    media_type: str = PDF
    data: bytes | None = None

    @property
    def native_capable(self) -> bool:
        return self.data is not None and self.media_type == PDF and len(self.data) <= MAX_NATIVE_BYTES


class _Ledger:
    """Routes that rejected a file, with the time the block lifts."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def block(self, key: str, *, seconds: float = BLOCK_SECONDS) -> None:
        with self._lock:
            self._until[key] = time.monotonic() + seconds

    def blocked(self, key: str) -> bool:
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return False
            if until <= time.monotonic():
                del self._until[key]
                return False
            return True

    def clear(self) -> None:
        with self._lock:
            self._until.clear()


LEDGER = _Ledger()


def route_key(route: Mapping[str, Any]) -> str:
    return str(route.get("model_key") or route.get("model_name") or "")


def route_accepts_native_files(route: Mapping[str, Any]) -> bool:
    params = route.get("extra_params")
    forced = params.get("runtime_native_files") if isinstance(params, Mapping) else None
    if isinstance(forced, bool):
        return forced
    if LEDGER.blocked(route_key(route)):
        return False
    if str(route.get("api_style") or "").lower() == "anthropic":
        return True
    host = urlparse(str(route.get("base_url") or "")).hostname or ""
    return host == "api.openai.com" and bool(_OPENAI_PDF_MODELS.match(str(route.get("model_name") or "")))


def looks_like_file_rejection(error: BaseException) -> bool:
    """A client error that names the file, not a timeout, quota or server fault."""

    status = getattr(error, "status_code", None)
    return status in _REJECTION_STATUSES and bool(_REJECTION_WORDS.search(str(error)))


def note_native_rejection(route: Mapping[str, Any]) -> None:
    LEDGER.block(route_key(route))


def _safe(text: str) -> str:
    # The wrapper tag must stay unambiguous even if the document mentions it.
    return text.replace("</document", "</ document")


def as_text(prompt: str, attachments: Iterable[Attachment]) -> str:
    """Prompt with every document inlined as extracted text."""

    blocks = [f'<document name="{a.name}">\n{_safe(a.text)}\n</document>' for a in attachments]
    return "\n\n".join([*blocks, prompt])


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def as_native_content(style: str, prompt: str, attachments: Sequence[Attachment]) -> list[dict[str, Any]]:
    """Provider message content: files natively, anything else as text blocks."""

    anthropic = style == "anthropic"
    parts: list[dict[str, Any]] = []
    for item in attachments:
        if item.native_capable:
            assert item.data is not None
            if anthropic:
                parts.append(
                    {
                        "type": "document",
                        "title": item.name,
                        "source": {"type": "base64", "media_type": item.media_type, "data": _b64(item.data)},
                    }
                )
            else:
                parts.append(
                    {
                        "type": "file",
                        "file": {
                            "filename": item.name,
                            "file_data": f"data:{item.media_type};base64,{_b64(item.data)}",
                        },
                    }
                )
        else:
            parts.append({"type": "text", "text": as_text("", [item]).strip()})
    parts.append({"type": "text", "text": prompt})
    return parts


__all__ = [
    "Attachment",
    "LEDGER",
    "NATIVE_INPUT",
    "TEXT_INPUT",
    "as_native_content",
    "as_text",
    "looks_like_file_rejection",
    "note_native_rejection",
    "route_accepts_native_files",
]
