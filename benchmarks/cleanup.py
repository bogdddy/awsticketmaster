import argparse
import json
import os
import time
import urllib.request
import urllib.error
import base64
import psycopg2


def get_queue_depth(host, user, password, queue="tickets.buy"):
    url = f"http://{host}:15672/api/queues/%2F/{queue}"
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {credentials}")
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode())
        return data.get("messages", 0)
    except Exception:
        return None


def wait_for_drain(host, user, password, timeout=120, poll_interval=5):
    print("Waiting for queue to drain...")
    elapsed = 0
    while elapsed < timeout:
        depth = get_queue_depth(host, user, password)
        if depth is None:
            print("  Warning: cannot check queue depth, proceeding anyway")
            return True
        if depth == 0:
            print(f"  Queue empty after ~{elapsed}s")
            return True
        print(f"  Queue has {depth} messages, waiting {poll_interval}s...")
        time.sleep(poll_interval)
        elapsed += poll_interval
    print(f"  Timeout reached ({timeout}s). Queue still has messages.")
    return False


def purge_rabbitmq(host, user, password):
    queues = ["tickets.buy", "tickets.dlq"]
    for queue in queues:
        url = f"http://{host}:15672/api/queues/%2F/{queue}/contents"
        req = urllib.request.Request(url, method="DELETE")
        credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {credentials}")
        try:
            urllib.request.urlopen(req, timeout=5)
            print(f"RabbitMQ: purged queue {queue}")
        except urllib.error.HTTPError as e:
            print(f"RabbitMQ: failed to purge {queue} (HTTP {e.code})")
        except urllib.error.URLError as e:
            print(f"RabbitMQ: connection failed to {host}:15672 ({e.reason})")
            return
    depth = get_queue_depth(host, user, password)
    if depth and depth > 0:
        print(f"Warning: queue still has {depth} messages after purge")
    print("RabbitMQ: cleanup complete")


def kill_all_app_connections(conn, db, app_user="ticketapp"):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = %s
              AND pg_stat_activity.usename = %s
              AND pid <> pg_backend_pid()
        """, (db, app_user))
        killed = cur.rowcount
        conn.commit()
        if killed:
            print(f"Killed {killed} connections from user '{app_user}'")
        return killed


def verify_clean(conn):
    checks = {
        "results": "SELECT COUNT(*) FROM results",
        "processed": "SELECT COUNT(*) FROM processed",
        "seats not available": "SELECT COUNT(*) FROM seats WHERE status != 'available'",
        "inventory sold > 0": "SELECT COUNT(*) FROM inventory WHERE sold > 0",
    }
    dirty = []
    with conn.cursor() as cur:
        for label, query in checks.items():
            cur.execute(query)
            count = cur.fetchone()[0]
            if count > 0:
                dirty.append(f"{label}: {count}")
                print(f"  Verification FAILED: {label} -> {count} rows")
            else:
                print(f"  Verification OK: {label} -> 0 rows")
    return dirty


def reset_postgres(host, port, db, user, password, force=False):
    try:
        conn = psycopg2.connect(
            host=host, port=port, dbname=db, user=user, password=password,
            connect_timeout=5,
            options="-c statement_timeout=60000",
        )

        if force:
            kill_all_app_connections(conn, db, user)

        with conn.cursor() as cur:
            cur.execute("DELETE FROM results")
            cur.execute("DELETE FROM processed")
            cur.execute("""
                UPDATE seats
                SET status = 'available', request_id = NULL, reserved_at = NULL, sold_at = NULL
                WHERE status != 'available'
            """)
            cur.execute("UPDATE inventory SET sold = 0")
        conn.commit()

        dirty = verify_clean(conn)
        conn.close()

        if dirty:
            print(f"PostgreSQL: cleanup completed with {len(dirty)} verification failures")
            return False
        print("PostgreSQL: cleanup complete")
        return True
    except Exception as e:
        print(f"PostgreSQL: failed to cleanup: {e}")
        return False
def main():
    parser = argparse.ArgumentParser(description="Cleanup RabbitMQ and PostgreSQL before benchmarks")
    parser.add_argument("--rabbitmq-host", default=os.environ.get("RABBITMQ_HOST", "10.0.1.10"))
    parser.add_argument("--rabbitmq-user", default=os.environ.get("RABBITMQ_USER", "admin"))
    parser.add_argument("--rabbitmq-password", default=os.environ.get("RABBITMQ_PASS", "ddd"))
    parser.add_argument("--pg-host", default=os.environ.get("POSTGRES_HOST", "10.0.1.20"))
    parser.add_argument("--pg-port", type=int, default=int(os.environ.get("POSTGRES_PORT", "5432")))
    parser.add_argument("--pg-db", default=os.environ.get("POSTGRES_DB", "ticketdb"))
    parser.add_argument("--pg-user", default=os.environ.get("POSTGRES_USER", "ticketapp"))
    parser.add_argument("--pg-password", default=os.environ.get("POSTGRES_PASS", "ddd"))
    parser.add_argument("--drain", action="store_true",
                        help="Wait for RabbitMQ queue to empty before purging")
    parser.add_argument("--drain-timeout", type=int, default=120,
                        help="Max seconds to wait for queue drain (default: 120)")
    parser.add_argument("--force", action="store_true",
                        help="Kill ALL app connections to PostgreSQL before cleanup")
    parser.add_argument("--verify", action="store_true", default=True,
                        help="Verify tables are empty after cleanup (default: True)")
    parser.add_argument("--retry", type=int, default=0,
                        help="Retry cleanup this many times if verification fails (default: 0)")
    args = parser.parse_args()

    print("Cleaning environment...")
    
    if args.drain:
        wait_for_drain(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password,
                       timeout=args.drain_timeout)
    
    try:
        purge_rabbitmq(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password)
    except Exception as e:
        print(f"RabbitMQ critical error: {e}")
    
    success = reset_postgres(args.pg_host, args.pg_port, args.pg_db, args.pg_user,
                              args.pg_password, force=args.force)
    
    if not success and args.retry > 0:
        for attempt in range(1, args.retry + 1):
            print(f"Retry {attempt}/{args.retry}...")
            time.sleep(5)
            success = reset_postgres(args.pg_host, args.pg_port, args.pg_db, args.pg_user,
                                      args.pg_password, force=True)
            if success:
                break
    
    if success:
        print("Ready for next experiment")
    else:
        print("WARNING: Cleanup may be incomplete. Check manually.")
        exit(1)

if __name__ == "__main__":
    main()