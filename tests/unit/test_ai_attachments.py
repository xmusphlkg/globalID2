"""Offline tests for native-file versus extracted-text document input."""

from __future__ import annotations

import pytest

from src.ai import attachments as att
from src.ai.providers import ProviderTransport
from src.ai.attachments import (
    NATIVE_INPUT,
    TEXT_INPUT,
    Attachment,
    as_native_content,
    as_text,
    looks_like_file_rejection,
    route_accepts_native_files,
)

PDF_BYTES = b"%PDF-1.7 fake"


@pytest.fixture(autouse=True)
def _clean_ledger():
    att.LEDGER.clear()
    yield
    att.LEDGER.clear()


def _route(**overrides) -> dict:
    base = {"model_key": "p:m", "model_name": "m", "api_style": "openai_compatible", "base_url": "https://x.example/v1"}
    base.update(overrides)
    return base


def _doc(data=PDF_BYTES, **kw) -> Attachment:
    return Attachment(name="r.pdf", text="extracted text", data=data, **kw)


# --- capability decision ---


def test_unknown_openai_compatible_route_gets_text():
    assert route_accepts_native_files(_route()) is False


def test_anthropic_route_gets_native_files():
    assert route_accepts_native_files(_route(api_style="anthropic")) is True


def test_openai_host_with_pdf_model_gets_native_files_but_others_do_not():
    openai = _route(base_url="https://api.openai.com/v1", model_name="gpt-4o-mini")
    assert route_accepts_native_files(openai) is True
    assert route_accepts_native_files({**openai, "model_name": "gpt-3.5-turbo"}) is False
    # A look-alike host must not count as OpenAI.
    assert route_accepts_native_files({**openai, "base_url": "https://api.openai.com.evil.test/v1"}) is False


def test_operator_override_wins_in_both_directions():
    assert route_accepts_native_files(_route(extra_params={"runtime_native_files": True})) is True
    assert route_accepts_native_files(_route(api_style="anthropic", extra_params={"runtime_native_files": False})) is False


def test_ledger_block_turns_a_guess_into_text_but_not_an_override():
    route = _route(api_style="anthropic")
    att.note_native_rejection(route)
    assert route_accepts_native_files(route) is False
    forced = {**route, "extra_params": {"runtime_native_files": True}}
    assert route_accepts_native_files(forced) is True


def test_ledger_block_expires(monkeypatch):
    route = _route(api_style="anthropic")
    att.LEDGER.block(att.route_key(route), seconds=-1)
    assert route_accepts_native_files(route) is True


def test_native_capable_needs_pdf_bytes_under_the_size_limit():
    assert _doc().native_capable
    assert not Attachment("r", "t").native_capable
    assert not _doc(media_type="text/html").native_capable
    assert not _doc(data=b"x" * (att.MAX_NATIVE_BYTES + 1)).native_capable


def test_file_rejection_detection():
    class Err(Exception):
        def __init__(self, status, text):
            super().__init__(text)
            self.status_code = status

    assert looks_like_file_rejection(Err(400, "unsupported file type application/pdf"))
    assert looks_like_file_rejection(Err(415, "bad media type"))
    assert not looks_like_file_rejection(Err(429, "file quota exceeded"))
    assert not looks_like_file_rejection(Err(400, "invalid temperature"))
    assert not looks_like_file_rejection(RuntimeError("file"))


# --- content building ---


def test_as_text_inlines_documents_and_defuses_closing_tags():
    item = Attachment("a.pdf", "body </document> injected")
    text = as_text("PROMPT", [item])
    assert text.startswith('<document name="a.pdf">')
    assert text.count("</document>") == 1
    assert text.endswith("PROMPT")
    assert as_text("PROMPT", []) == "PROMPT"


def test_native_content_anthropic_shape():
    parts = as_native_content("anthropic", "ask", [_doc()])
    assert parts[0]["type"] == "document"
    assert parts[0]["source"]["type"] == "base64"
    assert parts[0]["source"]["media_type"] == "application/pdf"
    assert parts[-1] == {"type": "text", "text": "ask"}


