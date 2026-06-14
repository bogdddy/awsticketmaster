import json
import math
import os
import time
import urllib.request
import base64
import boto3

RABBITMQ_HOST = os.environ["RABBITMQ_HOST"]
RABBITMQ_PORT = os.environ["RABBITMQ_PORT"]
RABBITMQ_USER = os.environ["RABBITMQ_USER"]
RABBITMQ_PASS = os.environ["RABBITMQ_PASS"]
ECS_CLUSTER   = os.environ["ECS_CLUSTER"]
ECS_SERVICE   = os.environ["ECS_SERVICE"]
PROJECT_NAME  = os.environ["PROJECT_NAME"]
TARGET_BACKLOG = float(os.environ["TARGET_BACKLOG"])
WORKER_MIN    = int(os.environ["WORKER_MIN"])
WORKER_MAX    = int(os.environ["WORKER_MAX"])

# Estimacion inicial de capacidad por worker (se afina experimentalmente en calibracion).
# Con 100ms de delay y overhead, un worker procesa ~10 msg/s en regimen secuencial.
CAPACITY_PER_WORKER = 10.0
# Cooldowns asimetricos: subida rapida (30s) para responder a picos, bajada lenta (90s)
# para evitar flapping (oscilacion del numero de workers).
SCALE_UP_COOLDOWN = 30
SCALE_DOWN_COOLDOWN = 90

NAMESPACE = f"{PROJECT_NAME}/autoscaling"

cloudwatch = boto3.client("cloudwatch")
ecs_client = boto3.client("ecs")

_last_scale_time = 0
_last_scale_direction = None
_prev_backlog = None
_prev_arrival_rate = None


def _api_get(path):
    url = f"http://{RABBITMQ_HOST}:{RABBITMQ_PORT}/api/{path}"
    credentials = f"{RABBITMQ_USER}:{RABBITMQ_PASS}"
    encoded = base64.b64encode(credentials.encode()).decode()
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {encoded}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"RabbitMQ API error ({path}): {e}")
        return None


def get_rabbitmq_backlog():
    data = _api_get("queues/%2F/tickets.buy")
    if data is None:
        return None
    return data.get("messages_ready", 0)


def get_rabbitmq_arrival_rate():
    data = _api_get("queues/%2F/tickets.buy")
    if data is None:
        return 0.0
    return (
        data.get("message_stats", {})
        .get("publish_details", {})
        .get("rate", 0.0)
    )


def get_worker_count():
    try:
        resp = ecs_client.describe_services(
            cluster=ECS_CLUSTER,
            services=[ECS_SERVICE],
        )
        service = resp["services"][0]
        return service["desiredCount"], service["runningCount"]
    except Exception as e:
        print(f"Error describing ECS service: {e}")
        return None, None


def put_metric(name, value, unit="Count", storage_resolution=None):
    metric = {"MetricName": name, "Value": value, "Unit": unit}
    if storage_resolution:
        metric["StorageResolution"] = storage_resolution
    cloudwatch.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[metric],
    )


def handler(event, context):
    global _last_scale_time, _last_scale_direction, _prev_backlog, _prev_arrival_rate

    records = event.get("Records", [])
    if not records:
        print("No SQS records, skipping")
        return {"status": "no_records"}

    now = time.time()
    backlog = get_rabbitmq_backlog()
    arrival_rate = get_rabbitmq_arrival_rate()
    desired_current, running = get_worker_count()

    if backlog is None or running is None:
        print("Failed to get metrics, skipping")
        return {"status": "error"}

    # Calculo de la derivada del backlog para escalado predictivo.
    # Si el backlog esta creciendo, necesitamos workers extras para compensar antes
    # de que el backlog se dispare.
    backlog_growth = 0
    if _prev_backlog is not None and _prev_arrival_rate is not None:
        backlog_growth = max(0, (backlog - _prev_backlog))
    _prev_backlog = backlog
    _prev_arrival_rate = arrival_rate

    backlog_per_worker = backlog / max(running, 1)

    # Formula de escalado: workers necesarios para cubrir:
    # 1) workers_for_rate: la tasa de llegada actual (N = ceil(lambda / C))
    # 2) workers_for_backlog: drenar el backlog acumulado en TARGET_BACKLOG segundos
    # 3) workers_for_growth: workers extra para absorber el crecimiento del backlog
    workers_for_rate = max(1, math.ceil(arrival_rate / CAPACITY_PER_WORKER))
    workers_for_backlog = int(backlog / max(TARGET_BACKLOG, 1))
    workers_for_growth = int(backlog_growth / max(CAPACITY_PER_WORKER, 1))

    # El deseado se recorta a los limites configurados (WORKER_MIN, WORKER_MAX)
    # para no saturar PostgreSQL ni incurrir en costes excesivos.
    desired = max(WORKER_MIN, min(WORKER_MAX,
                  workers_for_rate + workers_for_backlog + workers_for_growth))

    put_metric("Backlog", backlog)
    put_metric("ArrivalRate", arrival_rate, "Count/Second")
    put_metric("WorkerCount", running)
    put_metric("BacklogPerWorker", backlog_per_worker, storage_resolution=1)
    put_metric("BacklogGrowth", backlog_growth, "Count/Second")
    put_metric("DesiredWorkers", desired)

    elapsed = now - _last_scale_time
    scaled = False
    cooldown_remaining = 0

    if desired != desired_current:
        direction = "up" if desired > desired_current else "down"
        cooldown_needed = SCALE_UP_COOLDOWN if direction == "up" else SCALE_DOWN_COOLDOWN

        if elapsed >= cooldown_needed:
            print(f"Scaling {direction} from {desired_current} to {desired} workers "
                  f"(rate={arrival_rate:.1f}/s → need {workers_for_rate}, "
                  f"backlog={backlog}(+{backlog_growth}/s) → need {workers_for_backlog}+{workers_for_growth})")
            try:
                ecs_client.update_service(
                    cluster=ECS_CLUSTER,
                    service=ECS_SERVICE,
                    desiredCount=desired,
                )
                _last_scale_time = now
                _last_scale_direction = direction
                scaled = True
            except Exception as e:
                print(f"Error updating service desired count: {e}")
        else:
            cooldown_remaining = int(cooldown_needed - elapsed)
            print(f"Cooldown active for scale-{direction}: {cooldown_remaining}s remaining")

    put_metric("CooldownRemaining", cooldown_remaining)
    put_metric("Scaled", 1 if scaled else 0)

    print(f"backlog={backlog}(+{backlog_growth}/s), rate={arrival_rate:.1f}/s, "
          f"workers={running}, b/w={backlog_per_worker:.2f}, "
          f"desired={desired}, scaled={scaled}")

    return {
        "status": "ok",
        "backlog": backlog,
        "backlog_growth": backlog_growth,
        "arrival_rate": arrival_rate,
        "workers": running,
        "backlog_per_worker": backlog_per_worker,
        "desired": desired,
        "scaled": scaled,
    }
