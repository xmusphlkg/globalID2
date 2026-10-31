"""Shared pytest configuration."""

import os
import signal

import pytest

# One stuck test used to hold the GitHub job until its 60-minute limit.
# Fail that test instead, after enough time for the slowest real case.
_CI_TEST_TIMEOUT_SECONDS = 300


def _ci_test_timeout_enabled() -> bool:
    return os.environ.get("CI") == "1"


def _handle_ci_test_timeout(signum, frame):
    raise TimeoutError(
        f"CI stopped this test after {_CI_TEST_TIMEOUT_SECONDS}s with no result"
    )


def pytest_addoption(parser):
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Run tests marked as network that call live external services.",
    )


def pytest_collection_modifyitems(config, items):
    skip_requested = os.environ.get("SKIP_NETWORK_TESTS", "").lower() in {"1", "true", "yes"}
    run_requested = config.getoption("--run-network") or os.environ.get(
        "GLOBALID_RUN_NETWORK_TESTS",
        "",
    ).lower() in {"1", "true", "yes"}

    if run_requested and not skip_requested:
        return

    reason = "live network tests are skipped by default; pass --run-network to enable"
    if skip_requested:
        reason = "SKIP_NETWORK_TESTS is set"
    skip_network = pytest.mark.skip(reason=reason)

    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    """Bound each test on CI so a stalled test fails with its node id."""

    if not _ci_test_timeout_enabled():
        yield
        return
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_ci_test_timeout)
    signal.alarm(_CI_TEST_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
