#!/usr/bin/env python3
"""
Collect all benchmark results into a single JSON file for analysis.

Usage:
    python3 collect_results.py
    python3 collect_results.py --input-dir ./benchmark_results --output results.json
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime


def parse_csv(filepath):
    """Parse CSV file and return list of dicts."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        return list(reader)


def calculate_metrics(results_rows):
    """Calculate key metrics from results.csv rows."""
    if not results_rows:
        return {}
    
    sold = [r for r in results_rows if r.get('outcome') == 'sold']
    rejected = [r for r in results_rows if r.get('outcome') == 'rejected']
    sold_out = [r for r in results_rows if r.get('outcome') == 'sold_out']
    
    latencies = []
    for r in results_rows:
        try:
            enqueue = datetime.fromisoformat(r['enqueue_ts'])
            finish = datetime.fromisoformat(r['finish_ts'])
            latency_ms = (finish - enqueue).total_seconds() * 1000
            if latency_ms > 0:
                latencies.append(latency_ms)
        except (KeyError, ValueError):
            continue
    
    latencies.sort()
    
    metrics = {
        'total_requests': len(results_rows),
        'sold': len(sold),
        'rejected': len(rejected),
        'sold_out': len(sold_out),
        'success_rate': len(sold) / len(results_rows) * 100 if results_rows else 0,
    }
    
    if latencies:
        metrics['latency'] = {
            'min_ms': round(latencies[0], 2),
            'max_ms': round(latencies[-1], 2),
            'avg_ms': round(sum(latencies) / len(latencies), 2),
            'p50_ms': round(latencies[int(len(latencies) * 0.50)], 2),
            'p95_ms': round(latencies[int(len(latencies) * 0.95)], 2),
            'p99_ms': round(latencies[int(len(latencies) * 0.99)], 2),
        }
    
    if results_rows:
        try:
            timestamps = [datetime.fromisoformat(r['finish_ts']) for r in results_rows if 'finish_ts' in r]
            if timestamps:
                duration_s = (max(timestamps) - min(timestamps)).total_seconds()
                if duration_s > 0:
                    metrics['duration_s'] = round(duration_s, 2)
                    metrics['throughput_rps'] = round(len(sold) / duration_s, 2)
        except (KeyError, ValueError):
            pass
    
    return metrics


EXPERIMENT_NAMES = {"calibration", "speedup", "stress", "elasticity", "contention", "oversight", "fault_tolerance"}


def collect_benchmarks(input_dir):
    """Collect all benchmark results into structured dict."""
    benchmarks = {}
    run_timestamps = {}
    
    if not os.path.exists(input_dir):
        print(f"Error: {input_dir} not found")
        return benchmarks, run_timestamps
    
    # Soporta estructura plana (legacy): benchmark_results/<experiment>/<run>/
    # y anidada (nueva): benchmark_results/<timestamp>/<experiment>/<run>/
    entries = sorted(os.listdir(input_dir))
    
    # Detectar si es estructura nueva (timestamp) o plana (legacy)
    is_timestamped = any(
        os.path.isdir(os.path.join(input_dir, e))
        and os.path.isdir(os.path.join(input_dir, e, next(iter(EXPERIMENT_NAMES), "")))
        for e in entries
    ) if entries else False
    
    if is_timestamped:
        for timestamp in entries:
            ts_path = os.path.join(input_dir, timestamp)
            if not os.path.isdir(ts_path):
                continue
            _collect_experiments(ts_path, benchmarks, run_timestamps, timestamp)
    else:
        _collect_experiments(input_dir, benchmarks, run_timestamps)
    
    return benchmarks, run_timestamps


