"""
Generate the 3 required validation plots for the final report.
Usage: python generate_final_plots.py
Output: plots/ directory with 3 PNGs
"""
import os
import sys
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent / "benchmark_results" / "best_becnhmark"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "plots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "figure.dpi": 150,
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 12,
    "legend.fontsize": 10,
})


def plot_throughput_vs_workers():
    """(a) Throughput vs Workers — speedup curve with ideal reference."""
    workers_exp = ["workers_1", "workers_2", "workers_4", "workers_8"]
    n_workers = [1, 2, 4, 8]
    throughputs = []

    for w in workers_exp:
        path = BENCH_DIR / "speedup" / w / "summary.csv"
        df = pd.read_csv(path)
        sold_row = df[df["outcome"] == "sold"].iloc[0]
        # Also read results.csv for total duration
        results_path = BENCH_DIR / "speedup" / w / "results.csv"
        rdf = pd.read_csv(results_path)
        rdf["finish_ts"] = pd.to_datetime(rdf["finish_ts"], format="ISO8601")
        rdf["enqueue_ts"] = pd.to_datetime(rdf["enqueue_ts"], format="ISO8601")
        duration = (rdf["finish_ts"].max() - rdf["enqueue_ts"].min()).total_seconds()
        sold = sold_row["count"]
        throughputs.append(sold / duration)

    t0 = throughputs[0]
    ideal = [t0 * w for w in n_workers]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.plot(n_workers, throughputs, marker="o", linestyle="-", linewidth=2,
            markersize=8, color="#1f77b4", label="Real")
    ax.plot(n_workers, ideal, linestyle="--", linewidth=1.5, color="#ff7f0e",
            alpha=0.7, label="Ideal (lineal)")

    for i, (x, y) in enumerate(zip(n_workers, throughputs)):
        eff = throughputs[i] / ideal[i] * 100
        ax.annotate(f"{y:.1f} rps\n({eff:.0f}%)", (x, y),
                    textcoords="offset points", xytext=(0, 14),
                    ha="center", fontsize=9, color="#1f77b4")

    ax.set_xlabel("Número de Workers")
    ax.set_ylabel("Throughput (requests/s)")
    ax.set_title("(a) Throughput vs Workers — Speedup")
    ax.set_xticks(n_workers)
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path = OUTPUT_DIR / "throughput_vs_workers.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def plot_backlog_vs_time():
    """(b) Backlog vs Time from elasticity run_1."""
    path = BENCH_DIR / "elasticity" / "run_1" / "results.csv"
    df = pd.read_csv(path)
    df["enqueue_ts"] = pd.to_datetime(df["enqueue_ts"], format="ISO8601")
    df["finish_ts"] = pd.to_datetime(df["finish_ts"], format="ISO8601")
    df = df.sort_values("enqueue_ts")

    start_time = df["enqueue_ts"].min()
    df["enqueue_sec"] = (df["enqueue_ts"] - start_time).dt.total_seconds()
    df["finish_sec"] = (df["finish_ts"] - start_time).dt.total_seconds()

    # Compute backlog at 5s intervals
    bins = int(df["finish_sec"].max() / 5) + 1
    enqueued = df.groupby(pd.cut(df["enqueue_sec"], bins=bins)).size().cumsum()
    finished = df.groupby(pd.cut(df["finish_sec"], bins=bins)).size().cumsum()

    time_pts = [5 * i for i in range(bins)]
    backlog = enqueued.values - finished.values

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.fill_between(time_pts, backlog, alpha=0.3, color="#d62728")
    ax.plot(time_pts, backlog, linewidth=1, color="#d62728")
    ax.axvline(x=780, color="gray", linestyle=":", alpha=0.7, label="Inicio pico (~780s)")
    ax.set_xlabel("Tiempo (segundos desde inicio del experimento)")
    ax.set_ylabel("Backlog (mensajes en cola)")
    ax.set_title("(b) Backlog vs Tiempo — Elasticidad (run_1)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path = OUTPUT_DIR / "backlog_vs_time.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def plot_latency_percentiles():
    """(c) Latency Percentiles — comparative bar chart p50/p95/p99 by experiment."""
    experiments = [
        ("Calibración\nrate_10", "calibration", "rate_10"),
        ("Calibración\nrate_50", "calibration", "rate_50"),
        ("Calibración\nrate_100", "calibration", "rate_100"),
        ("Stress\nmax_rate_80", "stress", "max_rate_80"),
        ("Elasticidad\nrun_1", "elasticity", "run_1"),
        ("Elasticidad\nrun_2", "elasticity", "run_2"),
    ]

    labels = []
    p50s = []
    p95s = []
    p99s = []

    for label, exp_type, exp_name in experiments:
        path = BENCH_DIR / exp_type / exp_name / "results.csv"
        df = pd.read_csv(path)
        df["enqueue_ts"] = pd.to_datetime(df["enqueue_ts"], format="ISO8601")
        df["finish_ts"] = pd.to_datetime(df["finish_ts"], format="ISO8601")
        df["latency_s"] = (df["finish_ts"] - df["enqueue_ts"]).dt.total_seconds()
        df = df[df["latency_s"] > 0]
        labels.append(label)
        p50s.append(df["latency_s"].quantile(0.50))
        p95s.append(df["latency_s"].quantile(0.95))
        p99s.append(df["latency_s"].quantile(0.99))

    x = range(len(labels))
    width = 0.25

    fig, ax = plt.subplots(figsize=(12, 6))
    bars1 = ax.bar([i - width for i in x], p50s, width, label="p50", color="#2ca02c")
    bars2 = ax.bar(x, p95s, width, label="p95", color="#ff7f0e")
    bars3 = ax.bar([i + width for i in x], p99s, width, label="p99", color="#d62728")

    ax.set_ylabel("Latencia (segundos)")
    ax.set_title("(c) Percentiles de Latencia por Experimento")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.legend()
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3, axis="y")

    for bar_group in [bars1, bars2, bars3]:
        for bar in bar_group:
            h = bar.get_height()
            if h > 0:
                ax.annotate(f"{h:.1f}s", xy=(bar.get_x() + bar.get_width() / 2, h),
                            xytext=(0, 3), textcoords="offset points",
                            ha="center", va="bottom", fontsize=7, rotation=90)

    fig.tight_layout()
    path = OUTPUT_DIR / "latency_percentiles.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def plot_calibration_detail():
    """Extra: latency distribution for calibration (showing regime change)."""
    experiments = [
        ("10 msg/s", "calibration", "rate_10"),
        ("50 msg/s", "calibration", "rate_50"),
        ("100 msg/s", "calibration", "rate_100"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(14, 4), sharey=False)

    for ax, (label, exp_type, exp_name) in zip(axes, experiments):
        path = BENCH_DIR / exp_type / exp_name / "results.csv"
        df = pd.read_csv(path)
        df["enqueue_ts"] = pd.to_datetime(df["enqueue_ts"], format="ISO8601")
        df["finish_ts"] = pd.to_datetime(df["finish_ts"], format="ISO8601")
        df["latency_s"] = (df["finish_ts"] - df["enqueue_ts"]).dt.total_seconds()
        df = df[df["latency_s"] > 0]

        sold = df[df["outcome"] == "sold"]["latency_s"]
        rejected = df[df["outcome"] == "rejected"]["latency_s"]

        ax.hist(sold, bins=50, alpha=0.6, label=f"sold (n={len(sold)})", color="#2ca02c")
        ax.hist(rejected, bins=50, alpha=0.6, label=f"rejected (n={len(rejected)})", color="#d62728")
        ax.set_xlabel("Latencia (s)")
        ax.set_ylabel("Frecuencia")
        ax.set_title(f"Carga = {label}")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)

    fig.suptitle("Distribución de Latencia en Calibración (1 worker)", fontsize=13)
    fig.tight_layout()
    path = OUTPUT_DIR / "calibration_latency_distribution.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def plot_contention_comparison():
    """Extra: contention uniform vs hotspot throughput."""
    experiments = [
        ("Uniforme", "contention", "uniform"),
        ("Hotspot 80/5", "contention", "hotspot_80_5"),
    ]

    labels = []
    throughputs = []
    success_rates = []

    for label, exp_type, exp_name in experiments:
        path = BENCH_DIR / exp_type / exp_name / "results.csv"
        df = pd.read_csv(path)
        df["finish_ts"] = pd.to_datetime(df["finish_ts"], format="ISO8601")
        df["enqueue_ts"] = pd.to_datetime(df["enqueue_ts"], format="ISO8601")
        duration = (df["finish_ts"].max() - df["enqueue_ts"].min()).total_seconds()
        sold = len(df[df["outcome"] == "sold"])
        total = len(df)
        labels.append(label)
        throughputs.append(sold / duration)
        success_rates.append(sold / total * 100)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    bars = ax1.bar(labels, throughputs, color=["#2ca02c", "#d62728"])
    for bar, val in zip(bars, throughputs):
        ax1.annotate(f"{val:.1f} rps", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                     ha="center", va="bottom", fontsize=10)
    ax1.set_ylabel("Throughput (rps)")
    ax1.set_title("Throughput")
    ax1.grid(True, alpha=0.3, axis="y")

    bars = ax2.bar(labels, success_rates, color=["#2ca02c", "#d62728"])
    for bar, val in zip(bars, success_rates):
        ax2.annotate(f"{val:.1f}%", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                     ha="center", va="bottom", fontsize=10)
    ax2.set_ylabel("Tasa de Éxito (%)")
    ax2.set_title("Tasa de Éxito")
    ax2.grid(True, alpha=0.3, axis="y")

    fig.suptitle("Comparación Contención: Uniforme vs Hotspot 80/5", fontsize=13)
    fig.tight_layout()
    path = OUTPUT_DIR / "contention_comparison.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def main():
    print("Generating validation plots...")
    print(f"Input data: {BENCH_DIR}")
    print(f"Output dir: {OUTPUT_DIR}\n")

    plot_throughput_vs_workers()
    plot_backlog_vs_time()
    plot_latency_percentiles()
    plot_calibration_detail()
    plot_contention_comparison()

    print(f"\nDone. {len(list(OUTPUT_DIR.glob('*.png')))} plots saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
