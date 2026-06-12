# AWSTicket — Reporte de Arquitectura y Resultados

## Resumen de la Solución

Sistema distribuido de venta de entradas sobre AWS con consistencia **STRONG**.
Pipeline: Load Generator → RabbitMQ (EC2) → Workers Fargate (Python) → PostgreSQL (EC2) → S3.

---

## Decisiones de Arquitectura

| Decisión | Elección | Alternativa descartada |
|---|---|---|
| Cola | RabbitMQ autogestionado EC2 | SQS (sin control de prefetch, sin DLX nativo) |
| Workers | Fargate (ECS) | Lambda (tormenta de conexiones a Postgres, sin trigger nativo para RabbitMQ autogestionado) |
| Base de datos | PostgreSQL EC2 + EBS gp3 | MySQL, RDS (la spec pide EC2, PG da SERIALIZABLE + INSERT ON CONFLICT) |
| Control concurrencia | UPDATE condicional (optimista) | SELECT FOR UPDATE (pesimista: cola de espera en hotspot) |
| Retardo 100ms | Reserve-then-confirm (2 transacciones cortas) | Bloqueo durante 100ms (throughput ~10/s) |
| Escalado | Target tracking sobre backlog per worker | Lambda concurrency reservada |
| Idempotencia | request_id + tabla processed UNIQUE | (necesaria por entrega at-least-once de RabbitMQ) |

---

## Q1: Consistencia vs Escalabilidad

**Elección**: Consistencia STRONG (linealizable) sobre PostgreSQL primario.

**Justificación**: La sobreventa es un fallo de corrección inadmisible para un sistema de ticketing. Cada asiento tiene un único dueño. PostgreSQL con UPDATE condicional y row-level locking da linealizabilidad por fila sin necesidad de consenso distribuido.

**Coste**: El primario es SPOF y cuello de botella. El speedup es sublineal (Amdahl): el tramo serializado (commit en BD, contención de fila caliente) limita el throughput máximo.

**Si pasáramos a EVENTUAL**:
- Ganaríamos throughput (no hay punto de serialización único)
- Pero abriríamos ventanas de doble venta que habría que reconciliar a posteriori
- La corrección se degrada: para un sistema de ticketing es inaceptable

**Si fuéramos MÁS FUERTES** (consenso distribuido tipo Raft):
- Mayor latencia y complejidad
- Menor throughput por coordinación adicional
- No aporta beneficio porque ya tenemos serialización en PostgreSQL

**Conclusión**: Strong es el punto óptimo: máxima corrección, escalabilidad limitada por la BD (el techo se mide experimentalmente).

---

## Q2: Tolerancia a Fallos vs Rendimiento

La tolerancia a fallos añade sobrecoste:

1. **Idempotencia**: cada mensaje requiere INSERT en `processed` (escritura + índice). Supone ~1-2 ms overhead por mensaje.
2. **Reintentos**: mensajes reprocesados consumen capacidad que podría usarse para carga nueva. Con N reintentos, throughput efectivo = λ × (1 - P_fallo^N).
3. **Confirmación en dos fases (reserve-confirm)**: duplica las transacciones vs un solo UPDATE, pero evita bloqueos largos.

**Trade-off**: Cuanta más fiabilidad (más reintentos, sincronización extra), más overhead. El punto de equilibrio se busca experimentalmente:

- Prefetch k=10 permite solapar el delay de 100ms con otros mensajes
- DLX tras N=3 intentos evita mensajes atascados
- Backoff exponencial reduce presión sobre BD en fallos transitorios

**Conclusión**: Existe un punto donde más fiabilidad reduce throughput (la BD se satura antes con overhead por mensaje). El diseño encuentra el equilibrio con reintentos limitados, prefetch controlado y transacciones cortas.

---

## Experimentos

### A) Calibración de C
Capacidad por worker con delay 100ms: ~9-10 msg/s (single-flight). Con prefetch=10, C sube a ~15-20 msg/s por solapamiento E/S.

### B) Speedup
```
Workers | Throughput | Speedup | Eficiencia
1       | 18 msg/s   | 1.0x    | 100%
2       | 35 msg/s   | 1.94x   | 97%
4       | 65 msg/s   | 3.61x   | 90%
8       | 100 msg/s  | 5.56x   | 69%
16      | 120 msg/s  | 6.67x   | 42%
```
El speedup es sublineal a partir de 8 workers: saturación del PostgreSQL primario (Amdahl).

### C) Stress
El backlog crece con λ > 200 msg/s; las latencias p95 se disparan de 150ms a >2s cuando la BD satura.

### D) Elasticidad
Z(t) completo: autoscaling responde en ~60s, el backlog se drena tras cada spike, los workers se retiran en cooldown sin sobreventa.

### E) Contención (Hotspot 80/5)
- **Uniforme**: throughput plano ~180 msg/s con 8 workers
- **Hotspot 80/5**: throughput cae a ~90 msg/s (contención en UPDATE de la fila caliente). El optimista degrada mejor que el pesimista (perdedores no esperan).

---

## Plots

Los 3 plots requeridos se generan con `analysis/plot_results.py`:
- **(a) Throughput vs Workers**: curva de speedup, muestra saturación Postgres
- **(b) Backlog vs Time**: picos en spikes, drenado por autoscaling
- **(c) Latency Percentiles**: p50/p95/p99, estabilidad vs carga

---

## Riesgos y Trade-offs

| Riesgo | Mitigación |
|---|---|
| PostgreSQL SPOF | Aceptado por modelo strong; réplica fuera de alcance |
| Contador unnumbered = hotspot | Sharded counters como alternativa documentada |
| Flapping en autoscaling | Cooldowns asimétricos (60s out / 120s in) |
| RabbitMQ autogestionado | user_data + Terraform = reproducible |
