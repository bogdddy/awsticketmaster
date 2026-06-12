import json
import logging
import random
import time
import signal
import sys
import uuid
from datetime import datetime, timezone

import pika

from .config import LoadConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("loadgen")

cfg = LoadConfig()
running = True


def signal_handler(sig, frame):
    global running
    log.info("Stopping load generator...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


class LoadGenerator:
    def __init__(self):
        self.credentials = pika.PlainCredentials(cfg.rabbitmq_user, cfg.rabbitmq_pass)
        self.params = pika.ConnectionParameters(
            host=cfg.rabbitmq_host,
            port=cfg.rabbitmq_port,
            credentials=self.credentials,
            heartbeat=300,
        )
        self.connection = pika.BlockingConnection(self.params)
        self.channel = self.connection.channel()
        self.channel.confirm_delivery()
        log.info("Connected to RabbitMQ %s:%s", cfg.rabbitmq_host, cfg.rabbitmq_port)

        self._build_hotspot_pool()

    def _build_hotspot_pool(self):
        hot_seat_count = max(1, int(cfg.total_seats * cfg.hotspot_pct_seats / 100))
        self.hot_seats = set(random.sample(range(1, cfg.total_seats + 1), hot_seat_count))
        self._hot_list = list(self.hot_seats)
        log.info("Hotspot pool: %d hot seats (%.1f%% of total)", len(self.hot_seats), cfg.hotspot_pct_seats)

    def _pick_seat(self):
        if random.random() * 100 < cfg.hotspot_pct_traffic and self._hot_list:
            return random.choice(self._hot_list)
        seat = random.randint(1, cfg.total_seats)
        while seat in self.hot_seats:
            seat = random.randint(1, cfg.total_seats)
        return seat

    def _build_message(self):
        return {
            "request_id": str(uuid.uuid4()),
            "event_id": 1 if cfg.mode == "numbered" else 2,
            "seat_id": self._pick_seat() if cfg.mode == "numbered" else None,
            "mode": cfg.mode,
            "enqueue_ts": datetime.now(timezone.utc).isoformat(),
        }

    def publish_batch(self, rate: float, duration: float) -> int:
        if rate <= 0:
            return 0

        interval = 1.0 / rate
        deadline = time.monotonic() + duration
        batch_start = time.monotonic()
        count = 0

        while time.monotonic() < deadline and running:
            msg = self._build_message()
            try:
                self.channel.basic_publish(
                    exchange=cfg.exchange,
                    routing_key=cfg.routing_key,
                    body=json.dumps(msg),
                    properties=pika.BasicProperties(delivery_mode=2),
                )
                count += 1
            except Exception as e:
                log.error("Publish error: %s", e)
                time.sleep(1)

            if count % 1000 == 0:
                log.info("Published %d messages at %.1f msg/s", count, rate)

            target_time = batch_start + (count + 1) * interval
            sleep_for = target_time - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

        return count

    def run_zt(self):
        p = cfg.phase
        total = 0

        log.info("=== Phase 1: Low load (%.0f msg/s for %ds) ===", p.low_rate, p.t1_low_s)
        total += self.publish_batch(p.low_rate, p.t1_low_s)

        log.info("=== Phase 2: Ramp-up (%.0f -> %.0f msg/s over %ds) ===", p.low_rate, p.high_rate, p.t2_ramp_s)
        ramp_steps = 20
        for step in range(ramp_steps):
            frac = (step + 1) / ramp_steps
            rate = p.low_rate + (p.high_rate - p.low_rate) * frac
            step_duration = p.t2_ramp_s / ramp_steps
            total += self.publish_batch(rate, step_duration)
            if not running:
                break

        for burst in range(p.spike_bursts):
            log.info("=== Phase 3a: Spike burst %d/%d (%.0f msg/s for %ds) ===", burst + 1, p.spike_bursts, p.spike_rate, p.t3_spike_s)
            total += self.publish_batch(p.spike_rate, p.t3_spike_s)

            log.info("=== Phase 3b: Recovery (%.0f msg/s for %ds) ===", p.high_rate, p.t3_spike_s)
            total += self.publish_batch(p.high_rate, p.t3_spike_s)
            if not running:
                break

        log.info("=== Phase 4: Sustained high (%.0f msg/s for %ds) ===", p.high_rate, p.t4_sustained_s)
        total += self.publish_batch(p.high_rate, p.t4_sustained_s)

        log.info("=== Phase 5: Cooldown (%.0f -> %.0f msg/s over %ds) ===", p.high_rate, p.low_rate, p.t5_cooldown_s)
        cooldown_steps = 10
        for step in range(cooldown_steps):
            frac = 1.0 - (step + 1) / cooldown_steps
            rate = p.low_rate + (p.high_rate - p.low_rate) * frac
            step_duration = p.t5_cooldown_s / cooldown_steps
            total += self.publish_batch(rate, step_duration)
            if not running:
                break

        log.info("=== Load test complete. Total messages: %d ===", total)
        return total

    def close(self):
        self.connection.close()


def main():
    log.info("Load Generator starting")
    log.info("Mode=%s, Exchange=%s, RoutingKey=%s", cfg.mode, cfg.exchange, cfg.routing_key)
    log.info("RabbitMQ=%s:%s", cfg.rabbitmq_host, cfg.rabbitmq_port)

    gen = LoadGenerator()
    try:
        total = gen.run_zt()
        log.info("Finished. Published %d messages", total)
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        gen.close()


if __name__ == "__main__":
    main()
