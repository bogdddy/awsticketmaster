# AWSTicket — Informe Final

## 1. Resumen

Sistema distribuido de venta de entradas sobre AWS con consistencia fuerte, escalado elastico y procesamiento asincrono. Pipeline: Load Generator (EC2) -> RabbitMQ (EC2) -> Workers Fargate (ECS) -> PostgreSQL (EC2) -> S3.

## 2. Arquitectura

| Componente | Stack | Proposito |
|-----------|-------|-----------|
| Load Generator | EC2 (Python) | Produce carga Z(t) |
| Cola | RabbitMQ EC2 | Desacople asincrono, backlog como senal de escalado |
| Workers | ECS Fargate | Stateless: dedup -> delay 100ms -> UPDATE condicional -> ack |
| Base de datos | PostgreSQL EC2 | Fuente de verdad: asientos, inventario, idempotencia, resultados |
| Autoscaler | Lambda + SQS | Lee backlog RabbitMQ, calcula workers, llama ECS API |
| Monitorizacion | CloudWatch + S3 | Dashboards, logs, resultados, plots |

### 2.1 Modelo de concurrencia

UPDATE condicional con row-level locking en PostgreSQL. Cada venta ejecuta:

```sql
UPDATE seats SET status='sold', request_id=%s, sold_at=NOW()
WHERE event_id=%s AND seat_id=%s AND status='available';
-- Si rowcount=0 -> asiento ya vendido -> rechazo
```

Esto garantiza consistencia fuerte sin necesidad de consenso distribuido. El coste es un speedup sublineal (Ley de Amdahl) por la serializacion en la BD.

## 3. Problemas encontrados y soluciones

### 3.1 Autoscaler con EventBridge (60s) -> SQS (~15s)

**Problema:** La Lambda de autoscaling se ejecutaba cada 60s via EventBridge (minimo soportado). Combinado con provisioning de Fargate (60-90s), el tiempo total de respuesta era ~150s. Para una rampa de 600s, el autoscaler tenia solo ~4 ciclos de evaluacion, causando backlog masivo.

Ademas, la Lambda estaba en una subnet publica sin NAT Gateway. Las ENIs de Lambda en VPC no reciben IPs publicas, por lo que las llamadas a ECS y CloudWatch fallaban por timeout. El autoscaler estaba efectivamente muerto.

**Solucion:** 
- Workers publican un mensaje a SQS cada ~15s, que dispara la Lambda
- Anyadido NAT Gateway para dar salida a internet a la Lambda
- Metrica BacklogPerWorker con StorageResolution=1 (high-resolution)

**Resultado:** p95 bajo de 331s a 46s (7x). Ventas aumentaron de 11.880 a 15.900 (+31%).

### 3.2 DLX no ruteaba a DLQ

**Problema:** La politica DLX tenia `dead-letter-routing-key: ""` (preserva routing key original) y el binding de `tickets.dlx` a `tickets.dlq` tenia `routing_key: ""`. Los mensajes llegaban a `tickets.dlx` con routing_key `"buy"` (la original), que no coincidia con el binding de `""`. Los mensajes se perdian silenciosamente.

**Solucion:** Cambiar `dead-letter-routing-key` a `"buy"` y el binding a `routing_key: "buy"`. Actualizado en Terraform y en caliente via API de RabbitMQ.

### 3.3 Capacidad por worker limitada

**Problema:** El delay artificial de 100ms (requisito de realismo) limita la capacidad base a ~10 rps por worker.

**Solucion:** Usar prefetch=10 para solapar el delay con otros mensajes, elevando el throughput efectivo a ~38 rps. El speedup con 8 workers alcanza 30.59 rps (eficiencia 79%).

### 3.4 Contaminacion de datos entre experimentos

**Problema:** Experimentos speedup, stress y elasticidad usaban cargas fijas muy por encima de la capacidad del sistema (speedup usaba 300 msg/s para todos los workers, stress arrancaba en 50 msg/s). Esto causaba backlog desde el segundo 1, generando datos inservibles.

**Solucion:** 
- Speedup: rate = workers x C x 0.5 (50% de capacidad)
- Stress: low=10, high=80, autoscaler OFF, workers fijos
- Elasticidad: low=10, high=50, workers-min=4, rampa 600s
- Contencion: low=10, high=30 (75% de capacidad)
- Cleanup entre runs con drene de cola y kill de conexiones PG

## 4. Resultados experimentales

| Experimento | Resultado clave |
|------------|----------------|
| Calibracion | C = 9.49 rps (prefetch=10 -> ~38 rps) |
| Speedup | 4.89 -> 30.59 rps (eficiencia 100% -> 79%) |
| Contencion | Hotspot 80/5 penaliza 1.29x vs uniforme |
| Elasticidad | p95 = 46s (era 331s con EventBridge) |
| Stress | 26.26 rps sostenidos, saturacion en ~76 rps |

## 5. Dependencias entre archivos

| Archivo | Proposito |
|---------|-----------|
| `README.md` | Vision general, arquitectura, resultados clave |
| `deploy.md` | Guia de despliegue paso a paso |
| `specifications.txt` | Enunciado de la practica |
| `roadmap.txt` | Diseno de arquitectura y justificaciones |
| `benchmarks/test_plan.md` | Plan de ejecucion de experimentos |
| `benchmarks/analysis/report.md` | Reporte completo con analisis y Q1/Q2 |
| `benchmarks/benchmark_results/results.md` | Resultados finales detallados |
| `benchmarks/benchmark_results/summary.json` | Datos agregados de todos los experimentos |
| `benchmarks/plots/` | 5 plots de validacion (throughput, backlog, latencias) |

## 6. Tests de validacion

| Test | Script | Que verifica |
|------|--------|-------------|
| Oversell | `benchmarks/tests/test_oversell.py` | Consistencia UPDATE condicional con pool de 100 asientos y 500 requests concurrentes |
| Fault tolerance | `benchmarks/tests/test_fault_tolerance.py` | Recuperacion tras matar worker ECS durante carga moderada |
| Retry + DLQ | En worker `app/main.py` | 3 reintentos con backoff exponencial (1s, 2s, 4s), mensaje a DLQ tras agotar |

## 7. Archivos eliminados

| Archivo | Motivo |
|---------|--------|
| `ARCHITECTURE_LOG.md` | Contenido migrado a README.md y report.md |
| `benchmarks/analysis/results.md` | Duplicado de benchmark_results/results.md |
| `tests/test_retry_locally.py` | Obsoleto (todo se prueba en AWS) |
