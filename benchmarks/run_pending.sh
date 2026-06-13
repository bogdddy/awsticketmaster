#!/bin/bash
set -e

PG_HOST="10.0.1.20"
PG_USER="ticketapp"
PG_PASSWORD="ddd"
PG_DB="ticketdb"
RABBITMQ_HOST="10.0.1.10"
RABBITMQ_USER="admin"
RABBITMQ_PASSWORD="ddd"
ECS_CLUSTER="awsticket-cluster"
ECS_SERVICE="awsticket-worker-svc"
AWS_REGION="us-east-1"

# Verifica/instala AWS CLI
if ! command -v aws &>/dev/null; then
    echo "AWS CLI no encontrado. Instalando..."
    curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
    unzip -q awscliv2.zip
    ./aws/install
    rm -rf aws awscliv2.zip
    echo "AWS CLI instalado"
fi

RUN_TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RESULTS_DIR="./benchmark_results/${RUN_TIMESTAMP}"
EXPORTS_DIR="./results"
mkdir -p "$RESULTS_DIR"

echo "=========================================="
echo "AWSTicket - Elasticity only (SQS trigger fix)"
echo "Timestamp: $RUN_TIMESTAMP"
echo "Results: $RESULTS_DIR"
echo "=========================================="

cleanup() {
    echo "=== Cleaning environment ==="
    python3 cleanup.py \
        --pg-host "$PG_HOST" \
        --pg-user "$PG_USER" \
        --pg-password "$PG_PASSWORD" \
        --rabbitmq-host "$RABBITMQ_HOST" \
        --rabbitmq-user "$RABBITMQ_USER" \
        --rabbitmq-password "$RABBITMQ_PASSWORD" \
        --drain --drain-timeout 120 --force --retry 2
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo "ERROR: Cleanup failed. Aborting."
        exit 1
    fi
    echo "Cleanup complete"
}

scale_workers() {
    local desired=$1
    echo "=== Scaling ECS workers to $desired ==="
    aws ecs update-service --cluster "$ECS_CLUSTER" --service "$ECS_SERVICE" \
        --desired-count "$desired" --region "$AWS_REGION" > /dev/null
    echo "Waiting for workers to reach $desired..."
    for i in {1..30}; do
        local running=$(aws ecs describe-services --cluster "$ECS_CLUSTER" \
            --services "$ECS_SERVICE" --region "$AWS_REGION" \
            --query "services[0].runningCount" --output text)
        if [ "$running" = "$desired" ]; then
            echo "Workers ready: $running"
            return 0
        fi
        echo "  runningCount=$running, waiting 10s... ($i/30)"
        sleep 10
    done
    echo "ERROR: Workers did not reach $desired in time"
    exit 1
}

save_results() {
    local experiment=$1
    local run_name=$2
    local dest="$RESULTS_DIR/$experiment/$run_name"
    mkdir -p "$dest"
    if [ -d "$EXPORTS_DIR" ] && [ "$(ls -A $EXPORTS_DIR/*.csv 2>/dev/null)" ]; then
        cp $EXPORTS_DIR/*.csv "$dest/"
        echo "Results saved to $dest"
    else
        echo "Warning: No CSV files found in $EXPORTS_DIR"
    fi
}

BASE_OPTS="--pg-host $PG_HOST --pg-user $PG_USER --pg-password $PG_PASSWORD --rabbitmq-host $RABBITMQ_HOST --rabbitmq-user $RABBITMQ_USER --rabbitmq-password $RABBITMQ_PASSWORD"

# ===== ELASTICITY run 1 (Z(t) 10->50, min 4 workers, ramp 600s) =====
echo ""
echo "=========================================="
echo "1/2 ELASTICITY run 1 - Z(t) 10->50, min 4 workers, ramp 600s"
echo "=========================================="
scale_workers 4
cleanup
PYTHONPATH=../loadgen python3 run_experiment.py --type elasticity --workers-min 4 --workers-max 20 $BASE_OPTS
save_results "elasticity" "run_1"

# ===== ELASTICITY run 2 (rampa 600s, min 4 workers) =====
echo ""
echo "=========================================="
echo "2/2 ELASTICITY run 2 - Z(t) 10->50, min 4 workers"
echo "=========================================="
cleanup
PYTHONPATH=../loadgen python3 run_experiment.py --type elasticity --workers-min 4 --workers-max 20 $BASE_OPTS
save_results "elasticity" "run_2"

echo ""
echo "=========================================="
echo "Elasticity experiments complete!"
echo "Timestamp: $RUN_TIMESTAMP"
echo "Results saved to: $RESULTS_DIR"
echo "=========================================="
