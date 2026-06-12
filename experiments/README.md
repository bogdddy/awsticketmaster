# Experiments — Plan de validación

## Requisitos previos

- RabbitMQ corriendo en `10.0.1.10`
- PostgreSQL corriendo en `10.0.1.20` con usuario `ticketapp` y permisos
- Worker ECS activo (`runningCount >= 1`)
- Dependencias instaladas: `pip3 install pika psycopg2-binary`

## Limpiar entre experimentos

```bash
python3 cleanup.py \
  --rabbitmq-host 10.0.1.10 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd
```

---

## A) Calibración de C (capacidad por worker)

Mide el throughput real por worker a diferentes tasas de envío.

```bash
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type calibration \
  --rates 10,50,100 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10
```

## B) Throughput vs workers (speedup)

Varía el número de workers y mide el throughput total.

```bash
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type speedup \
  --workers 1,2,4,8,16 \
  --rate 500 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10
```

## C) Stress / saturación

Incrementa la carga hasta encontrar el punto de saturación.

```bash
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type stress \
  --workers 4 \
  --max-rate 1000 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10
```

## D) Elasticidad (Z(t) completo)

Perfil de carga completo con autoscaling activado.

```bash
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type elasticity \
  --workers-min 1 \
  --workers-max 20 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10
```

## E) Contención: uniforme vs hotspot 80/5

Compara distribución uniforme vs hotspot (5% de asientos reciben 80% del tráfico).

```bash
# Distribución uniforme
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type contention \
  --hotspot-pct 100 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

# Hotspot 80/5
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type contention \
  --hotspot-pct 5 \
  --hotspot-traffic 80 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10
```
