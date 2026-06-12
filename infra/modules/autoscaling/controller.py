import json
import math
import os
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

CAPACITY_PER_WORKER = 10.0

NAMESPACE = f"{PROJECT_NAME}/autoscaling"

cloudwatch = boto3.client("cloudwatch")
ecs_client = boto3.client("ecs")


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


def put_metric(name, value, unit="Count"):
    cloudwatch.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[{"MetricName": name, "Value": value, "Unit": unit}],
    )


def handler(event, context):
    backlog = get_rabbitmq_backlog()
    arrival_rate = get_rabbitmq_arrival_rate()
    desired_current, running = get_worker_count()

    if backlog is None or running is None:
        print("Failed to get metrics, skipping")
        return {"status": "error"}

    backlog_per_worker = backlog / max(running, 1)

    workers_for_rate = max(1, math.ceil(arrival_rate / CAPACITY_PER_WORKER))
    workers_for_backlog = int(backlog / max(TARGET_BACKLOG, 1))
    desired = max(WORKER_MIN, min(WORKER_MAX, max(workers_for_rate, workers_for_backlog)))

    put_metric("Backlog", backlog)
    put_metric("ArrivalRate", arrival_rate, "Count/Second")
    put_metric("WorkerCount", running)
    put_metric("BacklogPerWorker", backlog_per_worker)

    if desired != desired_current:
        print(f"Scaling from {desired_current} to {desired} workers "
              f"(rate={arrival_rate:.1f}/s → need {workers_for_rate}, "
              f"backlog={backlog} → need {workers_for_backlog})")
        try:
            ecs_client.update_service(
                cluster=ECS_CLUSTER,
                service=ECS_SERVICE,
                desiredCount=desired,
            )
        except Exception as e:
            print(f"Error updating service desired count: {e}")

    print(f"backlog={backlog}, arrival_rate={arrival_rate:.1f}/s, "
          f"workers={running}, b/w={backlog_per_worker:.2f}, "
          f"desired={desired}")

    return {
        "status": "ok",
        "backlog": backlog,
        "arrival_rate": arrival_rate,
        "workers": running,
        "backlog_per_worker": backlog_per_worker,
        "desired": desired,
    }
