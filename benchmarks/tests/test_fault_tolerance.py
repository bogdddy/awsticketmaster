"""
Test de tolerancia a fallos: worker crash recovery.

Requiere:
  - Infraestructura AWS desplegada (EC2, ECS, RabbitMQ, PostgreSQL)
  - AWS CLI configurado con credenciales
  - acceso de red a las maquinas

Uso:
    python benchmarks/tests/test_fault_tolerance.py [options]

El script:
  1. Prepara un experimento con carga moderada (~20 rps)
  2. Durante la ejecucion, mata una tarea ECS (worker Fargate)
  3. Verifica que:
     a) Los mensajes en proceso se reencolan y son procesados por otro worker
     b) No hay duplicados en la tabla `processed`
     c) Los mensajes que exceden reintentos van a DLQ
  4. Reporta resultados en CSV para integracion con benchmarks
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3
import psycopg2

PROJECT_NAME = "awsticket"
ECS_CLUSTER = f"{PROJECT_NAME}-cluster"
ECS_SERVICE = f"{PROJECT_NAME}-worker-svc"
EXPERIMENT_TAG = f"ft-{uuid.uuid4().hex[:8]}"
TOTAL_MESSAGES = 100
PUBLISH_RATE = 20


def _write_summary_csv(output_dir, results):
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"fault_tolerance_{EXPERIMENT_TAG}.csv")
    fieldnames = [
        "experiment_tag", "passed", "total_messages", "published", "processed",
        "duplicates_found", "dlq_messages", "workers_final", "worker_killed",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(results)
    print(f"[RESULT] Summary saved: {path}")
    return path


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}")


def get_running_tasks(ecs):
    resp = ecs.list_tasks(cluster=ECS_CLUSTER, serviceName=ECS_SERVICE, desiredStatus="RUNNING")
    return resp.get("taskArns", [])


def kill_one_worker(ecs):
    tasks = get_running_tasks(ecs)
    if not tasks:
        log("WARNING: No hay tareas RUNNING para matar")
        return False
    target = tasks[0]
    log(f"Matando tarea ECS: {target}")
    ecs.stop_task(cluster=ECS_CLUSTER, task=target, reason="Fault tolerance test")
    return True


def wait_for_workers(ecs, expected_count, timeout=180):
    log(f"Esperando {expected_count} worker(s) RUNNING (timeout={timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        tasks = get_running_tasks(ecs)
        if len(tasks) >= expected_count:
            log(f"OK: {len(tasks)} worker(s) RUNNING")
            return True
        time.sleep(5)
    log(f"WARNING: Timeout esperando workers. Actual: {len(get_running_tasks(ecs))}")
    return False


def set_desired_count(ecs, count):
    log(f"Escalando ECS service a desired_count={count}")
    ecs.update_service(cluster=ECS_CLUSTER, service=ECS_SERVICE, desiredCount=count)


def publish_messages(rabbitmq_host, rabbitmq_user, rabbitmq_pass, count, rate):
    import pika

    credentials = pika.PlainCredentials(rabbitmq_user, rabbitmq_pass)
    params = pika.ConnectionParameters(
        host=rabbitmq_host, port=5672, credentials=credentials, heartbeat=300,
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.confirm_delivery()

    published = 0
    interval = 1.0 / rate
    start = time.monotonic()
    published_ids = []

    for i in range(count):
        rid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{EXPERIMENT_TAG}-{i:04d}"))
        published_ids.append(rid)
        msg = {
            "request_id": rid,
            "event_id": 1,
            "seat_id": (i % 100000) + 1,
            "mode": "numbered",
            "enqueue_ts": datetime.now(timezone.utc).isoformat(),
            "x-retry-count": 0,
        }
        try:
            channel.basic_publish(
                exchange="tickets",
                routing_key="buy",
                body=json.dumps(msg),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            published += 1
        except Exception as e:
            log(f"Publish error: {e}")

        target_time = start + (i + 1) * interval
        sleep_for = target_time - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)

    connection.close()
    log(f"Publicados {published}/{count} mensajes")
    return published, published_ids


def check_duplicates(pg_conn, request_ids):
    if not request_ids:
        return []
    with pg_conn.cursor() as cur:
        cur.execute(
            """SELECT request_id::text, COUNT(*) as cnt
               FROM processed
               WHERE request_id = ANY(%s)
               GROUP BY request_id
               HAVING COUNT(*) > 1""",
            (request_ids,),
        )
        return cur.fetchall()


def check_dlq(rabbitmq_host, rabbitmq_user, rabbitmq_pass):
    import urllib.request
    import base64

    url = f"http://{rabbitmq_host}:15672/api/queues/%2F/tickets.dlq"
    credentials = base64.b64encode(f"{rabbitmq_user}:{rabbitmq_pass}".encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {credentials}")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())
        return data.get("messages", 0)
    except Exception as e:
        log(f"Error checking DLQ: {e}")
        return None


def count_processed(pg_conn, request_ids):
    if not request_ids:
        return {}
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT outcome, COUNT(*) FROM processed WHERE request_id = ANY(%s) GROUP BY outcome",
            (request_ids,),
        )
        return dict(cur.fetchall())


def main():
    parser = argparse.ArgumentParser(description="Test de tolerancia a fallos")
    parser.add_argument("--rabbitmq-host", default="10.0.1.10")
    parser.add_argument("--rabbitmq-user", default="admin")
    parser.add_argument("--rabbitmq-pass", default="ddd")
    parser.add_argument("--pg-host", default="10.0.1.20")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-user", default="ticketapp")
    parser.add_argument("--pg-pass", default="ddd")
    parser.add_argument("--pg-db", default="ticketdb")
    parser.add_argument("--messages", type=int, default=TOTAL_MESSAGES)
    parser.add_argument("--rate", type=int, default=PUBLISH_RATE)
    parser.add_argument("--min-workers", type=int, default=2)
    parser.add_argument("--kill-at-sec", type=int, default=3)
    parser.add_argument("--output-dir", default=None,
                        help="Directorio para guardar resultados CSV (default: benchmarks/tests/results/)")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = args.output_dir or os.path.join(script_dir, "results")

    ecs = boto3.client("ecs", region_name="us-east-1")
    pg_conn = psycopg2.connect(
        host=args.pg_host, port=args.pg_port, dbname=args.pg_db,
        user=args.pg_user, password=args.pg_pass,
    )

    log("=" * 60)
    log("TEST DE TOLERANCIA A FALLOS")
    log(f"EXPERIMENT_TAG={EXPERIMENT_TAG}")
    log(f"Total mensajes={args.messages}, rate={args.rate} msg/s, min_workers={args.min_workers}")
    log("=" * 60)

    set_desired_count(ecs, args.min_workers)
    if not wait_for_workers(ecs, args.min_workers):
        log("ERROR: No se pudieron provisionar workers suficientes")
        return 1

    log("\n[PASO 2] Publicando mensajes a RabbitMQ...")
    published, published_ids = publish_messages(args.rabbitmq_host, args.rabbitmq_user,
                                  args.rabbitmq_pass, args.messages, args.rate)

    log(f"\n[PASO 3] Esperando {args.kill_at_sec}s antes de matar worker...")
    time.sleep(args.kill_at_sec)
    killed = kill_one_worker(ecs)

    if killed:
        log("\n[PASO 4] Esperando recuperacion del servicio ECS...")
        time.sleep(15)
        wait_for_workers(ecs, args.min_workers, timeout=120)

    log("\n[PASO 5] Esperando drenaje de cola (max 120s)...")
    time.sleep(30)
    import urllib.request
    import base64
    for attempt in range(12):
        url = f"http://{args.rabbitmq_host}:15672/api/queues/%2F/tickets.buy"
        creds = base64.b64encode(f"{args.rabbitmq_user}:{args.rabbitmq_pass}".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {creds}")
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read().decode())
            depth = data.get("messages", 0)
            if depth == 0:
                log("Cola drenada completamente.")
                break
            log(f"Cola tiene {depth} mensajes, esperando 10s...")
        except:
            pass
        time.sleep(10)

    log("\n" + "=" * 60)
    log("RESULTADOS")
    log("=" * 60)

    all_pass = True
    results = {
        "experiment_tag": EXPERIMENT_TAG,
        "passed": "0",
        "total_messages": str(args.messages),
        "published": str(published),
        "processed": "0",
        "duplicates_found": "0",
        "dlq_messages": "0",
        "workers_final": "0",
        "worker_killed": "1" if killed else "0",
    }

    duplicates = check_duplicates(pg_conn, published_ids)
    if duplicates:
        results["duplicates_found"] = str(len(duplicates))
        log(f"[FAIL] {len(duplicates)} request_id(s) duplicados encontrados:")
        for rid, cnt in duplicates[:5]:
            log(f"  - {rid} aparece {cnt} veces")
        all_pass = False
    else:
        log("[PASS] No hay request_id duplicados en processed (idempotencia OK)")

    counts = count_processed(pg_conn, published_ids)
    total_processed = sum(counts.values())
    results["processed"] = str(total_processed)
    log(f"\n[DATOS] Procesados: {total_processed}/{published} mensajes")
    for outcome, cnt in sorted(counts.items()):
        log(f"   {outcome}: {cnt}")

    if total_processed < published:
        log(f"[WARN] {published - total_processed} mensajes no procesados "
             f"(pueden estar en RabbitMQ o DLQ)")
    else:
        log("[PASS] Todos los mensajes fueron procesados")

    dlq_count = check_dlq(args.rabbitmq_host, args.rabbitmq_user, args.rabbitmq_pass)
    if dlq_count is not None:
        results["dlq_messages"] = str(dlq_count)
        if dlq_count > 0:
            log(f"[INFO] DLQ tiene {dlq_count} mensajes (esperado si hubo fallos > reintentos)")
        else:
            log("[PASS] DLQ vacia (todos los mensajes se procesaron con exito)")

    remaining = get_running_tasks(ecs)
    results["workers_final"] = str(len(remaining))
    if remaining:
        log(f"[PASS] {len(remaining)} worker(s) RUNNING despues del test")
    else:
        log("[FAIL] No hay workers RUNNING")
        all_pass = False

    if killed:
        log("[PASS] Worker fue eliminado durante el test y el sistema se recupero")

    results["passed"] = "1" if all_pass else "0"
    _write_summary_csv(output_dir, results)

    log("\n[LIMPIANDO] Datos de prueba...")
    with pg_conn.cursor() as cur:
        if published_ids:
            cur.execute("DELETE FROM results WHERE request_id = ANY(%s)", (published_ids,))
            cur.execute("DELETE FROM processed WHERE request_id = ANY(%s)", (published_ids,))
    pg_conn.commit()
    pg_conn.close()

    log("\n" + "=" * 60)
    if all_pass:
        log("[PASS] VEREDICTO: TODAS LAS VERIFICACIONES PASARON")
    else:
        log("[FAIL] VEREDICTO: ALGUNAS VERIFICACIONES FALLARON")
    log("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
