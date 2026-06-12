import argparse
import os
import urllib.request
import urllib.error

import psycopg2


def purge_rabbitmq(host, user, password):
    queues = ["tickets.buy", "tickets.dlq"]
    for queue in queues:
        url = f"http://{host}:15672/api/queues/%2F/{queue}/contents"
        req = urllib.request.Request(url, method="DELETE")
        import base64
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
    print("RabbitMQ: cleanup complete")


def reset_postgres(host, port, db, user, password):
    conn = psycopg2.connect(host=host, port=port, dbname=db, user=user, password=password)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE processed CASCADE")
        cur.execute("""
            UPDATE seats
            SET status = 'available', request_id = NULL, reserved_at = NULL, sold_at = NULL
            WHERE status != 'available'
        """)
        cur.execute("UPDATE inventory SET sold = 0")
    conn.commit()
    conn.close()
    print("PostgreSQL: cleanup complete")


def main():
    parser = argparse.ArgumentParser(description="Cleanup RabbitMQ and PostgreSQL before experiments")
    parser.add_argument("--rabbitmq-host", default=os.environ.get("RABBITMQ_HOST", "10.0.1.10"))
    parser.add_argument("--rabbitmq-user", default=os.environ.get("RABBITMQ_USER", "admin"))
    parser.add_argument("--rabbitmq-password", default=os.environ.get("RABBITMQ_PASS", "ddd"))
    parser.add_argument("--pg-host", default=os.environ.get("POSTGRES_HOST", "10.0.1.20"))
    parser.add_argument("--pg-port", type=int, default=int(os.environ.get("POSTGRES_PORT", "5432")))
    parser.add_argument("--pg-db", default=os.environ.get("POSTGRES_DB", "ticketdb"))
    parser.add_argument("--pg-user", default=os.environ.get("POSTGRES_USER", "ticketapp"))
    parser.add_argument("--pg-password", default=os.environ.get("POSTGRES_PASS", "ddd"))
    args = parser.parse_args()

    print("Cleaning environment...")
    purge_rabbitmq(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_password)
    reset_postgres(args.pg_host, args.pg_port, args.pg_db, args.pg_user, args.pg_password)
    print("Ready for next experiment")


if __name__ == "__main__":
    main()
