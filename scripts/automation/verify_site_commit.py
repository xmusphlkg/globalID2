#!/usr/bin/env python3
"""Fail a release until the public HTML exposes the expected source commit."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import math
import re
import socket
import sys
import time
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class SiteCommitVerificationError(RuntimeError):
    """A safe-to-log release provenance verification failure."""


class _SourceCommitParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.commit: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "meta":
            return
        values = {key.lower(): value for key, value in attrs}
        if values.get("name") == "gids-source-commit":
            self.commit = values.get("content")


def _site_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise SiteCommitVerificationError("public_site_url_https_required")
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


def _attempt_url(url: str, expected: str, attempt: int) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((("release_commit", expected[:16]), ("verification_attempt", str(attempt))))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def verify_site_commit(
    public_site_url: str,
    expected_source_commit: str,
    *,
    attempts: int = 12,
    timeout_seconds: float = 10,
    initial_delay_seconds: float = 2,
    maximum_delay_seconds: float = 20,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    expected = expected_source_commit.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise SiteCommitVerificationError("expected_source_commit_invalid")
    if attempts < 1 or attempts > 30:
        raise SiteCommitVerificationError("attempts_out_of_range")
    for value, field, maximum in ((timeout_seconds, "timeout_seconds", 60), (initial_delay_seconds, "initial_delay_seconds", 60), (maximum_delay_seconds, "maximum_delay_seconds", 120)):
        if not math.isfinite(value) or value < 0 or value > maximum:
            raise SiteCommitVerificationError(f"{field}_out_of_range")
    if timeout_seconds == 0:
        raise SiteCommitVerificationError("timeout_seconds_out_of_range")
    url = _site_url(public_site_url)
    last_error = "source_commit_missing"
    completed_attempts = 0
    for attempt in range(1, attempts + 1):
        completed_attempts = attempt
        request = Request(_attempt_url(url, expected, attempt), headers={"Accept": "text/html", "Cache-Control": "no-cache", "Pragma": "no-cache", "User-Agent": "globalid-release-verifier/1"})
        retryable = True
        try:
            with opener(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or response.getcode() or 0)
                body = response.read(1_048_577)
            if status < 200 or status >= 300:
                last_error = f"public_http_error:{status}"
                retryable = status in {408, 425, 429} or status >= 500
            elif len(body) > 1_048_576:
                last_error = "public_html_too_large"
            else:
                parser = _SourceCommitParser()
                parser.feed(body.decode("utf-8", errors="replace"))
                observed = (parser.commit or "").strip().lower()
                if observed == expected:
                    return {"status": "verified", "attempts": attempt, "source_commit": expected}
                last_error = "source_commit_missing" if not observed else "source_commit_mismatch"
        except HTTPError as exc:
            last_error = f"public_http_error:{exc.code}"
            retryable = exc.code in {408, 425, 429} or exc.code >= 500
        except (URLError, socket.timeout, TimeoutError, OSError) as exc:
            last_error = f"public_transport_error:{type(exc).__name__}"
        if not retryable or attempt == attempts:
            break
        delay = min(maximum_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1)))
        if delay:
            sleep(delay)
    raise SiteCommitVerificationError(f"site_commit_not_observable:{last_error}:attempts={completed_attempts}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-site-url", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--initial-delay-seconds", type=float, default=2)
    parser.add_argument("--maximum-delay-seconds", type=float, default=20)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify_site_commit(args.public_site_url, args.expected_source_commit, attempts=args.attempts, timeout_seconds=args.timeout_seconds, initial_delay_seconds=args.initial_delay_seconds, maximum_delay_seconds=args.maximum_delay_seconds)
    except SiteCommitVerificationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
