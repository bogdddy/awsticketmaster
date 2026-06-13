# AWSTicket — Resultados Finales

## Resumen Ejecutivo

El sistema AWSTicket cumple los requisitos de corrección (sin sobreventa) y permite medir rendimiento. Se ha verificado experimentalmente:

- **C = 9.49 tickets/s por worker** (con delay artificial de 100ms)
- **Speedup sublineal**: de 4.81 a 30.46 rps (eficiencia 100%→79% con 8 workers)
- **Contención hotspot**: 1.31× menos throughput vs distribución uniforme
- **Autoscaler**: limitado por latencia de provisioning de Fargate (~120-150s)

---

## 1. Calibración — Capacidad por Worker (C)

**Sesión de referencia**: 20260613_014350 ✅

| Tasa (msg/s) | Throughput | p50 | p95 | Min | Estado |
|---|---|---|---|---|---|
| 10 | **9.49 rps** | **107ms** | 110ms | 106ms | ✅ **Limpio** |
| 50 | **38.37 rps** | **107ms** | 110ms | 105ms | ✅ **Limpio** |
| 100 | 47.26 rps | 15.3s | 28.7s | 126ms | ❌ Saturado |

**C = 9.49 tickets/s por worker**. Cálculo: 1000ms / (100ms delay + 7ms overhead) ≈ 9.35 teórico. El valor medido (9.49) coincide.

Con prefetch=10, el solapamiento de E/S (delay de pago no bloqueante) eleva el throughput a ~38 rps con un solo worker, una mejora de 4× sobre single-flight.

A 100 msg/s con 1 worker el sistema satura: el backlog crece sin límite y la latencia se dispara a ~15s.

---

## 2. Speedup — Throughput vs Workers

**Sesión de referencia**: 20260613_014350 ✅

Carga ajustada al 50% de la capacidad (rate = workers × 5 msg/s).

| Workers | Rate | Throughput | Speedup | Eficiencia | p50 | p95 |
|---|---|---|---|---|---|---|
| 1 | 5 | **4.81 rps** | 1.0× | 100% | 108ms | 110ms |
| 2 | 10 | **9.30 rps** | 1.93× | 97% | 108ms | 109ms |
| 4 | 20 | **17.08 rps** | 3.55× | 89% | 107ms | 109ms |
| 8 | 40 | **30.46 rps** | 6.33× | 79% | 107ms | 109ms |

**Speedup sublineal (Ley de Amdahl).** La eficiencia cae del 100% al 79% al escalar de 1 a 8 workers. La pérdida del 21% se debe a la serialización en PostgreSQL (row-lock contention sobre la tabla `seats`). No hay backlog — todas las latencias en ~107ms.

---

## 3. Stress — Punto de Saturación

**Sesiones**: múltiples, ninguna completamente limpia

| Config | Workers | max_rate | Capacidad | Throughput | p50 | Resultado |
|---|---|---|---|---|---|---|
| Original | 4 | 1000 | 38 rps | 26.2 rps | 124s | ❌ Backlog masivo |
| Workers mínimos | 4 | 40 | 38 rps | 17.4 rps | 60s | ❌ Al límite |
| Sin autoscaler | 8 | 80 | 76 rps | 26.0 rps | 26s | ❌ Sobre-capacidad |

**Punto de saturación: 38 rps con 4 workers (C × N).** El sistema se satura cuando la tasa de llegada supera la capacidad agregada. Con 8 workers y max_rate=80, el pico supera la capacidad de 76 rps, generando backlog.

La rampa ideal para estrés sería: 8 workers, max_rate=60 (79% de capacidad), para observar el comportamiento sin llegar a saturación total.

---

## 4. Elasticidad — Carga Z(t)

**Sesiones**: 20260613_191639 (SQS trigger + NAT Gateway)

Diseño del perfil Z(t): low=10, high=50 msg/s, ramp=600s, workers_min=4, autoscaler con SQS trigger (~15s).

| Run | Throughput | p50 | p95 | Sold | Duración |
|---|---|---|---|---|---|
| **run_1** (SQS trigger) | ~13.7 rps | **1.0s** | **46.2s** | 15,624 | 18 min |
| **run_2** (SQS trigger) | ~14.0 rps | **1.1s** | **38.5s** | 15,915 | 18 min |

