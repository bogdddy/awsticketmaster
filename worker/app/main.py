import json
import os
import time
import signal
import logging
import threading
from datetime import datetime, timezone

import boto3
import pika

from app.config import Config
from app.db import Database, check_idempotent, mark_processed, sell_seat, decrement_inventory
from app.models import BuyRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("worker")

db = Database()
running = True
sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def signal_handler(sig, frame):
    global running
    log.info("Shutting down...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def sqs_scaling_publisher():
    if not Config.sqs_queue_url:
        log.warning("SQS_QUEUE_URL not set, scaling trigger disabled")
        return
    while running:
        try:
            sqs.send_message(
                QueueUrl=Config.sqs_queue_url,
                MessageBody=json.dumps({"worker_id": Config.worker_id, "ts": time.time()}),
            )
        except Exception as e:
            log.error("SQS send error: %s", e)
        for _ in range(Config.sqs_scaling_interval):
            if not running:
                break
            time.sleep(1)


def process_message(ch, method, properties, body):
    start_ts = datetime.now(timezone.utc)

    try:
        data = json.loads(body)
        req = BuyRequest.from_dict(data)
    except Exception as e:
        log.error("Failed to parse message: %s", e)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    conn = db.get_conn()
    try:
        existing = check_idempotent(conn, str(req.request_id))
        if existing:
            log.info("Duplicate request_id=%s result=%s", req.request_id, existing)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            return

        if req.mode == "numbered":
            result = process_numbered(req)
        elif req.mode == "unnumbered":
            result = process_unnumbered(req)
        else:
            log.error("Unknown mode=%s", req.mode)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        finish_ts = datetime.now(timezone.utc)

        conn2 = db.get_conn()
        try:
            mark_processed(
                conn2, str(req.request_id), result,
                enqueue_ts=req.enqueue_ts,
                start_ts=start_ts,
                finish_ts=finish_ts,
                worker_id=Config.worker_id,
            )
        finally:
            db.put_conn(conn2)

        log.info("request_id=%s result=%s", req.request_id, result)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        log.error("Error processing request_id=%s: %s", req.request_id, e)
        try:
            conn.rollback()
        except Exception:
            pass
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    finally:
        try:
            db.put_conn(conn)
        except Exception:
            pass


def process_numbered(req):
    if req.seat_id is None:
        return "error"

    time.sleep(Config.payment_delay_ms / 1000.0)

    conn = db.get_conn()
    try:
        sold = sell_seat(conn, req.event_id, req.seat_id, str(req.request_id))
        conn.commit()
        return "sold" if sold > 0 else "rejected"
    finally:
        db.put_conn(conn)


def process_unnumbered(req):
    time.sleep(Config.payment_delay_ms / 1000.0)

    conn = db.get_conn()
    try:
        updated = decrement_inventory(conn, req.event_id)
        if updated == 0:
            conn.commit()
            return "sold_out"
        conn.commit()
    finally:
        db.put_conn(conn)

    return "sold"


def main():
    log.info(
        "Worker starting: id=%s rabbitmq=%s:%s queue=%s postgres=%s:%s/%s",
        Config.worker_id,
        Config.rabbitmq_host, Config.rabbitmq_port, Config.rabbitmq_queue,
        Config.postgres_host, Config.postgres_port, Config.postgres_db,
    )

    t = threading.Thread(target=sqs_scaling_publisher, daemon=True)
    t.start()
    log.info("SQS scaling publisher started (queue=%s, interval=%ss)", Config.sqs_queue_url, Config.sqs_scaling_interval)

    credentials = pika.PlainCredentials(Config.rabbitmq_user, Config.rabbitmq_pass)
    params = pika.ConnectionParameters(
        host=Config.rabbitmq_host,
        port=Config.rabbitmq_port,
        credentials=credentials,
        heartbeat=300,
        blocked_connection_timeout=300,
    )

    while running:
        try:
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            channel.basic_qos(prefetch_count=Config.rabbitmq_prefetch)
            channel.basic_consume(
                queue=Config.rabbitmq_queue,
                on_message_callback=process_message,
            )
            log.info("Connected, consuming from %s", Config.rabbitmq_queue)
            channel.start_consuming()
        except pika.exceptions.ConnectionClosedByBroker:
            log.warning("Connection closed by broker, reconnecting...")
            time.sleep(5)
        except pika.exceptions.AMQPChannelError as e:
            log.error("Channel error: %s", e)
            time.sleep(5)
        except pika.exceptions.AMQPConnectionError:
            log.warning("Connection error, reconnecting in 5s...")
            time.sleep(5)

    db.close_all()
    log.info("Worker stopped")


if __name__ == "__main__":
    main()
