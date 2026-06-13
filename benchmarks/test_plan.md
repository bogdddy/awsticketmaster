# Plan de Ejecución de Tests - AWSTicket

## Arquitectura de Máquinas

| Máquina | IP | Rol |
|---|---|---|
| **Local** | Tu PC | Terraform, AWS CLI, Docker build/push |
| **Loadgen** | 10.0.1.70 | Ejecuta benchmarks y cleanup |
| **RabbitMQ** | 10.0.1.10 | Broker de mensajes |
| **PostgreSQL** | 10.0.1.20 | Base de datos |
| **Workers** | ECS Fargate | Procesan cola |

---

## Preparación (Máquina LOCAL)

### 1. Verificar infraestructura

```bash
# Ver instancias EC2
aws ec2 describe-instances \
  --query "Reservations[*].Instances[*].{ID:InstanceId,Name:Tags[?Key=='Name'].Value | [0]}" \
  --output table \
  --region us-east-1
```

### 2. Verificar worker ECS

```bash
aws ecs describe-services \
  --cluster awsticket-cluster \
  --services awsticket-worker-svc \
  --query "services[0].{status:status,desired:desiredCount,running:runningCount}" \
  --region us-east-1
```

Debe mostrar `running: 1`.

### 3. Copiar código a loadgen

```bash
# Opción A: S3
aws s3 sync /root/urv/sd/awsticket/ s3://awsticket-code/ --exclude ".git/*"

# Opción B: SCP directo
scp -r /root/urv/sd/awsticket/ ec2-user@10.0.1.70:/home/ec2-user/awsticket/
```

---

## Preparación (Máquina LOADGEN)

### 4. Conectarse por SSM

```bash
aws ssm start-session --target i-0232ad1fed7596504 --region us-east-1
```

### 5. Instalar dependencias

```bash
pip3 install pika psycopg2-binary
```

### 6. Navegar al directorio

```bash
cd /home/ec2-user/awsticket/benchmarks
# o donde hayas copiado el código
```

---

## Ejecución de Tests

### Opción A: Script automático (RECOMENDADO)

```bash
chmod +x run_all_benchmarks.sh
./run_all_benchmarks.sh
```

El script ejecuta todos los tests en orden, hace cleanup entre runs, y guarda resultados en `benchmark_results/`.

**Nota:** Para speedup, el script pausa y te pide escalar workers manualmente desde otra terminal.

### Opción B: Manual

#### A) Calibración (1 worker, diferentes tasas)

```bash
# Verificar 1 worker
aws ecs update-service --cluster awsticket-cluster --service awsticket-worker-svc --desired-count 1 --region us-east-1

# Esperar 2 min a que arranque

for rate in 10 50 100; do
  echo "=== Calibración: rate=$rate ==="
  
  # Cleanup
  python3 cleanup.py --pg-host 10.0.1.20 --pg-user ticketapp --pg-password ddd
  
  # Ejecutar
  PYTHONPATH=../loadgen python3 run_experiment.py \
    --type calibration \
    --rates $rate \
    --pg-host 10.0.1.20 \
    --pg-user ticketapp \
    --pg-password ddd \
    --rabbitmq-host 10.0.1.10
  
  # Guardar resultados (archivo histórico)
  mkdir -p benchmark_results/calibration/rate_${rate}
  cp ./results/*.csv benchmark_results/calibration/rate_${rate}/
  
  sleep 10
done
```

#### B) Speedup (variar workers)

```bash
for workers in 1 2 4 8; do
  echo "=== Speedup: workers=$workers ==="
  
  # Escalar workers (desde OTRA terminal en máquina LOCAL)
  aws ecs update-service \
    --cluster awsticket-cluster \
    --service awsticket-worker-svc \
    --desired-count $workers \
    --region us-east-1
  
  # Esperar 2-3 min a que arranquen
  sleep 180
  
  # Cleanup
  python3 cleanup.py --pg-host 10.0.1.20 --pg-user ticketapp --pg-password ddd
  
  # Ejecutar
  PYTHONPATH=../loadgen python3 run_experiment.py \
    --type speedup \
    --workers $workers \
    --rate 300 \
    --pg-host 10.0.1.20 \
    --pg-user ticketapp \
    --pg-password ddd \
    --rabbitmq-host 10.0.1.10
  
  # Guardar resultados
  mkdir -p benchmark_results/speedup/workers_${workers}
  cp ./results/*.csv benchmark_results/speedup/workers_${workers}/
  
  sleep 15
done
```

