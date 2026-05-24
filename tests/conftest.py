"""Shared pytest configuration."""

import os

import pytest


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
