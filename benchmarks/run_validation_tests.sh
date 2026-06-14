#!/bin/bash
set -e

# Run the 2 validation tests (oversell + fault tolerance) independently.
# These use the same infra as the benchmarks but only run the consistency tests.
# Usage:
#   ./run_validation_tests.sh              # uses defaults
#   ./run_validation_tests.sh --skip-ft    # skip fault tolerance (needs AWS CLI + ECS)

PG_HOST="${PG_HOST:-10.0.1.20}"
PG_USER="${PG_USER:-ticketapp}"
PG_PASSWORD="${PG_PASSWORD:-ddd}"
PG_DB="${PG_DB:-ticketdb}"
RABBITMQ_HOST="${RABBITMQ_HOST:-10.0.1.10}"
RABBITMQ_USER="${RABBITMQ_USER:-admin}"
RABBITMQ_PASSWORD="${RABBITMQ_PASSWORD:-ddd}"

SKIP_FT=0
if [ "$1" = "--skip-ft" ]; then
    SKIP_FT=1
fi

RUN_TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RESULTS_DIR="./benchmark_results/${RUN_TIMESTAMP}_validation"
mkdir -p "$RESULTS_DIR"

echo "=========================================="
echo "AWSTicket Validation Tests"
echo "Timestamp: $RUN_TIMESTAMP"
echo "Results dir: $RESULTS_DIR"
echo "=========================================="
echo ""

# 1. OVERSIGHT VERIFICATION
echo "=========================================="
echo "TEST 1: Oversight Verification"
echo "Pool: 100 seats, 500 requests, 8 threads"
echo "=========================================="
python3 ./tests/test_oversell.py \
    --pg-host "$PG_HOST" --pg-user "$PG_USER" --pg-pass "$PG_PASSWORD" \
    --pg-db "$PG_DB" \
    --seats 100 --requests 500 --threads 8 \
    --output-dir "$RESULTS_DIR/oversight/pool_100"
echo ""

# 2. FAULT TOLERANCE (requires ECS + AWS CLI)
if [ "$SKIP_FT" -eq 0 ]; then
    echo "=========================================="
    echo "TEST 2: Fault Tolerance"
    echo "Kill 1 worker during 20 rps load"
    echo "=========================================="
    python3 ./tests/test_fault_tolerance.py \
        --rabbitmq-host "$RABBITMQ_HOST" --rabbitmq-user "$RABBITMQ_USER" --rabbitmq-pass "$RABBITMQ_PASSWORD" \
        --pg-host "$PG_HOST" --pg-user "$PG_USER" --pg-pass "$PG_PASSWORD" \
        --pg-db "$PG_DB" \
        --messages 100 --rate 20 --min-workers 2 --kill-at-sec 3 \
        --output-dir "$RESULTS_DIR/fault_tolerance/worker_kill"
else
    echo "=========================================="
    echo "SKIPPING: Fault Tolerance test (--skip-ft)"
    echo "=========================================="
fi

echo ""
echo "=========================================="
echo "Validation tests complete!"
echo "Results saved to: $RESULTS_DIR/"
echo "=========================================="
