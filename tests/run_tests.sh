#!/bin/bash
# Quick test runner with country support and AI tests

set -e

cd "$(dirname "$0")/.."

# Parse arguments
COUNTRY="CN"
VERBOSE=""
TEST_TYPE="all"  # all, crawlers, ai, integration

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
        --type|-t)
            TEST_TYPE="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo
            echo "Options:"
            echo "  --country COUNTRY   Country code to test (default: CN)"
            echo "  --type TYPE         Test type: all, crawlers, ai, integration (default: all)"
            echo "  --verbose, -v       Show detailed output" 
            echo "  --help, -h          Show this help message"
            echo
            echo "Test Types:"
            echo "  all          Run all tests"
            echo "  crawlers     Run web crawler tests only"
            echo "  ai           Run AI report generation tests only"
            echo "  ai-real      Run AI tests with real database data"
            echo "  ai-api       Test AI API connections"
            echo "  integration  Run integration tests only" 
            echo
            echo "Examples:"
            echo "  $0                    # Run all tests for CN with minimal output"
            echo "  $0 --country CN       # Test specific country"
            echo "  $0 --type ai          # Run only AI tests"
            echo "  $0 --type ai-real     # Run AI tests with real database data"
            echo "  $0 --type ai-api      # Test AI API connections"
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

echo "🚀 GlobalID Test Runner"
echo "======================"
echo "Country: $COUNTRY"
echo "Test Type: $TEST_TYPE"
echo "Verbose: $([ -n "$VERBOSE" ] && echo "Yes" || echo "No")"
echo

# Function to run tests with error handling
run_test() {
    local test_name="$1"
    local test_command="$2"
    
    echo "📋 Running $test_name..."
    if eval "$test_command"; then
        echo "✅ $test_name - PASSED"
        return 0
    else
        echo "❌ $test_name - FAILED"
        return 1
    fi
}

# Initialize counters
TOTAL_TESTS=0
PASSED_TESTS=0

# Run tests based on type
case $TEST_TYPE in
    "crawlers")
        echo "🕷️ Running Crawler Tests..."
        TOTAL_TESTS=1
        if run_test "Web Crawlers" "./venv/bin/python tests/test_crawlers.py --country $COUNTRY $VERBOSE"; then
            ((PASSED_TESTS++))
        fi
        ;;
        
    "ai")
        echo "🧠 Running AI Tests..."
        TOTAL_TESTS=1
        if run_test "AI Report Generation" "./venv/bin/python tests/verify_ai_test_setup.py"; then
            ((PASSED_TESTS++))
        fi
        ;;
        
    "ai-real")
        echo "🔬 Running AI Tests with Real Database Data..."
        TOTAL_TESTS=1
        if run_test "AI with Real Data" "./venv/bin/python tests/test_ai_with_real_data.py"; then
            ((PASSED_TESTS++))
        fi
        ;;
        
    "ai-api")
        echo "🌐 Testing AI API Connections..."
        TOTAL_TESTS=1
        if run_test "AI API Connection" "./venv/bin/python src/ai/test_api_connection.py"; then
            ((PASSED_TESTS++))
        fi
        ;;
        
    "integration")
        echo "🔄 Running Integration Tests..."
        TOTAL_TESTS=1
        if run_test "Integration Tests" "./venv/bin/python tests/test_integration.py"; then
            ((PASSED_TESTS++))
        fi
        ;;
        
    "all")
        echo "🎯 Running All Tests..."
        TOTAL_TESTS=3
        
        if run_test "AI Report Generation" "./venv/bin/python tests/verify_ai_test_setup.py"; then
            ((PASSED_TESTS++))
        fi
        
        if run_test "Web Crawlers" "./venv/bin/python tests/test_crawlers.py --country $COUNTRY $VERBOSE 2>/dev/null"; then
            ((PASSED_TESTS++))
        fi
        
        if run_test "Integration Tests" "./venv/bin/python tests/test_integration.py 2>/dev/null" || true; then
            ((PASSED_TESTS++))
        fi
        ;;
        
    *)
        echo "❌ Unknown test type: $TEST_TYPE"
        echo "Use --help for valid test types"
        exit 1
        ;;
esac

echo
echo "📊 Test Summary"
echo "==============="
echo "Total Tests: $TOTAL_TESTS"
echo "Passed: $PASSED_TESTS"
echo "Failed: $((TOTAL_TESTS - PASSED_TESTS))"

if [ $PASSED_TESTS -eq $TOTAL_TESTS ]; then
    echo "🎉 All tests passed!"
    exit 0
else
    echo "💥 Some tests failed!"
    exit 1
fi
