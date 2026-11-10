"""The model-center file probe trusts only a reply that contains the random marker."""

from types import SimpleNamespace

import pytest

from src.ai.catalogue.probes import probe_native_file


def _unexpected_openai(**kwargs):
    raise AssertionError("Anthropic probes must use the configured protocol")


class _HTTPError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _anthropic_client(reply=None, error=None):
    class Messages:
        async def create(self, **kwargs):
            if error is not None:
                raise error
            block = kwargs["messages"][0]["content"][0]
            assert block["type"] == "document"
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=reply(kwargs))]
            )

    class Client:
        def __init__(self, **kwargs):
            self.messages = Messages()

    return Client


@pytest.fixture(autouse=True)
def _dispatcher(monkeypatch):
    monkeypatch.setattr("src.ai.runtime.require_dispatcher", lambda: None)


ROUTE = {
    "api_style": "anthropic",
    "model_name": "m",
    "base_url": "http://g/v1",
    "api_key": "k",
}


async def test_marker_in_reply_means_supported(monkeypatch):
    # The probe PDF is the only source of the marker, so echo it back from the request.
    import base64

    import pypdfium2

    def read_marker(kwargs):
        data = kwargs["messages"][0]["content"][0]["source"]["data"]
        return (
            pypdfium2.PdfDocument(base64.b64decode(data))[0]
            .get_textpage()
            .get_text_range()
            .strip()
        )

    assert (
        await probe_native_file(
            ROUTE,
            anthropic_factory=_anthropic_client(reply=read_marker),
            openai_factory=_unexpected_openai,
        )
        is True
    )


async def test_silently_dropped_file_means_unsupported(monkeypatch):
    assert (
        await probe_native_file(
            ROUTE,
            anthropic_factory=_anthropic_client(reply=lambda kw: "NOFILE"),
            openai_factory=_unexpected_openai,
        )
        is False
    )


async def test_file_rejection_means_unsupported_but_other_errors_are_inconclusive(
    monkeypatch,
):
    assert (
        await probe_native_file(
            ROUTE,
            anthropic_factory=_anthropic_client(
                error=_HTTPError(400, "unsupported file input")
            ),
            openai_factory=_unexpected_openai,
        )
        is False
    )
    assert (
        await probe_native_file(
            ROUTE,
            anthropic_factory=_anthropic_client(error=_HTTPError(429, "rate limit")),
            openai_factory=_unexpected_openai,
        )
        is None
    )
