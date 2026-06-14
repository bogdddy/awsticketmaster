import os


class Config:
    rabbitmq_host = os.environ.get("RABBITMQ_HOST", "localhost")
    rabbitmq_port = int(os.environ.get("RABBITMQ_PORT", "5672"))
    rabbitmq_user = os.environ.get("RABBITMQ_USER", "guest")
    rabbitmq_pass = os.environ.get("RABBITMQ_PASS", "guest")
    rabbitmq_queue = os.environ.get("RABBITMQ_QUEUE", "tickets.buy")
    rabbitmq_prefetch = int(os.environ.get("RABBITMQ_PREFETCH", "10"))

    postgres_host = os.environ.get("POSTGRES_HOST", "localhost")
    postgres_port = int(os.environ.get("POSTGRES_PORT", "5432"))
    postgres_db = os.environ.get("POSTGRES_DB", "ticketdb")
    postgres_user = os.environ.get("POSTGRES_USER", "ticketapp")
    postgres_pass = os.environ.get("POSTGRES_PASS", "password")
    postgres_min_conn = int(os.environ.get("POSTGRES_MIN_CONN", "2"))
    postgres_max_conn = int(os.environ.get("POSTGRES_MAX_CONN", "10"))

    worker_id = os.environ.get("WORKER_ID", "unknown")
    payment_delay_ms = int(os.environ.get("PAYMENT_DELAY_MS", "100"))

    sqs_queue_url = os.environ.get("SQS_QUEUE_URL", "")
    sqs_scaling_interval = int(os.environ.get("SQS_SCALING_INTERVAL_S", "15"))

    max_retries = int(os.environ.get("MAX_RETRIES", "3"))
    retry_backoff_base_s = int(os.environ.get("RETRY_BACKOFF_BASE_S", "1"))
    retry_backoff_max_s = int(os.environ.get("RETRY_BACKOFF_MAX_S", "30"))

    rabbitmq_exchange = os.environ.get("RABBITMQ_EXCHANGE", "tickets")
    rabbitmq_routing_key = os.environ.get("RABBITMQ_ROUTING_KEY", "buy")
