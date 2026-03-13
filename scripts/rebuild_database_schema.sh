#!/bin/bash
################################################################################
# Database Schema Rebuild Script
# 
# Purpose: Completely rebuild database schema with updated data_quality field
# Usage: ./scripts/rebuild_database_schema.sh [--yes]
#
# Steps:
#   1. Drop all existing tables and types
#   2. Recreate schema from schema.sql
#   3. Optionally reload data using full_rebuild_database.py
#
# Author: GlobalID Team
# Date: 2026-02-17
################################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Database configuration (from .env)
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
DB_NAME=${DB_NAME:-globalid}
DB_USER=${DB_USER:-globalid}
DB_PASSWORD=${DB_PASSWORD:-globalid_dev_password}

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SCHEMA_FILE="$ROOT_DIR/schema.sql"

# Parse arguments
AUTO_CONFIRM=false
RELOAD_DATA=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --yes|-y)
            AUTO_CONFIRM=true
            shift
            ;;
        --reload-data|-r)
            RELOAD_DATA=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --yes, -y           Auto-confirm without prompting"
            echo "  --reload-data, -r   Reload data after schema rebuild"
            echo "  --help, -h          Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                     # Interactive mode"
            echo "  $0 --yes               # Auto-confirm"
            echo "  $0 --yes --reload-data # Rebuild and reload data"
            exit 0
            ;;
        *)
            echo -e "${RED}Error: Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

################################################################################
# Functions
################################################################################

print_header() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
}