#### C) Stress (4 workers, carga creciente)

```bash
# Escalar a 4 workers
aws ecs update-service --cluster awsticket-cluster --service awsticket-worker-svc --desired-count 4 --region us-east-1
sleep 180

# Cleanup
python3 cleanup.py --pg-host 10.0.1.20 --pg-user ticketapp --pg-password ddd

# Ejecutar
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type stress \
  --workers 4 \
  --max-rate 1000 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

# Guardar
mkdir -p benchmark_results/stress/max_rate_1000
cp ../results/*.csv benchmark_results/stress/max_rate_1000/
```

#### D) Elasticidad (Z(t) completo, 2 runs)

```bash
# Escalar a 1 worker (autoscaling hará el resto)
aws ecs update-service --cluster awsticket-cluster --service awsticket-worker-svc --desired-count 1 --region us-east-1
sleep 120

for run in 1 2; do
  echo "=== Elasticidad: run $run ==="
  
  # Cleanup
  python3 cleanup.py --pg-host 10.0.1.20 --pg-user ticketapp --pg-password ddd
  
  # Ejecutar
  PYTHONPATH=../loadgen python3 run_experiment.py \
    --type elasticity \
    --workers-min 1 \
    --workers-max 20 \
    --pg-host 10.0.1.20 \
    --pg-user ticketapp \
    --pg-password ddd \
    --rabbitmq-host 10.0.1.10
  
  # Guardar
  mkdir -p benchmark_results/elasticity/run_${run}
  cp ./results/*.csv benchmark_results/elasticity/run_${run}/
  
  sleep 20
done
```

#### E) Contención (uniforme vs hotspot)

```bash
# Uniforme
python3 cleanup.py --pg-host 10.0.1.20 --pg-user ticketapp --pg-password ddd

PYTHONPATH=../loadgen python3 run_experiment.py \
  --type contention \
  --hotspot-pct 100 \
  --hotspot-traffic 100 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

mkdir -p benchmark_results/contention/uniform
cp ../results/*.csv benchmark_results/contention/uniform/

# Hotspot 80/5
python3 cleanup.py --pg-host 10.0.1.20 --pg-user ticketapp --pg-password ddd

PYTHONPATH=../loadgen python3 run_experiment.py \
  --type contention \
  --hotspot-pct 5 \
  --hotspot-traffic 80 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

mkdir -p benchmark_results/contention/hotspot_80_5
cp ../results/*.csv benchmark_results/contention/hotspot_80_5/
```

---

## Verificación Durante Tests

### Cola RabbitMQ (Máquina RABBITMQ)

```bash
aws ssm start-session --target i-03fb7cab6e5b91db6 --region us-east-1
sudo rabbitmqctl list_queues name messages consumers
```

- `messages` debe bajar a ~0 después de cada test
- `consumers` debe ser >= 1 (número de workers)

### Actividad PostgreSQL (Máquina POSTGRESQL)

```bash
aws ssm start-session --target i-008caa436587b4324 --region us-east-1
sudo -u postgres psql -d ticketdb -c "
SELECT pid, state, query, age(clock_timestamp(), query_start) AS duration
FROM pg_stat_activity
WHERE datname = 'ticketdb' AND usename = 'ticketapp'
ORDER BY duration DESC
LIMIT 5;
"
```

No debe haber transacciones `idle in transaction` con duration > 10s.

### Logs del Worker (Máquina LOCAL)

```bash
aws logs tail /ecs/awsticket/worker --follow --region us-east-1
```

Debes ver:
```
Connected, consuming from tickets.buy
request_id=xxx result=sold
```

---

## Recopilación de Resultados

### Copiar resultados a máquina LOCAL

```bash
# Desde loadgen (si usaste SCP)
scp -r ec2-user@10.0.1.70:/home/ec2-user/awsticket/benchmarks/benchmark_results/ ./benchmark_results/

# O desde S3 si subiste
aws s3 sync s3://awsticket-code/benchmark_results/ ./benchmark_results/
```

### Estructura de resultados

