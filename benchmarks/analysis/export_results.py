"""
Export results from PostgreSQL to CSV files for analysis.
Usage:
    python export_results.py --host <host> --db <db> --user <user> --password <pass>
    python export_results.py --s3-bucket <bucket>   # also uploads to S3
"""
import argparse
import csv
import os
import sys

import psycopg2


def _build_queries(since=None, until=None):
    def _filter(col):
        clauses = []
        params = {}
        if since:
            clauses.append(f" AND {col} >= %(since)s")
            params["since"] = since
        if until:
            clauses.append(f" AND {col} < %(until)s")
            params["until"] = until
        return "".join(clauses), params

    res_filter, res_params = _filter("enqueue_ts")
    pro_filter, pro_params = _filter("processed_at")

    return {
        "results": (
            "SELECT * FROM results WHERE 1=1" + res_filter + " ORDER BY finish_ts",
            res_params,
        ),
        "processed": (
            "SELECT * FROM processed WHERE 1=1" + pro_filter + " ORDER BY processed_at",
            pro_params,
        ),
        "summary": (
            "SELECT"
            "  outcome, COUNT(*) AS count,"
            "  AVG(EXTRACT(EPOCH FROM (finish_ts - enqueue_ts)) * 1000) AS avg_latency_ms,"
            "  PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finish_ts - enqueue_ts))) * 1000 AS p50_ms,"
            "  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finish_ts - enqueue_ts))) * 1000 AS p95_ms,"
            "  PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finish_ts - enqueue_ts))) * 1000 AS p99_ms"
            " FROM results WHERE 1=1" + res_filter + " GROUP BY outcome",
            res_params,
        ),
        "throughput_by_minute": (
            "SELECT"
            "  date_trunc('minute', finish_ts) AS minute,"
            "  COUNT(*) AS requests,"
            "  COUNT(*) FILTER (WHERE outcome = 'sold') AS sold"
            " FROM results WHERE 1=1" + res_filter + " GROUP BY minute ORDER BY minute",
            res_params,
        ),
    }


def export_table(conn, name, query, params, output_dir):
    path = os.path.join(output_dir, f"{name}.csv")
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        colnames = [desc[0] for desc in cur.description]

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(colnames)
        writer.writerows(rows)

    print(f"Exported {len(rows)} rows -> {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="Export PostgreSQL results to CSV")
    parser.add_argument("--host", default=os.environ.get("POSTGRES_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("POSTGRES_PORT", "5432")))
    parser.add_argument("--db", default=os.environ.get("POSTGRES_DB", "ticketdb"))
    parser.add_argument("--user", default=os.environ.get("POSTGRES_USER", "ticketapp"))
    parser.add_argument("--password", default=os.environ.get("POSTGRES_PASS", "password"))
    parser.add_argument("--output-dir", default="./results")
    parser.add_argument("--since", help="Only export results after this timestamp (ISO8601)")
    parser.add_argument("--until", help="Only export results before this timestamp (ISO8601)")
    parser.add_argument("--s3-bucket", help="Upload CSVs to S3 bucket")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    conn = psycopg2.connect(
        host=args.host, port=args.port,
        dbname=args.db, user=args.user, password=args.password,
    )

    queries = _build_queries(since=args.since, until=args.until)
    exported = []
    for name, (query, params) in queries.items():
        path = export_table(conn, name, query, params, args.output_dir)
        exported.append(path)

    conn.close()

    if args.s3_bucket:
        import boto3
        s3 = boto3.client("s3")
        for path in exported:
            key = f"results/{os.path.basename(path)}"
            s3.upload_file(path, args.s3_bucket, key)
            print(f"Uploaded {path} -> s3://{args.s3_bucket}/{key}")

    print("Done. Run plot_results.py to generate plots.")


if __name__ == "__main__":
    main()
