"""
Generate the 3 required plots from exported CSV data:
  (a) Throughput vs Workers (speedup)
  (b) Backlog vs Time
  (c) Latency Percentiles (p50/p95/p99 over time)

Usage:
    python plot_results.py --input-dir ./results --output-dir ./plots
"""
import argparse
import os
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot_throughput_vs_workers(results_csv, summary_csv, output_dir):
    """(a) Throughput vs Workers — speedup curve"""
    if summary_csv and os.path.exists(summary_csv):
        summary = pd.read_csv(summary_csv)
        if "workers" in summary.columns and "throughput" in summary.columns:
            plot_throughput_from_summary(summary, output_dir)
            return

    df = pd.read_csv(results_csv)
    df = df[df["outcome"].isin(["sold", "rejected", "sold_out"])].copy()
    if df.empty:
        print("Skipping throughput vs workers: empty data")
        return

    df["finish_ts"] = pd.to_datetime(df["finish_ts"], format='ISO8601')
    df["enqueue_ts"] = pd.to_datetime(df["enqueue_ts"], format='ISO8601')
    df["finish_minute"] = df["finish_ts"].dt.floor("min")
    throughput_by_minute = df.groupby("finish_minute").size() / 60.0

    if len(throughput_by_minute) < 2:
        print("Skipping throughput vs workers: need multiple time windows (run benchmarks at different worker counts)")
        return

    fig, ax = plt.subplots(figsize=(10, 6))
    x = range(1, len(throughput_by_minute) + 1)
    ax.plot(x, throughput_by_minute.values, marker="o", linestyle="-")
    ax.set_xlabel("Experiment Run (varying workers each run)")
    ax.set_ylabel("Throughput (requests/s)")
    ax.set_title("(a) Throughput vs Workers (Speedup)")
    ax.grid(True, alpha=0.3)
    if len(throughput_by_minute) > 0:
        first_tp = throughput_by_minute.iloc[0]
        ideal = [first_tp * (i + 1) for i in range(len(throughput_by_minute))]
        ax.plot(x, ideal, linestyle="--", alpha=0.5, label="Linear (ideal)")
        ax.legend()

    path = os.path.join(output_dir, "throughput_vs_workers.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def plot_throughput_from_summary(summary_df, output_dir):
    fig, ax = plt.subplots(figsize=(10, 6))
    x = summary_df["workers"]
    y = summary_df["throughput"]
    ax.plot(x, y, marker="o", linestyle="-")
    ax.set_xlabel("Number of Workers")
    ax.set_ylabel("Throughput (requests/s)")
    ax.set_title("(a) Throughput vs Workers (Speedup)")
    ax.grid(True, alpha=0.3)
    if len(y) > 0:
        first = y.iloc[0]
        ideal = [first * (w / x.iloc[0]) for w in x]
        ax.plot(x, ideal, linestyle="--", alpha=0.5, label="Linear (ideal)")
        ax.legend()
    path = os.path.join(output_dir, "throughput_vs_workers.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def plot_backlog_vs_time(results_csv, output_dir):
    """(b) Backlog vs Time — computed from enqueue_ts and finish_ts"""
    df = pd.read_csv(results_csv)
    if df.empty:
        print("Skipping backlog plot: empty results")
        return

    df["finish_ts"] = pd.to_datetime(df["finish_ts"], format='ISO8601')
    df["enqueue_ts"] = pd.to_datetime(df["enqueue_ts"], format='ISO8601')
    df = df.sort_values("enqueue_ts")

    enqueued = df.groupby(df["enqueue_ts"].dt.floor("5s")).size().cumsum()
    finished = df.groupby(df["finish_ts"].dt.floor("5s")).size().cumsum()

    timeline = pd.concat([
        pd.DataFrame({"time": enqueued.index, "enqueued_cum": enqueued.values}),
        pd.DataFrame({"time": finished.index, "finished_cum": finished.values}),
    ]).sort_values("time").ffill().fillna(0)

    if "enqueued_cum" not in timeline.columns:
        timeline["enqueued_cum"] = 0
    if "finished_cum" not in timeline.columns:
        timeline["finished_cum"] = 0

    timeline["backlog"] = timeline["enqueued_cum"] - timeline["finished_cum"]

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(timeline["time"], timeline["backlog"], linewidth=0.8)
    ax.set_xlabel("Time")
    ax.set_ylabel("Backlog (messages)")
    ax.set_title("(b) Backlog vs Time")
    ax.grid(True, alpha=0.3)

    path = os.path.join(output_dir, "backlog_vs_time.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def plot_latency_percentiles(results_csv, output_dir):
    """(c) Latency Percentiles (p50/p95/p99) over time"""
    df = pd.read_csv(results_csv)
    df["finish_ts"] = pd.to_datetime(df["finish_ts"], format='ISO8601')
    df["enqueue_ts"] = pd.to_datetime(df["enqueue_ts"], format='ISO8601')
    df["latency_ms"] = (df["finish_ts"] - df["enqueue_ts"]).dt.total_seconds() * 1000
    df = df[df["latency_ms"] > 0].copy()

    if df.empty:
        print("Skipping latency plot: no valid latency data")
        return

    df["minute"] = df["finish_ts"].dt.floor("1min")
    grouped = df.groupby("minute")["latency_ms"].agg([
        ("p50", lambda x: x.quantile(0.50)),
        ("p95", lambda x: x.quantile(0.95)),
        ("p99", lambda x: x.quantile(0.99)),
    ])

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(grouped.index, grouped["p50"], label="p50", linewidth=1)
    ax.plot(grouped.index, grouped["p95"], label="p95", linewidth=1)
    ax.plot(grouped.index, grouped["p99"], label="p99", linewidth=1)
    ax.set_xlabel("Time")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("(c) Latency Percentiles (p50/p95/p99)")
    ax.legend()
    ax.grid(True, alpha=0.3)

    path = os.path.join(output_dir, "latency_percentiles.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {path}")


def main():
    parser = argparse.ArgumentParser(description="Generate plots from exported CSV data")
    parser.add_argument("--input-dir", default="./results")
    parser.add_argument("--output-dir", default="./plots")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    results_csv = os.path.join(args.input_dir, "results.csv")
    summary_csv = os.path.join(args.input_dir, "summary.csv")

    if not os.path.exists(results_csv):
        print(f"Error: {results_csv} not found. Run export_results.py first.")
        sys.exit(1)

    plot_throughput_vs_workers(results_csv, summary_csv, args.output_dir)
    plot_backlog_vs_time(results_csv, args.output_dir)
    plot_latency_percentiles(results_csv, args.output_dir)

    print(f"\nAll plots saved to {args.output_dir}/")
    for f in sorted(os.listdir(args.output_dir)):
        print(f"  {f}")


if __name__ == "__main__":
    main()
