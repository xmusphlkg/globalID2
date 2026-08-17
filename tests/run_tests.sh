#!/usr/bin/env bash
# Canonical local/CI test runner. Live-network tests remain opt-in via pytest.

set -uo pipefail

cd "$(dirname "$0")/.."

COUNTRY="CN"
TEST_TYPE="all"
VERBOSE_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --country)
            if [[ $# -lt 2 || ! "$2" =~ ^[A-Za-z0-9_-]{2,16}$ ]]; then
                echo "--country requires a short country code" >&2
                exit 2
            fi
            COUNTRY="$2"
            shift 2
            ;;
        --verbose|-v)
            VERBOSE_ARGS=(-vv)
            shift
            ;;
        --type|-t)
            if [[ $# -lt 2 ]]; then
                echo "--type requires a value" >&2
                exit 2
            fi
            TEST_TYPE="$2"
            shift 2
            ;;
        --help|-h)
            cat <<'EOF'
Usage: tests/run_tests.sh [OPTIONS]

Options:
  --country COUNTRY   Country code for the crawler suite (default: CN)
  --type TYPE         all, unit, integration, crawlers, situation, automation,
                      or email (default: all)
  --verbose, -v       Show verbose pytest output
  --help, -h          Show this help message

The all/unit/integration/situation/automation modes never opt into live-network
tests. Use pytest --run-network explicitly for a deliberate live-source probe.
The email mode validates configuration and may send a real test message.
EOF
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Use --help for usage information" >&2
            exit 2
            ;;
    esac
done

PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Python executable is unavailable: $PYTHON_BIN" >&2
    exit 2
fi

run_test() {
    local test_name="$1"
    shift
    echo "Running ${test_name}..."
    if "$@"; then
        echo "${test_name}: PASSED"
        return 0
    fi
    echo "${test_name}: FAILED" >&2
    return 1
}

case "$TEST_TYPE" in
    all)
        run_test "complete offline test suite" \
            "$PYTHON_BIN" -m pytest -q "${VERBOSE_ARGS[@]}"
        ;;
    unit)
        run_test "unit test suite" \
            "$PYTHON_BIN" -m pytest -q tests/unit "${VERBOSE_ARGS[@]}"
        ;;
    integration)
        run_test "integration test suite" \
            "$PYTHON_BIN" -m pytest -q tests/integration tests/test_integration.py "${VERBOSE_ARGS[@]}"
        ;;
    crawlers)
        run_test "crawler tests (${COUNTRY})" \
            "$PYTHON_BIN" -m pytest -q tests/test_crawlers.py --country "$COUNTRY" "${VERBOSE_ARGS[@]}"
        ;;
    situation)
        run_test "Situation Room tests" \
            "$PYTHON_BIN" -m pytest -q \
            tests/unit/test_situation_automation.py \
            tests/unit/test_situation_alert_dispatch.py \
            tests/unit/test_situation_v3.py \
            tests/unit/test_situation_v3_backtest.py \
            tests/unit/test_situation_v3_review_api.py \
            tests/unit/test_situation_quality.py \
            tests/e2e/test_situation_static_build.py \
            "${VERBOSE_ARGS[@]}"
        ;;
    automation)
        run_test "release automation tests" \
            "$PYTHON_BIN" -m pytest -q \
            tests/unit/test_situation_automation.py \
            tests/unit/test_situation_alert_dispatch.py \
            tests/unit/test_data_release_checks.py \
            tests/unit/test_data_release_pipeline.py \
            tests/unit/test_data_release_resilience.py \
            tests/unit/test_data_release_service.py \
            "${VERBOSE_ARGS[@]}"
        ;;
    email)
        run_test "Microsoft Graph email configuration" \
            "$PYTHON_BIN" scripts/test_email_config.py
        ;;
    *)
        echo "Unknown test type: $TEST_TYPE" >&2
        echo "Use --help for valid test types" >&2
        exit 2
        ;;
esac
