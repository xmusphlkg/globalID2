#!/usr/bin/env python3
"""Poll a public Situation JSON endpoint until it matches the deployed artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import socket
import sys
import time
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class DeploymentVerificationError(RuntimeError):
    """A safe-to-log public deployment verification failure."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _public_https_url(value: str) -> str:
    text = value.strip()
    parsed = urlsplit(text)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise DeploymentVerificationError("public_data_url_https_required")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DeploymentVerificationError("invalid_public_data_url") from exc
    netloc = parsed.hostname.lower()
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc += f":{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def _attempt_url(url: str, expected_digest: str, attempt: int) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.extend(
        [
            ("release_sha256", expected_digest[:16]),
            ("verification_attempt", str(attempt)),
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def verify_deployment(
    artifact_path: Path,
    public_data_url: str,
    *,
    attempts: int = 12,
    timeout_seconds: float = 10,
    initial_delay_seconds: float = 2,
    maximum_delay_seconds: float = 20,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if attempts < 1 or attempts > 30:
        raise DeploymentVerificationError("attempts_out_of_range")
    for value, field, upper in (
        (timeout_seconds, "timeout_seconds", 60),
        (initial_delay_seconds, "initial_delay_seconds", 60),
        (maximum_delay_seconds, "maximum_delay_seconds", 120),
    ):
        if not math.isfinite(value) or value < 0 or value > upper:
            raise DeploymentVerificationError(f"{field}_out_of_range")
    if timeout_seconds == 0:
        raise DeploymentVerificationError("timeout_seconds_out_of_range")
    url = _public_https_url(public_data_url)
    try:
        expected = artifact_path.read_bytes()
    except FileNotFoundError as exc:
        raise DeploymentVerificationError("deployment_artifact_not_found") from exc
    except OSError as exc:
        raise DeploymentVerificationError("deployment_artifact_unreadable") from exc
    if not expected:
        raise DeploymentVerificationError("deployment_artifact_empty")
    try:
        parsed_expected = json.loads(expected)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentVerificationError("deployment_artifact_invalid_json") from exc
    if not isinstance(parsed_expected, dict):
        raise DeploymentVerificationError("deployment_artifact_object_required")
    expected_digest = _sha256(expected)
    last_error = "public_content_mismatch"
    completed_attempts = 0
    for attempt in range(1, attempts + 1):
        completed_attempts = attempt
        request = Request(
            _attempt_url(url, expected_digest, attempt),
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": "globalid-deployment-verifier/1",
            },
        )
        retryable = True
        try:
            with opener(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 0) or response.getcode() or 0)
                body = response.read(len(expected) + 1)
            if status < 200 or status >= 300:
                last_error = f"public_http_error:{status}"
                retryable = status in {408, 425, 429} or status >= 500
            elif len(body) > len(expected):
                last_error = "public_content_size_mismatch"
            elif _sha256(body) == expected_digest:
                return {
                    "status": "verified",
                    "attempts": attempt,
                    "bytes": len(expected),
                    "sha256": expected_digest,
                }
            else:
                last_error = "public_content_mismatch"
        except HTTPError as exc:
            last_error = f"public_http_error:{exc.code}"
            retryable = exc.code in {408, 425, 429} or exc.code >= 500
        except (URLError, socket.timeout, TimeoutError, OSError) as exc:
            # Never include the exception text because proxy errors can echo
            # request details. The endpoint contains no credential regardless.
            last_error = f"public_transport_error:{type(exc).__name__}"
        if not retryable or attempt == attempts:
            break
        delay = min(maximum_delay_seconds, initial_delay_seconds * (2 ** (attempt - 1)))
        if delay:
            sleep(delay)
    raise DeploymentVerificationError(
        f"deployment_not_observable:{last_error}:attempts={completed_attempts}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--public-data-url", required=True)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--initial-delay-seconds", type=float, default=2)
    parser.add_argument("--maximum-delay-seconds", type=float, default=20)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify_deployment(
            args.artifact,
            args.public_data_url,
            attempts=args.attempts,
            timeout_seconds=args.timeout_seconds,
            initial_delay_seconds=args.initial_delay_seconds,
            maximum_delay_seconds=args.maximum_delay_seconds,
        )
    except DeploymentVerificationError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
