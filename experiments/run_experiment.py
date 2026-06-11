"""
Experiment orchestrator. Runs load tests and captures results.
"""
import argparse
import logging
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("experiment")


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


def export_results(db_host=None, db_port=None, db_user=None, db_password=None, db_name=None):
    """Export results from PostgreSQL to CSV."""
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, "analysis", "export_results.py")]

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

    env = os.environ.copy()
    if "POSTGRES_HOST" in env:
        pass
    if "POSTGRES_PASS" in env:
        pass

    log.info("Exporting results from %s...", db_host or os.environ.get("POSTGRES_HOST", "localhost"))
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)


def generate_plots():
    """Generate plots from exported CSV."""
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, "analysis", "plot_results.py")]
    log.info("Generating plots...")
    subprocess.run(cmd, check=True, cwd=PROJECT_ROOT)


def experiment_calibration(args):
    """A) Calibrate C: measure throughput at different rates with N=1."""
    rates = [int(r) for r in args.rates.split(",")]
    for rate in rates:
        log.info("=== Calibration: rate=%d msg/s ===", rate)
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
        export_results(
            db_host=args.pg_host,
            db_port=args.pg_port,
            db_user=args.pg_user,
            db_password=args.pg_password,
            db_name=args.pg_db,
        )
    generate_plots()


def experiment_speedup(args):
    """B) Speedup: vary worker count, measure throughput."""
    workers = [int(w) for w in args.workers.split(",")]
    for i, n in enumerate(workers):
        log.info("=== Speedup run %d/%d: workers=%d ===", i + 1, len(workers), n)
        run_loadgen({
            "LOAD_MODE": "numbered",
            "LOAD_LOW_RATE": str(args.rate),
            "LOAD_HIGH_RATE": str(args.rate),
            "LOAD_T1_LOW_S": "30",
            "LOAD_T2_RAMP_S": "0",
            "LOAD_T3_SPIKE_S": "0",
            "LOAD_T4_SUSTAINED_S": "90",
            "LOAD_T5_COOLDOWN_S": "0",
            "LOAD_SPIKE_BURSTS": "0",
        }, duration=120, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
        time.sleep(10)
        export_results(
            db_host=args.pg_host,
            db_port=args.pg_port,
            db_user=args.pg_user,
            db_password=args.pg_password,
            db_name=args.pg_db,
        )
        time.sleep(5)
    generate_plots()


def experiment_stress(args):
    """C) Stress: increasing load to find saturation point."""
    log.info("=== Stress test: workers=%d max-rate=%d ===", args.workers, args.max_rate)
    run_loadgen({
        "LOAD_MODE": "numbered",
        "LOAD_LOW_RATE": "50",
        "LOAD_HIGH_RATE": str(args.max_rate),
        "LOAD_T1_LOW_S": "30",
        "LOAD_T2_RAMP_S": "120",
        "LOAD_T3_SPIKE_S": "10",
        "LOAD_T4_SUSTAINED_S": "60",
        "LOAD_T5_COOLDOWN_S": "30",
        "LOAD_SPIKE_BURSTS": "2",
    }, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
    export_results(
        db_host=args.pg_host,
        db_port=args.pg_port,
        db_user=args.pg_user,
        db_password=args.pg_password,
        db_name=args.pg_db,
    )
    generate_plots()


def experiment_elasticity(args):
    """D) Elasticity: full Z(t) with autoscaling."""
    log.info("=== Elasticity test: Z(t) full ===")
    run_loadgen({
        "LOAD_MODE": "numbered",
        "LOAD_LOW_RATE": "50",
        "LOAD_HIGH_RATE": "500",
        "LOAD_T1_LOW_S": "60",
        "LOAD_T2_RAMP_S": "60",
        "LOAD_T3_SPIKE_S": "30",
        "LOAD_T4_SUSTAINED_S": "120",
        "LOAD_T5_COOLDOWN_S": "60",
        "LOAD_SPIKE_BURSTS": "3",
    }, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
    export_results(
        db_host=args.pg_host,
        db_port=args.pg_port,
        db_user=args.pg_user,
        db_password=args.pg_password,
        db_name=args.pg_db,
    )
    generate_plots()


def experiment_contention(args):
    """E) Contention: uniform vs hotspot."""
    hotspot_pct = args.hotspot_pct
    hotspot_traffic = args.hotspot_traffic
    log.info("=== Contention test: hotspot_pct=%.0f%%, traffic=%.0f%% ===", hotspot_pct, hotspot_traffic)
    run_loadgen({
        "LOAD_MODE": "numbered",
        "LOAD_HOTSPOT_PCT_SEATS": str(hotspot_pct),
        "LOAD_HOTSPOT_PCT_TRAFFIC": str(hotspot_traffic),
        "LOAD_LOW_RATE": "50",
        "LOAD_HIGH_RATE": "300",
        "LOAD_T1_LOW_S": "30",
        "LOAD_T2_RAMP_S": "30",
        "LOAD_T3_SPIKE_S": "0",
        "LOAD_T4_SUSTAINED_S": "120",
        "LOAD_T5_COOLDOWN_S": "30",
        "LOAD_SPIKE_BURSTS": "0",
    }, rabbitmq_host=args.rabbitmq_host, rabbitmq_user=args.rabbitmq_user, rabbitmq_password=args.rabbitmq_password)
    export_results(
        db_host=args.pg_host,
        db_port=args.pg_port,
        db_user=args.pg_user,
        db_password=args.pg_password,
        db_name=args.pg_db,
    )
    generate_plots()


def main():
    parser = argparse.ArgumentParser(description="Run experiments")
    parser.add_argument("--type", required=True,
                        choices=["calibration", "speedup", "stress", "elasticity", "contention"])
    parser.add_argument("--workers", default="1")
    parser.add_argument("--rates", default="10,50,100")
    parser.add_argument("--rate", type=int, default=300)
    parser.add_argument("--max-rate", type=int, default=1000)
    parser.add_argument("--workers-min", type=int, default=1)
    parser.add_argument("--workers-max", type=int, default=20)
    parser.add_argument("--hotspot-pct", type=float, default=5.0)
    parser.add_argument("--hotspot-traffic", type=float, default=80.0)
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
