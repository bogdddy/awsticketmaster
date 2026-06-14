"""
Test de verificacion de No Oversell.

Requiere acceso a PostgreSQL en EC2 (10.0.1.20) con las tablas del proyecto.

Uso:
    python benchmarks/tests/test_oversell.py [--pg-host HOST] [--pg-user USER] [--pg-pass PASS]

El script:
  1. Crea un evento temporal con 100 asientos numbered
  2. Lanza N requests concurrentes (por defecto 500) con distribucion hotspot sobre esos 100 asientos
  3. Verifica que no haya oversell: COUNT(DISTINCT seat_id) = COUNT(*) y ambos <= 100
  4. Limpia los datos de prueba

Salida: PASS/FAIL con detalle de filas violadas si las hay.
Los resultados se guardan en CSV para integracion con benchmarks.
"""
import argparse
import csv
import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone

import psycopg2

TEST_EVENT_ID = 9999
NUM_SEATS = 100
NUM_REQUESTS = 500
HOTSPOT_SEATS_PCT = 10
HOTSPOT_TRAFFIC_PCT = 80
NUM_WORKER_THREADS = 8
_test_request_ids = []


def _write_summary_csv(output_dir, success, total_sold, total_requests, issues, elapsed):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "oversell_summary.csv")
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["test", "passed", "total_requests", "sold", "seats_available",
                         "elapsed_s", "issues"])
        writer.writerow([
            "oversell",
            "1" if success else "0",
            str(total_requests),
            str(total_sold),
            str(NUM_SEATS),
            f"{elapsed:.2f}",
            "; ".join(issues) if issues else "none",
        ])
    print(f"[RESULT] Summary saved: {path}")
    return path


def setup_test_db(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM seats WHERE event_id = %s", (TEST_EVENT_ID,))

        cur.execute(
            "INSERT INTO events (event_id, mode, capacity) VALUES (%s, 'numbered', %s)"
            " ON CONFLICT (event_id) DO UPDATE SET capacity = %s",
            (TEST_EVENT_ID, NUM_SEATS, NUM_SEATS),
        )

        for seat_id in range(1, NUM_SEATS + 1):
            cur.execute(
                "INSERT INTO seats (seat_id, event_id, status) VALUES (%s, %s, 'available')"
                " ON CONFLICT (event_id, seat_id) DO UPDATE SET status = 'available'",
                (seat_id, TEST_EVENT_ID),
            )

        cur.execute("UPDATE inventory SET sold = 0 WHERE event_id = %s", (TEST_EVENT_ID,))

    conn.commit()
    print(f"[SETUP] Evento {TEST_EVENT_ID} con {NUM_SEATS} asientos listo.")


def verify_no_oversell(conn):
    issues = []

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM seats WHERE event_id = %s AND status = 'sold'",
            (TEST_EVENT_ID,),
        )
        total_sold = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(DISTINCT seat_id) FROM seats WHERE event_id = %s AND status = 'sold'",
            (TEST_EVENT_ID,),
        )
        distinct_sold = cur.fetchone()[0]

        if total_sold != distinct_sold:
            issues.append(
                f"OVERSOLD: {total_sold} filas vs {distinct_sold} asientos unicos"
            )

        if total_sold > NUM_SEATS:
            issues.append(
                f"OVERSOLD: {total_sold} vendidos > {NUM_SEATS} disponibles"
            )

        cur.execute(
            """SELECT seat_id, COUNT(*) as cnt
               FROM seats WHERE event_id = %s AND status = 'sold'
               GROUP BY seat_id HAVING COUNT(*) > 1""",
            (TEST_EVENT_ID,),
        )
        duplicates = cur.fetchall()
        if duplicates:
            for seat_id, cnt in duplicates:
                issues.append(f"Asiento duplicado: seat_id={seat_id} aparece {cnt} veces")

        cur.execute(
            """SELECT outcome, COUNT(*) FROM results
               WHERE worker_id = 'test-worker'
               GROUP BY outcome ORDER BY outcome"""
        )
        print("\n[DISTRIBUCION DE RESULTADOS]:")
        for outcome, count in cur.fetchall():
            print(f"  {outcome}: {count}")
        print(f"  Total vendidos: {total_sold}/{NUM_SEATS}")

    return issues, total_sold


def sell_seat(conn, event_id, seat_id, request_id):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE seats
               SET status = 'sold', request_id = %s, sold_at = NOW()
               WHERE event_id = %s AND seat_id = %s AND status = 'available'""",
            (request_id, event_id, seat_id),
        )
        return cur.rowcount > 0


def worker_thread(pg_host, pg_port, pg_user, pg_pass, pg_db, requests_list, results_list):
    conn = psycopg2.connect(
        host=pg_host, port=pg_port, dbname=pg_db, user=pg_user, password=pg_pass,
    )

    for req_data in requests_list:
        request_id = req_data["request_id"]
        seat_id = req_data["seat_id"]

        try:
            time.sleep(0.1)
            sold = sell_seat(conn, TEST_EVENT_ID, seat_id, request_id)
            conn.commit()
            outcome = "sold" if sold else "rejected"

            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO processed (request_id, result)
                       VALUES (%s, %s) ON CONFLICT (request_id) DO NOTHING""",
                    (request_id, outcome),
                )
                cur.execute(
                    """INSERT INTO results (request_id, enqueue_ts, start_ts, finish_ts, outcome, worker_id)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (request_id) DO NOTHING""",
                    (request_id, datetime.now(timezone.utc), datetime.now(timezone.utc),
                     datetime.now(timezone.utc), outcome, "test-worker"),
                )
            conn.commit()
            results_list.append((request_id, outcome))
        except Exception as e:
            conn.rollback()
            print(f"[WORKER] Error: {e}")

    conn.close()