print_step() {
    echo -e "\n${GREEN}▶ $1${NC}\n"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

# Execute SQL command
exec_sql() {
    PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "$1"
}

# Execute SQL file
exec_sql_file() {
    PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f "$1"
}

# Check if PostgreSQL is accessible
check_database() {
    print_step "Checking database connection..."
    
    if PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -c "SELECT 1" > /dev/null 2>&1; then
        print_success "Database connection OK"
        return 0
    else
        print_error "Cannot connect to database"
        echo "Host: $DB_HOST:$DB_PORT"
        echo "Database: $DB_NAME"
        echo "User: $DB_USER"
        return 1
    fi
}

# Get current table statistics
get_table_stats() {
    print_step "Current database statistics..."
    
    echo -e "${BLUE}Tables and record counts:${NC}"
    PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME << 'EOF'
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY tablename;
EOF
}

# Drop all tables and types
drop_all_objects() {
    print_step "Dropping all tables and types..."
    
    # Create temporary SQL file
    cat > /tmp/drop_all.sql << 'EOF'
-- Drop all tables (order matters due to foreign keys)
DROP TABLE IF EXISTS ai_conversations CASCADE;
DROP TABLE IF EXISTS task_workbook CASCADE;
DROP TABLE IF EXISTS task_dependencies CASCADE;
DROP TABLE IF EXISTS report_section_runs CASCADE;
DROP TABLE IF EXISTS report_sections CASCADE;
DROP TABLE IF EXISTS reports CASCADE;
DROP TABLE IF EXISTS disease_records CASCADE;
DROP TABLE IF EXISTS task_runs CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS disease_mappings CASCADE;
DROP TABLE IF EXISTS standard_diseases CASCADE;
DROP TABLE IF EXISTS crawl_raw_pages CASCADE;
DROP TABLE IF EXISTS crawl_runs CASCADE;
DROP TABLE IF EXISTS diseases CASCADE;
DROP TABLE IF EXISTS countries CASCADE;

-- Drop all custom types
DROP TYPE IF EXISTS reportsectionrunstatus CASCADE;
DROP TYPE IF EXISTS taskpriority CASCADE;
DROP TYPE IF EXISTS taskstatus CASCADE;
DROP TYPE IF EXISTS tasktype CASCADE;
DROP TYPE IF EXISTS reportstatus CASCADE;
DROP TYPE IF EXISTS reporttype CASCADE;
DROP TYPE IF EXISTS dataquality CASCADE;

SELECT 'All tables and types dropped successfully' AS status;
EOF

    # Execute drop script
    if exec_sql_file /tmp/drop_all.sql; then
        print_success "All tables and types dropped"
        rm -f /tmp/drop_all.sql
        return 0
    else
        print_error "Failed to drop tables"
        rm -f /tmp/drop_all.sql
        return 1
    fi
}

# Create schema from schema.sql
create_schema() {
    print_step "Creating schema from $SCHEMA_FILE..."
    
    if [ ! -f "$SCHEMA_FILE" ]; then
        print_error "Schema file not found: $SCHEMA_FILE"
        return 1
    fi
    
    if exec_sql_file "$SCHEMA_FILE" 2>&1 | grep -v "ERROR"; then
        print_success "Schema created successfully"
        return 0
    else
        print_error "Failed to create schema"
        echo "Check $SCHEMA_FILE for errors"
        return 1
    fi
}

# Reload data using Python script
reload_data() {
    print_step "Reloading data..."
    
    if [ -f "$ROOT_DIR/venv/bin/python" ]; then
        PYTHON_BIN="$ROOT_DIR/venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
    
    if $PYTHON_BIN "$ROOT_DIR/scripts/full_rebuild_database.py" --mode full --yes; then
        print_success "Data reloaded successfully"
        return 0
    else
        print_error "Failed to reload data"
        return 1
    fi
}

# Verify schema
verify_schema() {
    print_step "Verifying schema..."
    
    echo -e "${BLUE}Created tables:${NC}"
    PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME << 'EOF'
SELECT tablename 
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY tablename;
EOF

    echo -e "\n${BLUE}Checking data_quality column type:${NC}"
    PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME << 'EOF'
SELECT 
    column_name,
    data_type,
    character_maximum_length
FROM information_schema.columns
WHERE table_name = 'disease_records' 
  AND column_name = 'data_quality';
EOF
}

################################################################################
# Main Script
################################################################################

print_header "GlobalID Database Schema Rebuild"

echo ""
echo "This script will:"
echo "  1. Drop all existing tables and types"
echo "  2. Recreate schema from schema.sql"
if [ "$RELOAD_DATA" = true ]; then
    echo "  3. Reload data using full_rebuild_database.py"
fi
echo ""
echo "Database: $DB_NAME@$DB_HOST:$DB_PORT"
echo "User: $DB_USER"
echo ""

# Check database connection
if ! check_database; then
    exit 1
fi

# Show current stats
get_table_stats

# Confirmation
if [ "$AUTO_CONFIRM" = false ]; then
    echo ""
    print_warning "This will DELETE ALL DATA in the database!"
    read -p "Are you sure you want to continue? (yes/no): " confirm
    
    if [ "$confirm" != "yes" ]; then
        echo "Aborted."
        exit 0
    fi
fi

# Execute rebuild steps
echo ""
print_header "Starting Database Rebuild"

# Step 1: Drop all objects
if ! drop_all_objects; then
    print_error "Rebuild failed at drop step"
    exit 1
fi

# Step 2: Create schema
if ! create_schema; then
    print_error "Rebuild failed at schema creation step"
    exit 1
fi

# Step 3: Verify
verify_schema

# Step 4: Reload data (optional)
if [ "$RELOAD_DATA" = true ]; then
    if ! reload_data; then
        print_warning "Schema rebuilt but data reload failed"
        exit 1
    fi

    # Step 5: Cleanup suggestions
    print_step "Cleaning up invalid suggestions..."
    if [ -f "$ROOT_DIR/venv/bin/python" ]; then
        PYTHON_BIN="$ROOT_DIR/venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi

    if $PYTHON_BIN "$ROOT_DIR/scripts/cleanup_suggestions.py"; then
        print_success "Suggestions cleaned successfully"
    else
        print_warning "Suggestions cleanup failed"
    fi
fi

# Success
echo ""
print_header "Database Rebuild Complete"
print_success "Schema has been successfully rebuilt!"

if [ "$RELOAD_DATA" = true ]; then
    print_success "Data has been reloaded!"
else
    echo ""
    echo "To reload data, run:"
    echo "  ./venv/bin/python scripts/full_rebuild_database.py --mode full --yes"
fi

echo ""
exit 0
