import psycopg2
from psycopg2 import pool
from app.config import Config


class Database:
    def __init__(self):
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=Config.postgres_min_conn,
            maxconn=Config.postgres_max_conn,
            host=Config.postgres_host,
            port=Config.postgres_port,
            dbname=Config.postgres_db,
            user=Config.postgres_user,
            password=Config.postgres_pass,
        )

    def get_conn(self):
        return self._pool.getconn()

    def put_conn(self, conn):
        self._pool.putconn(conn)

    def close_all(self):
        self._pool.closeall()


# Idempotencia: si el request_id ya existe en processed, es un mensaje duplicado
# (entrega at-least-once de RabbitMQ). Devolvemos el resultado anterior para
# que el worker pueda ack sin reprocesar.
def check_idempotent(conn, request_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT result FROM processed WHERE request_id = %s",
            (request_id,)
        )
        row = cur.fetchone()
        if row:
            conn.rollback()
            return row[0]
    conn.rollback()
    return None


# Registra el resultado del procesamiento. ON CONFLICT DO NOTHING garantiza
# que aunque dos workers intenten marcar el mismo request_id, solo el primero
# persiste -> idempotencia incluso si el INSERT se ejecuta 2 veces.
def mark_processed(conn, request_id, result, enqueue_ts=None, start_ts=None, finish_ts=None, worker_id=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO processed (request_id, result)
               VALUES (%s, %s)
               ON CONFLICT (request_id) DO NOTHING""",
            (request_id, result)
        )
        if enqueue_ts:
            cur.execute(
                """INSERT INTO results (request_id, enqueue_ts, start_ts, finish_ts, outcome, worker_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (request_id) DO NOTHING""",
                (request_id, enqueue_ts, start_ts, finish_ts, result, worker_id)
            )
    conn.commit()


# UPDATE condicional: solo cambia el estado si el asiento esta 'available'.
# PostgreSQL bloquea la fila a nivel de fila durante la ejecucion, serializando
# los accesos concurrentes. Si rowcount=0, el asiento ya fue vendido por otro worker.
# Esta es la unica sentencia que garantiza no oversell en modo numbered.
def sell_seat(conn, event_id, seat_id, request_id):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE seats
               SET status = 'sold', request_id = %s, sold_at = NOW()
               WHERE event_id = %s AND seat_id = %s AND status = 'available'""",
            (request_id, event_id, seat_id)
        )
        return cur.rowcount


# Decremento atomico del inventario con guarda: solo descuenta si aun hay
# capacidad disponible (sold < capacity). El CHECK (sold <= capacity) en la
# tabla actua como red de seguridad adicional.
def decrement_inventory(conn, event_id):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE inventory
               SET sold = sold + 1
               WHERE event_id = %s AND sold < capacity""",
            (event_id,)
        )
        return cur.rowcount