def run_test(pg_host, pg_port, pg_user, pg_pass, pg_db, output_dir):
    conn = psycopg2.connect(
        host=pg_host, port=pg_port, dbname=pg_db, user=pg_user, password=pg_pass,
    )

    setup_test_db(conn)
    conn.close()

    hot_seats_count = max(1, int(NUM_SEATS * HOTSPOT_SEATS_PCT / 100))
    hot_seats = set(random.sample(range(1, NUM_SEATS + 1), hot_seats_count))
    hot_seats_list = list(hot_seats)

    all_requests = []
    for _ in range(NUM_REQUESTS):
        if random.random() * 100 < HOTSPOT_TRAFFIC_PCT and hot_seats_list:
            seat_id = random.choice(hot_seats_list)
        else:
            seat_id = random.randint(1, NUM_SEATS)
            while seat_id in hot_seats:
                seat_id = random.randint(1, NUM_SEATS)
        rid = str(uuid.uuid4())
        _test_request_ids.append(rid)
        all_requests.append({
            "request_id": rid,
            "seat_id": seat_id,
        })

    chunk_size = max(1, len(all_requests) // NUM_WORKER_THREADS)
    chunks = [all_requests[i:i + chunk_size] for i in range(0, len(all_requests), chunk_size)]

    threads = []
    results_list = []
    results_lock = threading.Lock()

    def worker_wrapper(req_chunk):
        local_results = []
        worker_thread(pg_host, pg_port, pg_user, pg_pass, pg_db, req_chunk, local_results)
        with results_lock:
            results_list.extend(local_results)

    print(f"\n[TEST] Lanzando {NUM_REQUESTS} requests con {NUM_WORKER_THREADS} hilos...")
    print(f"[TEST] Hotspot: {HOTSPOT_TRAFFIC_PCT}% trafico sobre {hot_seats_count} asientos ({HOTSPOT_SEATS_PCT}%)")

    start = time.time()
    for chunk in chunks:
        if chunk:
            t = threading.Thread(target=worker_wrapper, args=(chunk,))
            t.start()
            threads.append(t)

    for t in threads:
        t.join()
    elapsed = time.time() - start

    sold_count = sum(1 for _, o in results_list if o == "sold")
    print(f"\n[TEST] Completado en {elapsed:.2f}s")
    print(f"[TEST] Sold: {sold_count}, Rejected: {len(results_list) - sold_count}")

    conn = psycopg2.connect(
        host=pg_host, port=pg_port, dbname=pg_db, user=pg_user, password=pg_pass,
    )
    issues, total_sold = verify_no_oversell(conn)

    _write_summary_csv(output_dir, len(issues) == 0, total_sold, NUM_REQUESTS, issues, elapsed)

    with conn.cursor() as cur:
        if _test_request_ids:
            cur.execute("DELETE FROM results WHERE request_id = ANY(%s)", (_test_request_ids,))
            cur.execute("DELETE FROM processed WHERE request_id = ANY(%s)", (_test_request_ids,))
        cur.execute("DELETE FROM seats WHERE event_id = %s", (TEST_EVENT_ID,))
        cur.execute("DELETE FROM inventory WHERE event_id = %s", (TEST_EVENT_ID,))
        cur.execute("DELETE FROM events WHERE event_id = %s", (TEST_EVENT_ID,))
    conn.commit()
    conn.close()

    if issues:
        print("\n[FAIL] Verificacion de oversell NO superada:")
        for issue in issues:
            print(f"  - {issue}")
        return False
    else:
        print("\n[PASS] No oversell detectado. La consistencia fuerte funciona correctamente.")
        return True


def main():
    parser = argparse.ArgumentParser(description="Test de verificacion de No Oversell")
    parser.add_argument("--pg-host", default="10.0.1.20")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-user", default="ticketapp")
    parser.add_argument("--pg-pass", default="ddd")
    parser.add_argument("--pg-db", default="ticketdb")
    parser.add_argument("--seats", type=int, default=100, help="Numero de asientos en el pool de prueba")
    parser.add_argument("--requests", type=int, default=500, help="Numero total de requests a lanzar")
    parser.add_argument("--threads", type=int, default=8, help="Numero de hilos workers concurrentes")
    parser.add_argument("--output-dir", default=None,
                        help="Directorio para guardar resultados CSV (default: benchmarks/tests/results/)")
    args = parser.parse_args()

    global NUM_SEATS, NUM_REQUESTS, NUM_WORKER_THREADS
    NUM_SEATS = args.seats
    NUM_REQUESTS = args.requests
    NUM_WORKER_THREADS = args.threads

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = args.output_dir or os.path.join(script_dir, "results")
    success = run_test(args.pg_host, args.pg_port, args.pg_user, args.pg_pass, args.pg_db, output_dir)
    exit(0 if success else 1)


if __name__ == "__main__":
    main()
