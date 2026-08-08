#!/usr/bin/env python3
"""Serve the built Astro site with gzip and production cache headers.

The standard-library ``http.server`` is deliberately small, but it does not
apply compression or cache policy.  This drop-in server keeps the existing
systemd deployment lightweight while avoiding multi-megabyte uncompressed JS
and JSON responses from the origin.
"""

from __future__ import annotations

import argparse
from email.utils import formatdate
import gzip
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit


COMPRESSIBLE_SUFFIXES = {
    ".css", ".csv", ".html", ".js", ".json", ".mjs", ".svg", ".txt", ".xml",
}
MIN_COMPRESS_BYTES = 512
_GZIP_CACHE: dict[tuple[str, int, int], bytes] = {}


def cache_control_for(request_path: str) -> str:
    if request_path.startswith("/_astro/"):
        return "public, max-age=31536000, immutable"
    if request_path.startswith("/site-data/"):
        return "public, max-age=300, stale-while-revalidate=86400"
    if request_path.startswith("/data/"):
        return "public, max-age=86400"
    if request_path.endswith(".html") or request_path.endswith("/"):
        return "public, max-age=0, must-revalidate"
    return "public, max-age=86400"


def accepts_gzip(header: str | None) -> bool:
    return any(
        token.strip().split(";", 1)[0].strip().lower() == "gzip"
        for token in (header or "").split(",")
    )


def compressed_bytes(path: Path) -> bytes:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _GZIP_CACHE.get(key)
    if cached is not None:
        return cached
    payload = gzip.compress(path.read_bytes(), compresslevel=6, mtime=0)
    _GZIP_CACHE.clear()
    _GZIP_CACHE[key] = payload
    return payload


class SiteRequestHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GlobalIDStatic/1.0"

    def list_directory(self, path: str):  # type: ignore[override]
        self.send_error(HTTPStatus.FORBIDDEN, "Directory listing is disabled")
        return None

    def end_headers(self) -> None:
        request_path = urlsplit(self.path).path
        self.send_header("Cache-Control", cache_control_for(request_path))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def send_head(self):  # type: ignore[override]
        request_path = urlsplit(self.path).path
        path = Path(self.translate_path(unquote(request_path)))
        if path.is_dir():
            if not request_path.endswith("/"):
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", request_path + "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            index = path / "index.html"
            if not index.is_file():
                return self.list_directory(str(path))
            path = index

        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None

        stat = path.stat()
        content_type = self.guess_type(str(path))
        should_compress = (
            path.suffix.lower() in COMPRESSIBLE_SUFFIXES
            and stat.st_size >= MIN_COMPRESS_BYTES
            and accepts_gzip(self.headers.get("Accept-Encoding"))
        )
        if should_compress:
            payload = compressed_bytes(path)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
            self.end_headers()
            return BytesIO(payload)

        file_handle = path.open("rb")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-type", content_type)
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
        self.end_headers()
        return file_handle


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve a static GlobalID build")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4321)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_dir():
        raise SystemExit(f"Static build directory does not exist: {directory}")

    handler = lambda *handler_args, **handler_kwargs: SiteRequestHandler(
        *handler_args, directory=str(directory), **handler_kwargs
    )
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        print(f"Serving {directory} on http://{args.host}:{args.port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
