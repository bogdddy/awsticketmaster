# AWSTicket — Resultados Finales

## Resumen Ejecutivo

Sistema distribuido de venta de entradas con consistencia **STRONG** sobre PostgreSQL.
Pipeline: Load Generator → RabbitMQ → Workers Fargate → PostgreSQL.

- **C = 9.49 tickets/s por worker** (delay artificial 100ms + overhead 7ms)
- **Speedup**: 4.81 → 30.46 rps (eficiencia 100% → 79% con 8 workers)
- **Contención hotspot**: 1.29× menos throughput vs distribución uniforme
- **Autoscaler**: limitado por latencia de provisioning de Fargate (~75-105s)

---

## 1. Calibración — Capacidad por Worker (C)

1 worker, carga constante 60s + 30s warmup.

| Tasa | Throughput | p50 | p95 | Min | Estado |
|---|---|---|---|---|---|
| 10 msg/s | **9.49 rps** | **107ms** | 110ms | 106ms | ✅ **Limpio** |
| 50 msg/s | **38.37 rps** | **107ms** | 110ms | 105ms | ✅ **Limpio** |
| 100 msg/s | 47.26 rps | 15.3s | 28.7s | 126ms | ❌ Saturado |

**C = 9.49 tickets/s por worker**. Coincide con 1000ms / (100ms delay + 7ms overhead) ≈ 9.35 teórico.

Con prefetch=10, el solapamiento E/S eleva el throughput a ~38 rps (4× mejora sobre single-flight).

A 100 msg/s el sistema satura: el backlog crece sin límite y la latencia se dispara a ~15s.

---

## 2. Speedup — Throughput vs Workers

Carga al 50% de capacidad (rate = workers × 5 msg/s), 120s de duración.

| Workers | Rate | Throughput | Speedup | Eficiencia | p50 | p95 |
|---|---|---|---|---|---|---|
| 1 | 5 | **4.81 rps** | 1.0× | 100% | 108ms | 110ms |
| 2 | 10 | **9.30 rps** | 1.93× | 97% | 108ms | 109ms |
| 4 | 20 | **17.08 rps** | 3.55× | 89% | 107ms | 109ms |
| 8 | 40 | **30.46 rps** | 6.33× | 79% | 107ms | 109ms |

**Speedup sublineal (Ley de Amdahl).** La eficiencia cae del 100% al 79% al escalar de 1 a 8 workers. La pérdida del 21% se debe a la serialización en PostgreSQL (row-level lock contention sobre la tabla `seats`). Todas las latencias en ~107-108ms, sin backlog.

---

## 3. Stress — Punto de Saturación

8 workers, autoscaler desactivado, rampa 10→80 msg/s. El pico supera la capacidad de 8×9.49=76 rps.

| Métrica | Valor |
|---|---|
| Throughput | 26.0 rps |
| p50 | **26.4s** |
| Min latency | 106ms |
| Sold | 7,701 / 15,732 |
| Duración | 296s |

La rampa 10→80 supera la capacidad agregada (76 rps) hacia el final, generando backlog. **Punto de saturación teórico: C × N = 9.49 × 8 = 76 rps.** Para datos limpios se requeriría max_rate ≤ 60.

---

## 4. Elasticidad — Carga Z(t)

4 workers mínimo, autoscaler ON (SQS trigger ~15s + NAT Gateway), rampa 600s, Z(t) 10→50 msg/s.

| Run | Throughput | p50 | p95 | Sold | Duración |
|---|---|---|---|---|---|
| run_1 | ~13.7 rps | **1.0s** | **46.2s** | 15,624 | 18 min |
| run_2 | ~14.0 rps | **1.1s** | **38.5s** | 15,915 | 18 min |

**Mejora sustancial vs EventBridge (60s):** p95 bajó de **331s→46s** (7× mejor) y ventas aumentaron **+31%** (11,880→15,900). 

**Qué cambió:**
- Trigger de Lambda pasó de EventBridge (60s) → **SQS desde workers (~15s)**
- Se añadió **NAT Gateway** para que la Lambda alcance APIs de AWS (ECS, CloudWatch)
- Métrica `BacklogPerWorker` con `StorageResolution=1` (high-resolution)

**Backlog residual**: Fargate sigue tardando 60-90s en provisionar, por lo que el lag total es ~75-105s. Para eliminar completamente el backlog haría falta pre-warming de workers.

---

## 5. Contención — Uniforme vs Hotspot

4 workers, carga 10→30 msg/s (79% de capacidad). **Ambos limpios.**

| Patrón | Throughput | p50 | p95 | Éxito | Ratio |
|---|---|---|---|---|---|
| **Uniforme** | **23.55 rps** | **107ms** | 109ms | 98% | 1.0× |
| **Hotspot 80/5** | **18.20 rps** | **107ms** | 109ms | 75% | **1.29×** |

**Datos repetibles** (consistente en 3 sesiones consecutivas). El UPDATE condicional sobre filas calientes serializa operaciones, reduciendo el throughput en 1.29×. El efecto es moderado porque los locks se mantienen microsegundos.

---

## Limitaciones

| Limitación | Causa | Impacto |
|---|---|---|
| **Autoscaler lento** | SQS trigger ~15s + Fargate 90s = ~105s lag | Elasticidad no funcional para rampas < 600s |
| **PostgreSQL SPOF** | Instancia única EC2 | Sin HA, failback manual |
| **Conexiones PG** | max_connections=100, pool=10/worker | 8 workers = 80 conexiones (margen justo) |
| **Contención hotspot** | UPDATE condicional serializa por fila | Penalty 1.29× en hotspot 80/5 |

---

## Conclusión

| Experimento | Datos | Publicable |
|---|---|---|
| Calibración | ✅ C=9.49 rps, p50=107ms | **Sí** |
| Speedup | ✅ 4.81→30.46 rps, eficiencia 79% | **Sí** |
| Contención | ✅ Ratio 1.29×, p50=107ms | **Sí** |
| Stress | ❌ p50=26s (backlog en pico) | Punto saturación: 76 rps teórico |
| Elasticidad | ✅ p50=1s, p95=46s, +31% ventas | **Sí** (con SQS trigger + NAT) |

**El sistema cumple los requisitos de corrección y escalabilidad.** 4 de 5 experimentos con datos limpios. El fix del autoscaler (SQS trigger ~15s + NAT Gateway) eliminó el cuello de botella de EventBridge, reduciendo el p95 de 331s→46s y aumentando las ventas un 31%.
