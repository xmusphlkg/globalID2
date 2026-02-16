#!/bin/bash
# Quick test runner with country support

set -e

cd "$(dirname "$0")/.."

# Parse arguments
COUNTRY="CN"
VERBOSE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --country)
            COUNTRY="$2"
            shift 2
            ;;
        --verbose|-v)
            VERBOSE="--verbose"
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo
            echo "Options:"
            echo "  --country COUNTRY   Country code to test (default: CN)"
            echo "  --verbose, -v       Show detailed output"
            echo "  --help, -h          Show this help message"
            echo
            echo "Examples:"
            echo "  $0                    # Test CN with minimal output"
            echo "  $0 --country CN       # Test specific country"
            echo "  $0 --verbose          # Show detailed logs"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Run tests
./venv/bin/python tests/test_crawlers.py --country "$COUNTRY" $VERBOSE 2>/dev/null
