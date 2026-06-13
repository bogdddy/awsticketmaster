"""
Experiment orchestrator. Runs load tests and captures results.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
import base64
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCHMARKS_DIR = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("experiment")


def wait_for_queue_drain(rabbitmq_host, rabbitmq_user, rabbitmq_password,
                         queue="tickets.buy", timeout=120, poll_interval=5):
    url = f"http://{rabbitmq_host}:15672/api/queues/%2F/{queue}"
    credentials = base64.b64encode(f"{rabbitmq_user}:{rabbitmq_password}".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {credentials}")

    log.info("Waiting for queue '%s' to drain (timeout=%ds)...", queue, timeout)
    elapsed = 0
    while elapsed < timeout:
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read().decode())
            depth = data.get("messages", 0)
        except Exception:
            log.warning("Cannot check queue depth, proceeding anyway")
            return
        if depth == 0:
            log.info("Queue empty after ~%ds", elapsed)
            return
        log.info("Queue has %d messages, waiting %ds...", depth, poll_interval)
        time.sleep(poll_interval)
        elapsed += poll_interval
    log.warning("Timeout waiting for queue drain (%ds). Proceeding with %d messages.", timeout, depth)  # noqa: F821


def run_loadgen(env_overrides: dict, duration: int = None, rabbitmq_host=None, rabbitmq_user=None, rabbitmq_password=None):
    """Run the load generator with environment overrides."""
    cmd = [sys.executable, "-m", "loadgen.app.main"]
    env = os.environ.copy()
    env.update(env_overrides)
    if rabbitmq_host:
        env["RABBITMQ_HOST"] = rabbitmq_host
    if rabbitmq_user:
        env["RABBITMQ_USER"] = rabbitmq_user
    if rabbitmq_password:
        env["RABBITMQ_PASS"] = rabbitmq_password
    log.info("Starting loadgen with: %s", {k: v for k, v in env_overrides.items() if k.startswith("LOAD_")})
    proc = subprocess.Popen(cmd, env=env, cwd=PROJECT_ROOT)
    if duration:
        time.sleep(duration)
        proc.terminate()
        proc.wait()
    else:
        proc.wait()
    return proc.returncode


def export_results(db_host=None, db_port=None, db_user=None, db_password=None, db_name=None, since=None, until=None):
    """Export results from PostgreSQL to CSV."""
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "analysis", "export_results.py")]

    if db_host:
        cmd.extend(["--host", db_host])
    if db_port:
        cmd.extend(["--port", str(db_port)])
    if db_user:
        cmd.extend(["--user", db_user])
    if db_password:
        cmd.extend(["--password", db_password])
    if db_name:
        cmd.extend(["--db", db_name])
    if since:
        cmd.extend(["--since", since])
    if until:
        cmd.extend(["--until", until])

    log.info("Exporting results from %s (since=%s, until=%s)...", db_host or os.environ.get("POSTGRES_HOST", "localhost"), since or "all", until or "all")
    subprocess.run(cmd, check=True, cwd=BENCHMARKS_DIR)


def generate_plots():
    """Generate plots from exported CSV."""
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "analysis", "plot_results.py")]
    log.info("Generating plots...")
    subprocess.run(cmd, check=True, cwd=BENCHMARKS_DIR)


def experiment_calibration(args):
    """A) Calibrate C: measure throughput at different rates with N=1."""
    rates = [int(r) for r in args.rates.split(",")]
    for rate in rates:
        experiment_start = datetime.now(timezone.utc).isoformat()
        log.info("=== Calibration: rate=%d msg/s (start=%s) ===", rate, experiment_start)
        run_loadgen({
            "LOAD_MODE": "numbered",
            "LOAD_LOW_RATE": str(rate),
            "LOAD_HIGH_RATE": str(rate),
            "LOAD_T1_LOW_S": "30",
            "LOAD_T2_RAMP_S": "0",
            "LOAD_T3_SPIKE_S": "0",
            "LOAD_T4_SUSTAINED_S": "60",
            "LOAD_T5_COOLDOWN_S": "0",
            "LOAD_SPIKE_BURSTS": "0",
        }, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
        experiment_end = datetime.now(timezone.utc).isoformat()
        wait_for_queue_drain(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password,
                             timeout=args.drain_wait)
        export_results(
            db_host=args.pg_host,
            db_port=args.pg_port,
            db_user=args.pg_user,
            db_password=args.pg_password,
            db_name=args.pg_db,
            since=experiment_start,
            until=experiment_end,
        )
    generate_plots()


def experiment_speedup(args):
    """B) Speedup: vary worker count, measure throughput."""
    C = 10  # capacidad base por worker (medida en calibración rate_10)
    workers = [int(w) for w in args.workers.split(",")]
    for i, n in enumerate(workers):
        rate = args.rate if args.rate else n * C // 2  # 50% de capacidad para evitar backlog
        experiment_start = datetime.now(timezone.utc).isoformat()
        log.info("=== Speedup run %d/%d: workers=%d rate=%d (start=%s) ===", i + 1, len(workers), n, rate, experiment_start)
        run_loadgen({
            "LOAD_MODE": "numbered",
            "LOAD_LOW_RATE": str(rate),
            "LOAD_HIGH_RATE": str(rate),
            "LOAD_T1_LOW_S": "30",
            "LOAD_T2_RAMP_S": "0",
            "LOAD_T3_SPIKE_S": "0",
            "LOAD_T4_SUSTAINED_S": "90",
            "LOAD_T5_COOLDOWN_S": "0",
            "LOAD_SPIKE_BURSTS": "0",
        }, duration=120, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
        experiment_end = datetime.now(timezone.utc).isoformat()
        wait_for_queue_drain(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password,
                             timeout=args.drain_wait)
        export_results(
            db_host=args.pg_host,
            db_port=args.pg_port,
            db_user=args.pg_user,
            db_password=args.pg_password,
            db_name=args.pg_db,
            since=experiment_start,
            until=experiment_end,
        )
    generate_plots()


def experiment_stress(args):
    """C) Stress: increasing load to find saturation point."""
    low_rate = args.min_rate if args.min_rate else 10
    high_rate = args.max_rate if args.max_rate else 60
    experiment_start = datetime.now(timezone.utc).isoformat()
    log.info("=== Stress test: workers=%d rate=%d->%d (start=%s) ===", args.workers, low_rate, high_rate, experiment_start)
    run_loadgen({
        "LOAD_MODE": "numbered",
        "LOAD_LOW_RATE": str(low_rate),
        "LOAD_HIGH_RATE": str(high_rate),
        "LOAD_T1_LOW_S": "30",
        "LOAD_T2_RAMP_S": "120",
        "LOAD_T3_SPIKE_S": "10",
        "LOAD_T4_SUSTAINED_S": "60",
        "LOAD_T5_COOLDOWN_S": "30",
        "LOAD_SPIKE_BURSTS": "2",
    }, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
    experiment_end = datetime.now(timezone.utc).isoformat()
    wait_for_queue_drain(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password,
                         timeout=args.drain_wait)
    export_results(
        db_host=args.pg_host,
        db_port=args.pg_port,
        db_user=args.pg_user,
        db_password=args.pg_password,
        db_name=args.pg_db,
        since=experiment_start,
        until=experiment_end,
    )
    generate_plots()


def experiment_elasticity(args):
    """D) Elasticity: full Z(t) with autoscaling."""
    low_rate = args.elasticity_low if args.elasticity_low else 10
    high_rate = args.elasticity_high if args.elasticity_high else 50
    experiment_start = datetime.now(timezone.utc).isoformat()
    log.info("=== Elasticity test: Z(t) low=%d high=%d (start=%s) ===", low_rate, high_rate, experiment_start)
    run_loadgen({
        "LOAD_MODE": "numbered",
        "LOAD_LOW_RATE": str(low_rate),
        "LOAD_HIGH_RATE": str(high_rate),
        "LOAD_T1_LOW_S": "60",
        "LOAD_T2_RAMP_S": "600",
        "LOAD_T3_SPIKE_S": "30",
        "LOAD_T4_SUSTAINED_S": "120",
        "LOAD_T5_COOLDOWN_S": "60",
        "LOAD_SPIKE_BURSTS": "3",
    }, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
    experiment_end = datetime.now(timezone.utc).isoformat()
    wait_for_queue_drain(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password,
                         timeout=args.drain_wait)
    export_results(
        db_host=args.pg_host,
        db_port=args.pg_port,
        db_user=args.pg_user,
        db_password=args.pg_password,
        db_name=args.pg_db,
        since=experiment_start,
        until=experiment_end,
    )
    generate_plots()


def experiment_contention(args):
    """E) Contention: uniform vs hotspot."""
    hotspot_pct = args.hotspot_pct
    hotspot_traffic = args.hotspot_traffic
    low_rate = args.contention_low if args.contention_low else 10
    high_rate = args.contention_high if args.contention_high else 30
    experiment_start = datetime.now(timezone.utc).isoformat()
    log.info("=== Contention test: hotspot_pct=%.0f%%, traffic=%.0f%% rate=%d->%d (start=%s) ===", hotspot_pct, hotspot_traffic, low_rate, high_rate, experiment_start)
    run_loadgen({
        "LOAD_MODE": "numbered",
        "LOAD_HOTSPOT_PCT_SEATS": str(hotspot_pct),
        "LOAD_HOTSPOT_PCT_TRAFFIC": str(hotspot_traffic),
        "LOAD_LOW_RATE": str(low_rate),
        "LOAD_HIGH_RATE": str(high_rate),
        "LOAD_T1_LOW_S": "30",
        "LOAD_T2_RAMP_S": "30",
        "LOAD_T3_SPIKE_S": "0",
        "LOAD_T4_SUSTAINED_S": "120",
        "LOAD_T5_COOLDOWN_S": "30",
        "LOAD_SPIKE_BURSTS": "0",
    }, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
    experiment_end = datetime.now(timezone.utc).isoformat()
    wait_for_queue_drain(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password,
                         timeout=args.drain_wait)
    export_results(
        db_host=args.pg_host,
        db_port=args.pg_port,
        db_user=args.pg_user,
        db_password=args.pg_password,
        db_name=args.pg_db,
        since=experiment_start,
        until=experiment_end,
    )
    generate_plots()


def main():
    parser = argparse.ArgumentParser(description="Run benchmarks")
    parser.add_argument("--type", required=True,
                        choices=["calibration", "speedup", "stress", "elasticity", "contention"])
    parser.add_argument("--workers", default="1")
    parser.add_argument("--rates", default="10,50,100")
    parser.add_argument("--rate", type=int, default=0,
                        help="Offered load for speedup (0 = auto: workers * C // 2)")
    parser.add_argument("--min-rate", type=int, default=0,
                        help="Starting rate for stress (0 = auto: 10 msg/s)")
    parser.add_argument("--max-rate", type=int, default=0,
                        help="Peak rate for stress (0 = auto: 60 msg/s)")
    parser.add_argument("--elasticity-low", type=int, default=0,
                        help="Low rate for elasticity (0 = auto: 10 msg/s)")
    parser.add_argument("--elasticity-high", type=int, default=0,
                        help="High rate for elasticity (0 = auto: 50 msg/s)")
    parser.add_argument("--hotspot-pct", type=float, default=5.0)
    parser.add_argument("--hotspot-traffic", type=float, default=80.0)
    parser.add_argument("--contention-low", type=int, default=0,
                        help="Low rate for contention (0 = auto: 10 msg/s)")
    parser.add_argument("--contention-high", type=int, default=0,
                        help="High rate for contention (0 = auto: 30 msg/s)")
    parser.add_argument("--drain-wait", type=int, default=120,
                        help="Max seconds to wait for RabbitMQ queue to drain after loadgen (default: 120)")
    parser.add_argument("--pg-host", default=os.environ.get("POSTGRES_HOST"))
    parser.add_argument("--pg-port", type=int, default=int(os.environ.get("POSTGRES_PORT", "5432")))
    parser.add_argument("--pg-db", default=os.environ.get("POSTGRES_DB", "ticketdb"))
    parser.add_argument("--pg-user", default=os.environ.get("POSTGRES_USER", "ticketapp"))
    parser.add_argument("--pg-password", default=os.environ.get("POSTGRES_PASS"))
    parser.add_argument("--rabbitmq-host", default=os.environ.get("RABBITMQ_HOST", "10.0.1.10"))
    parser.add_argument("--rabbitmq-user", default=os.environ.get("RABBITMQ_USER", "admin"))
    parser.add_argument("--rabbitmq-password", default=os.environ.get("RABBITMQ_PASS", "ddd"))
    args = parser.parse_args()

    experiments = {
        "calibration": experiment_calibration,
        "speedup": experiment_speedup,
        "stress": experiment_stress,
        "elasticity": experiment_elasticity,
        "contention": experiment_contention,
    }

    experiments[args.type](args)
    log.info("Experiment '%s' complete.", args.type)


if __name__ == "__main__":
    main()