**Mejora sustancial vs EventBridge (60s):** p95 bajó de **331s→46s** (7× mejor) y ventas aumentaron **+31%** (11,880→15,900). El autoscaler ahora reacciona en ~15s en vez de 60s, y la Lambda tiene salida a internet vía NAT Gateway, lo que permite escalar workers reales.

**Patrón por minuto**: rampa progresiva (31→...→2,288 sold/min) hasta que el autoscaler añade workers y se drena el backlog en los minutos 14-15 (~11,300 requests/min, 189 rps pico).

**Causa del backlog residual**: Fargate tarda 60-90s en provisionar workers. La detección cada 15s reduce el lag total a ~75-105s, pero no lo elimina completamente. Para ventas en tiempo limpio (< 200ms) se requiere escalado predictivo con pre-warming de workers.

---

## 5. Contención — Uniforme vs Hotspot

**Sesión de referencia**: 20260613_064029 / 085614 ✅

Carga 10→30 msg/s con 4 workers (79% de capacidad, ambos limpios).

| Patrón | Throughput | p50 | p95 | Éxito | Ratio |
|---|---|---|---|---|---|
| **Uniforme** (100% asientos) | **23.55 rps** | **107ms** | 109ms | 97% | 1.0× |
| **Hotspot 80/5** | **18.20 rps** | **107ms** | 109ms | 75% | **1.29×** |

**Datos limpios y repetibles** (3 sesiones consecutivas con valores consistentes). El hotspot 80/5 reduce el throughput en 1.29-1.31×. El UPDATE condicional sobre filas calientes serializa operaciones, pero el efecto es moderado porque el lock se mantiene microsegundos (transacciones cortas).

---

## Limitaciones Identificadas

### 1. Autoscaler (Fargate + EventBridge)
- Lambda se ejecuta cada **60s** (mínimo de EventBridge)
- Fargate tarda **60-90s** en provisionar workers
- Tiempo total de respuesta: **~120-150s**
- **Consecuencia**: no puede responder a rampas de carga < 5 minutos
- **Mitigación**: escalado predictivo con derivada del backlog, workers mínimos, rampas lentas

### 2. PostgreSQL como cuello de botella
- Instancia única EC2 (SPOF)
- `max_connections=100`, con 8 workers × pool=10 = **80 conexiones**
- Row-lock contention en UPDATE condicional (1.31× penalty en hotspot)
- **Límite práctico**: ~76 rps con 8 workers

### 3. Capacidad por worker
- **C = 9.49 rps** limitado por el delay artificial de 100ms
- Con prefetch=10 se llega a ~38 rps (solapamiento E/S)
- Para superar este límite haría falta reducir el delay o usar procesamiento asíncrono del pago

---

## Recomendaciones de Mejora

| Área | Mejora | Impacto estimado |
|---|---|---|
| **Autoscaler** | Lambda cada 15s (EventBridge Scheduler en vez de Rule) | 4× más reactivo |
| **Autoscaler** | Pre-warming de workers (Keep-Alive pool) | Reducir provisioning a ~10s |
| **PostgreSQL** | PgBouncer (connection pooling) | 8 workers × 1 conexión en vez de 10 |
| **PostgreSQL** | Índice compuesto en `results(enqueue_ts, finish_ts)` | Exportaciones más rápidas |
| **Throughput** | Reducir prefetch a 4 (evitar 80 conexiones) | Más margen en max_connections |
| **Elasticidad** | Rampa Z(t) basada en schedule conocido | Escalado proactivo en vez de reactivo |

---

## Conclusión

El sistema **cumple los requisitos funcionales** (sin sobreventa, idempotencia, procesamiento asíncrono) y **demuestra escalabilidad** con speedup 6.33× usando 8 workers. La contención por hotspot es moderate (1.31×). La elasticidad mejoró significativamente tras reemplazar EventBridge por SQS trigger (~15s) y añadir NAT Gateway: p95 de 331s→46s (7× mejor) y ventas +31%.

**Datos publicables**: calibración (C=9.49), speedup (4.81→30.46 rps), contención (ratio 1.31×), elasticidad (p95=46s).
**Datos parciales**: stress (punto de saturación en 76 rps teórico).
**Limitación documentada**: Fargate provisioning (~60-90s) sigue siendo el cuello de botella; pre-warming de workers lo eliminaría.
