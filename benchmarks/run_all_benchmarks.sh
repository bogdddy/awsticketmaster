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

echo "Run timestamp: $RUN_TIMESTAMP"
echo "Results will be saved to: $RESULTS_DIR"

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
        ls -la $EXPORTS_DIR 2>/dev/null || echo "Directory $EXPORTS_DIR does not exist"
    fi
}

echo "=========================================="
echo "AWSTicket Experiment Suite"
echo "=========================================="
echo "Start time: $(date)"
echo ""

BASE_OPTS="--pg-host $PG_HOST --pg-user $PG_USER --pg-password $PG_PASSWORD --rabbitmq-host $RABBITMQ_HOST --rabbitmq-user $RABBITMQ_USER --rabbitmq-password $RABBITMQ_PASSWORD"

# ==========================================
# A) CALIBRATION (1 worker)
# ==========================================
echo ""
echo "=========================================="
echo "A) CALIBRATION - Measuring worker capacity"
echo "=========================================="
scale_workers 1

for rate in 10 50 100; do
    echo ""
    echo "--- Calibration: rate=$rate msg/s ---"
    cleanup
    PYTHONPATH=../loadgen python3 run_experiment.py --type calibration --rates "$rate" $BASE_OPTS
    save_results "calibration" "rate_${rate}"
    echo "Waiting 10s before next run..."
    sleep 10
done

# ==========================================
# B) SPEEDUP (1,2,4,8 workers)
# ==========================================
echo ""
echo "=========================================="
echo "B) SPEEDUP - Scaling workers"
echo "=========================================="

for workers in 1 2 4 8; do
    echo ""
    echo "--- Speedup: workers=$workers ---"
    scale_workers "$workers"
    cleanup
    PYTHONPATH=../loadgen python3 run_experiment.py --type speedup --workers "$workers" $BASE_OPTS
    save_results "speedup" "workers_${workers}"
    echo "Waiting 15s before next run..."
    sleep 15
done

# ==========================================
# C) STRESS (8 workers, autoscaler OFF)
# ==========================================
echo ""
echo "=========================================="
echo "C) STRESS - Finding saturation point"
echo "=========================================="
echo "Desactivando autoscaler para stress..."
SCALING_UUID=$(aws lambda list-event-source-mappings --function-name awsticket-scaling-controller --region "$AWS_REGION" --query "EventSourceMappings[0].UUID" --output text)
aws lambda update-event-source-mapping --uuid "$SCALING_UUID" --no-enabled --region "$AWS_REGION" 2>/dev/null || true
sleep 5
scale_workers 8
cleanup
PYTHONPATH=../loadgen python3 run_experiment.py --type stress --workers 8 $BASE_OPTS
save_results "stress" "max_rate_60"
echo "Reactivando autoscaler..."
aws lambda update-event-source-mapping --uuid "$SCALING_UUID" --enabled --region "$AWS_REGION" 2>/dev/null || true

# ==========================================
# D) ELASTICITY (autoscaling 1->20)
# ==========================================
echo ""
echo "=========================================="
echo "D) ELASTICITY - Z(t) workload with autoscaling"
echo "=========================================="
scale_workers 4

for run in 1 2; do
    echo ""
    echo "--- Elasticity: run $run/2 ---"
    cleanup
    PYTHONPATH=../loadgen python3 run_experiment.py --type elasticity --workers-min 4 --workers-max 20 $BASE_OPTS
    save_results "elasticity" "run_${run}"
    echo "Waiting 20s before next run..."
    sleep 20
done

# ==========================================
# E) CONTENTION (4 workers)
# ==========================================
echo ""
echo "=========================================="
echo "E) CONTENTION - Uniform vs Hotspot"
echo "=========================================="
scale_workers 4

# Uniform
echo ""
echo "--- Contention: Uniform distribution ---"
cleanup
PYTHONPATH=../loadgen python3 run_experiment.py --type contention --workers 4 --hotspot-pct 100 --hotspot-traffic 100 $BASE_OPTS
save_results "contention" "uniform"

# Hotspot 80/5
echo ""
echo "--- Contention: Hotspot 80/5 ---"
cleanup
PYTHONPATH=../loadgen python3 run_experiment.py --type contention --workers 4 --hotspot-pct 5 --hotspot-traffic 80 $BASE_OPTS
save_results "contention" "hotspot_80_5"

echo ""
echo "=========================================="
echo "All benchmarks complete!"
echo "=========================================="
echo "End time: $(date)"
echo ""
echo "Results saved to: $RESULTS_DIR/"
echo ""
echo "Next steps:"
echo "1. Generate plots:"
echo "   cd analysis && python3 plot_results.py --input-dir ../benchmark_results --output-dir ./plots"
echo ""
echo "2. Collect summary:"
echo "   python3 collect_results.py"