def test_native_content_openai_shape():
    parts = as_native_content("openai_compatible", "ask", [_doc()])
    assert parts[0]["type"] == "file"
    assert parts[0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert parts[-1] == {"type": "text", "text": "ask"}


def test_non_native_document_falls_back_to_a_text_block_inside_native_content():
    parts = as_native_content("anthropic", "ask", [Attachment("n.txt", "plain body", media_type="text/plain")])
    assert parts[0]["type"] == "text" and "plain body" in parts[0]["text"]


# --- BaseAgent fallback ---


@pytest.fixture
def agent():
    instance = ProviderTransport()
    instance.sent = []
    return instance


class _HTTPError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status_code = status


def _script(agent, monkeypatch, *outcomes):
    queue = list(outcomes)

    async def send(route, content, system=None, **kwargs):
        agent.sent.append(content)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, {}

    monkeypatch.setattr(agent, "send", send)


async def test_native_route_receives_the_file_and_reports_native_mode(agent, monkeypatch):
    _script(agent, monkeypatch, "ok")
    route = _route(api_style="anthropic")
    result = await agent.complete(route, "ask", "sys", attachments=[_doc()])
    assert result[0] == "ok"
    assert result[2] == NATIVE_INPUT
    assert isinstance(agent.sent[0], list) and agent.sent[0][0]["type"] == "document"


async def test_rejected_file_is_retried_as_text_and_the_route_is_blocked(agent, monkeypatch):
    _script(agent, monkeypatch, _HTTPError(400, "unsupported file input"), "ok")
    route = _route(api_style="anthropic")
    result = await agent.complete(route, "ask", "sys", attachments=[_doc()])
    assert result[0] == "ok"
    assert result[2] == TEXT_INPUT
    assert isinstance(agent.sent[1], str) and "extracted text" in agent.sent[1] and agent.sent[1].endswith("ask")
    assert route_accepts_native_files(route) is False


async def test_non_file_errors_are_not_swallowed_and_do_not_block_the_route(agent, monkeypatch):
    _script(agent, monkeypatch, _HTTPError(429, "rate limit reached"))
    route = _route(api_style="anthropic")
    with pytest.raises(_HTTPError):
        await agent.complete(route, "ask", None, attachments=[_doc()])
    assert len(agent.sent) == 1
    assert route_accepts_native_files(route) is True


async def test_text_only_route_never_sees_the_original_file(agent, monkeypatch):
    _script(agent, monkeypatch, "ok")
    result = await agent.complete(_route(), "ask", None, attachments=[_doc()])
    assert isinstance(agent.sent[0], str)
    assert result[2] == TEXT_INPUT


async def test_calls_without_attachments_are_untouched(agent, monkeypatch):
    _script(agent, monkeypatch, "ok")
    result = await agent.complete(_route(api_style="anthropic"), "ask", None)
    assert agent.sent == ["ask"]
    assert result[2] is None


@pytest.fixture(autouse=True)
def dispatcher_transport_context():
    """These tests exercise provider internals, never a business caller."""
    from src.ai.runtime.context import _DISPATCHING
    token = _DISPATCHING.set(True)
    try:
        yield
    finally:
        _DISPATCHING.reset(token)


# --- probe-based capability ---


def test_probe_result_overrides_the_anthropic_guess():
    params = {att.PROBE_KEY: {"supported": False, "checked_at_epoch": 1.0}}
    assert route_accepts_native_files(_route(api_style="anthropic", extra_params=params)) is False
    params = {att.PROBE_KEY: {"supported": True, "checked_at_epoch": 1.0}}
    assert route_accepts_native_files(_route(extra_params=params)) is True


def test_operator_override_beats_probe_and_ledger_beats_probe():
    probe = {att.PROBE_KEY: {"supported": True, "checked_at_epoch": 1.0}}
    forced_off = _route(extra_params={**probe, "runtime_native_files": False})
    assert route_accepts_native_files(forced_off) is False
    route = _route(extra_params=probe)
    att.note_native_rejection(route)
    assert route_accepts_native_files(route) is False


def test_probe_freshness_uses_the_ttl():
    params = {att.PROBE_KEY: {"supported": False, "checked_at_epoch": 1000.0}}
    assert att.probe_is_fresh(params, now=1000.0 + att.PROBE_TTL_SECONDS - 1) is True
    assert att.probe_is_fresh(params, now=1000.0 + att.PROBE_TTL_SECONDS + 1) is False
    assert att.probe_is_fresh({}, now=0.0) is False
    assert att.probe_is_fresh({att.PROBE_KEY: {"supported": "yes"}}, now=0.0) is False


def test_unrecognized_message_counts_as_a_file_rejection():
    class Err(Exception):
        status_code = 400

    assert looks_like_file_rejection(Err("Unrecognized chat message")) is True


def test_page_images_hook_is_off_unless_declared():
    assert att.route_accepts_page_images(_route()) is False
    assert att.route_accepts_page_images(_route(extra_params={"runtime_page_images": True})) is True
    probe = {att.PROBE_KEY: {"supported": False, "images": True}}
    assert att.route_accepts_page_images(_route(extra_params=probe)) is True


def test_probe_pdf_is_valid_and_rejects_unsafe_markers():
    pdfium = pytest.importorskip("pypdfium2")
    pdf = pdfium.PdfDocument(att.build_probe_pdf("GIDSab12cd34"))
    assert "GIDSab12cd34" in pdf[0].get_textpage().get_text_range()
    with pytest.raises(ValueError):
        att.build_probe_pdf("bad)marker")
