"""Offline source adapters: existing parsers consume captured response bytes."""

from __future__ import annotations
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import requests
from .artifacts import read_artifact


def request_key(url, params=None):
    parsed = urlsplit(str(url))
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if params:
        source = params.items() if isinstance(params, dict) else params
        for key, value in source:
            values = value if isinstance(value, (list, tuple)) else [value]
            pairs.extend((str(key), str(item)) for item in values)
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(sorted(pairs)), "")
    )


class ArtifactReplayMixin:
    """A crawler transport which fails closed when a response was not captured."""

    replay_artifacts = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        original = self.session
        owner = self

        class OfflineSession:
            def get(self, url, **options):
                return owner.get(url, **options)

            def post(self, url, **options):
                return owner.post(url, **options)

            def request(self, method, url, **options):
                if str(method).upper() == "GET":
                    return owner.get(url, **options)
                raise RuntimeError("Offline source transport refuses non-GET request")

            def send(self, *args, **options):
                raise RuntimeError(
                    "Offline source transport refuses direct network send"
                )

            def __getattr__(self, name):
                if name in {"head", "options", "put", "patch", "delete"}:
                    raise RuntimeError(
                        "Offline source transport refuses unsupported HTTP method"
                    )
                return getattr(original, name)

        self.session = OfflineSession()

    def get(self, url, **kwargs):
        key = request_key(url, kwargs.get("params"))
        candidates = [
            artifact
            for artifact in self.replay_artifacts
            if artifact.source_url and request_key(artifact.source_url) == key
        ]
        if not candidates:
            raise RuntimeError(
                f"No captured source artifact for offline request: {key}"
            )
        # Repeated metadata requests are common. All bytes still pass digest validation.
        artifact = candidates[-1]
        response = requests.Response()
        response.status_code = 200
        response.url = artifact.source_url
        response._content = read_artifact(artifact)
        response._content_consumed = True
        response.encoding = "utf-8"
        media_type = artifact.metadata_.get("media_type")
        if not media_type:
            media_type = (
                "application/pdf"
                if artifact.kind.endswith("pdf")
                else "application/json"
            )
        response.headers["Content-Type"] = media_type
        return response

    def post(self, url, **kwargs):
        raise RuntimeError(
            "Offline POST requires a source-specific captured request key"
        )


def offline_crawler_type(crawler_type, artifacts):
    return type(
        f"Offline{crawler_type.__name__}",
        (ArtifactReplayMixin, crawler_type),
        {"replay_artifacts": tuple(artifacts)},
    )