def _collect_experiments(base_path, benchmarks, run_timestamps, timestamp=None):
    for experiment in sorted(os.listdir(base_path)):
        exp_path = os.path.join(base_path, experiment)
        if not os.path.isdir(exp_path):
            continue
        
        if experiment not in EXPERIMENT_NAMES and timestamp is None:
            continue
        
        if experiment not in benchmarks:
            benchmarks[experiment] = {}
        
        for run in sorted(os.listdir(exp_path)):
            run_path = os.path.join(exp_path, run)
            if not os.path.isdir(run_path):
                continue
            
            results_csv = os.path.join(run_path, 'results.csv')
            summary_csv = os.path.join(run_path, 'summary.csv')
            throughput_csv = os.path.join(run_path, 'throughput_by_minute.csv')
            processed_csv = os.path.join(run_path, 'processed.csv')
            
            results_rows = parse_csv(results_csv)
            summary_rows = parse_csv(summary_csv)
            throughput_rows = parse_csv(throughput_csv)
            
            run_key = f"{run}@{timestamp}" if timestamp else run
            run_timestamps[run_key] = timestamp
            
            run_data = {
                'metrics': calculate_metrics(results_rows),
                'summary': summary_rows,
                'throughput_by_minute': throughput_rows,
                'files': {
                    'results': results_csv if os.path.exists(results_csv) else None,
                    'summary': summary_csv if os.path.exists(summary_csv) else None,
                    'throughput_by_minute': throughput_csv if os.path.exists(throughput_csv) else None,
                    'processed': processed_csv if os.path.exists(processed_csv) else None,
                }
            }
            
            benchmarks[experiment][run_key] = run_data


def main():
    parser = argparse.ArgumentParser(description="Collect benchmark results into JSON")
    parser.add_argument("--input-dir", default="./benchmark_results",
                        help="Directory containing benchmark results")
    parser.add_argument("--output", default="./benchmark_results/summary.json",
                        help="Output JSON file path")
    parser.add_argument("--pretty", action="store_true", default=True,
                        help="Pretty-print JSON output")
    args = parser.parse_args()
    
    print(f"Collecting benchmarks from {args.input_dir}...")
    benchmarks, run_timestamps = collect_benchmarks(args.input_dir)
    
    if not benchmarks:
        print("No benchmarks found")
        sys.exit(1)
    
    output_data = {
        'collected_at': datetime.now().isoformat(),
        'input_dir': args.input_dir,
        'experiments': benchmarks,
        'summary': {}
    }
    
    for exp_name, runs in benchmarks.items():
        total_requests = sum(r['metrics'].get('total_requests', 0) for r in runs.values())
        total_sold = sum(r['metrics'].get('sold', 0) for r in runs.values())
        run_list = []
        for run_key in runs:
            ts = run_timestamps.get(run_key)
            run_list.append({'name': run_key, 'timestamp': ts})
        output_data['summary'][exp_name] = {
            'num_runs': len(runs),
            'total_requests': total_requests,
            'total_sold': total_sold,
            'runs': run_list
        }
    
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    
    indent = 2 if args.pretty else None
    with open(args.output, 'w') as f:
        json.dump(output_data, f, indent=indent, default=str)
    
    timestamps_seen = set()
    print(f"\nCollected {len(benchmarks)} experiments:")
    for exp_name, runs in benchmarks.items():
        print(f"  {exp_name}: {len(runs)} runs")
        for run_key, run_data in runs.items():
            m = run_data['metrics']
            ts = run_timestamps.get(run_key, "")
            timestamps_seen.add(ts) if ts else None
            ts_label = f" @{ts}" if ts else ""
            if m:
                print(f"    {run_key}{ts_label}: {m.get('total_requests', 0)} requests, "
                      f"{m.get('sold', 0)} sold, "
                      f"{m.get('throughput_rps', 0)} req/s")
            else:
                summary = run_data.get('summary', [])
                if summary:
                    info = summary[0]
                    passed = info.get('passed', '?')
                    print(f"    {run_key}{ts_label}: {info.get('test', exp_name)} "
                          f"passed={passed} "
                          f"requests={info.get('total_requests', info.get('total_messages', '?'))}")
                else:
                    print(f"    {run_key}{ts_label}: (no metrics)")
    
    if timestamps_seen:
        print(f"\nRuns found across {len(timestamps_seen)} session(s): {', '.join(sorted(timestamps_seen, reverse=True))}")
    
    print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