```
benchmark_results/
├── calibration/
│   ├── rate_10/
│   │   ├── summary.csv
│   │   ├── throughput_by_minute.csv
│   │   ├── results.csv
│   │   └── processed.csv
│   ├── rate_50/
│   └── rate_100/
├── speedup/
│   ├── workers_1/
│   ├── workers_2/
│   ├── workers_4/
│   └── workers_8/
├── stress/
│   └── max_rate_1000/
├── elasticity/
│   ├── run_1/
│   └── run_2/
└── contention/
    ├── uniform/
    └── hotspot_80_5/
```

---

## Generación de Plots (Máquina LOCAL)

```bash
cd /root/urv/sd/awsticket/analysis

# Instalar dependencias
pip3 install matplotlib pandas

# Generar plots (desde un benchmark específico)
python3 analysis/plot_results.py --input-dir benchmark_results/calibration/rate_10 --output-dir ./plots
```

### Plots requeridos (Section 13 de specifications.txt)

1. **Throughput vs Workers** (speedup)
   - X: número de workers
   - Y: throughput total (msg/s)
   - Datos: `benchmark_results/speedup/workers_N/summary.csv`

2. **Queue backlog vs time** (elasticity)
   - X: tiempo (minutos)
   - Y: mensajes en cola + workers activos
   - Datos: `benchmark_results/elasticity/run_1/throughput_by_minute.csv`

3. **Latency percentiles** (todos los tests)
   - X: tipo de test
   - Y: latencia ms (p50, p95, p99)
   - Datos: `benchmark_results/*/summary.csv`

---

## Troubleshooting

### Latencias altas (>5s)

**Causa:** Worker no consume en tiempo real o hay backlog acumulado.

**Solución:**
```bash
# Verificar cola
sudo rabbitmqctl list_queues name messages consumers

# Si messages > 1000, esperar a que drene o purgar:
sudo rabbitmqctl purge_queue tickets.buy

# Verificar que no hay transacciones bloqueadas
sudo -u postgres psql -d ticketdb -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'ticketdb' AND pid <> pg_backend_pid();
"
```

### Worker no arranca

**Causa:** Imagen no existe en ECR o task definition incorrecta.

**Solución:**
```bash
# Reconstruir imagen
cd /root/urv/sd/awsticket/worker
docker build -t awsticket/worker:latest .
docker tag awsticket/worker:latest 296742169132.dkr.ecr.us-east-1.amazonaws.com/awsticket/worker:latest
docker push 296742169132.dkr.ecr.us-east-1.amazonaws.com/awsticket/worker:latest

# Forzar redeploy
aws ecs update-service \
  --cluster awsticket-cluster \
  --service awsticket-worker-svc \
  --desired-count 1 \
  --force-new-deployment \
  --region us-east-1
```

### Cleanup se cuelga

**Causa:** Transacciones bloqueadas en PostgreSQL.

**Solución:**
```bash
# Desde máquina PostgreSQL
sudo -u postgres psql -d ticketdb -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'ticketdb' AND pid <> pg_backend_pid();
"

# Luego ejecutar cleanup
python3 cleanup.py --pg-host 10.0.1.20 --pg-user ticketapp --pg-password ddd
```

### Credenciales AWS expiradas

**Causa:** AWS Academy expira cada 4 horas.

**Solución:**
```bash
# Renovar credenciales desde portal AWS Academy
export AWS_ACCESS_KEY_ID="nuevo_access_key"
export AWS_SECRET_ACCESS_KEY="nuevo_secret_key"
export AWS_SESSION_TOKEN="nuevo_session_token"
export AWS_DEFAULT_REGION="us-east-1"
```

---

## Checklist Final

- [ ] Infraestructura desplegada (`terraform apply`)
- [ ] Worker image subida a ECR
- [ ] Worker corriendo (`runningCount >= 1`)
- [ ] RabbitMQ accesible desde loadgen
- [ ] PostgreSQL accesible desde loadgen
- [ ] Dependencias instaladas en loadgen (`pika`, `psycopg2-binary`)
- [ ] Tests ejecutados (calibration, speedup, stress, elasticity, contention)
- [ ] Resultados guardados en `benchmark_results/`
- [ ] Plots generados
- [ ] Report completado con análisis
