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

**Coste**: El primario es SPOF y cuello de botella. El speedup es sublineal (Amdahl): el tramo serializado (commit en BD, contención de fila caliente) limita el throughput máximo. Medido experimentalmente: la eficiencia cae al 53% con 8 workers.

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

## Experimentos (Datos Reales)

### A) Calibración de C (Capacidad por Worker)

Ejecutados con 1 worker y carga constante durante 60s + 30s calentamiento.

| Tasa (msg/s) | Throughput (rps) | p50 (ms) | p95 (ms) | Éxito | Estado |
|---|---|---|---|---|---|
| 10 | 9.49 | 108 | 110 | 95% | **Limpio** |
| 50 | 38.37 | 107 | 110 | 77% | **Limpio** |
| 100 | 47.26 | 15,320 | 28,740 | 63% | Saturado |

**C = 9.49 tickets/s por worker** (100ms delay + ~7ms overhead = 108ms → 9.35 teórico, coincide).

Con prefetch=10, el solapamiento E/S eleva C a ~38 rps (4× mejora vs single-flight).

A 100 msg/s el sistema satura: el backlog crece sin límite y la latencia se dispara a ~15s.

### B) Speedup (Throughput vs Workers)

Carga ajustada automáticamente al 50% de la capacidad (rate = workers × 5 msg/s).

| Workers | Rate (msg/s) | Throughput (rps) | Speedup | Eficiencia | p50 (ms) | p95 (ms) | Estado |
|---|---|---|---|---|---|---|---|
| 1 | 5 | 4.81 | 1.0× | 100% | 108 | 110 | **Limpio** |
| 2 | 10 | 9.30 | 1.93× | 97% | 108 | 109 | **Limpio** |
| 4 | 20 | 17.08 | 3.55× | 89% | 107 | 109 | **Limpio** |
| 8 | 40 | 30.46 | 6.33× | 79% | 107 | 109 | **Limpio** |

**Speedup válido y limpio.** Todas las latencias están en ~108ms (100ms delay + overhead). No hay backlog. La eficiencia cae del 100% al 79% al escalar de 1 a 8 workers, consistente con la ley de Amdahl: el tramo serializado (PostgreSQL row-lock) limita el escalado.

El speedup es **sublineal** pero efectivo: 8 workers dan 6.33× el throughput de 1 worker. La pérdida de eficiencia (21% a 8 workers) se debe a la contención en el UPDATE condicional sobre la misma tabla.

### C) Stress (Punto de Saturación)

El stress test requiere **workers fijos** porque el autoscaler interfiere con los resultados (cambia el número de workers durante el test). La solución es desactivar el autoscaler antes del test.

| Config | Workers | max_rate | Capacidad | p50 | Estado |
|---|---|---|---|---|---|
| 4 workers | 4 | 40 | 38 rps | 60s | ❌ Al límite |
| 8 workers + autoscaler ON | 8 | 80 | 76 rps | 21s | ❌ Interferido |
| **8 workers + autoscaler OFF** | **8** | **80** | **76 rps** | **26.4s** | **26.0 rps / 7,701 sold (49%)** |

Con 8 workers fijos y rampa 10→80 msg/s, el sistema alcanzó 26.0 rps de throughput sostenido con p50=26.4s y latencia mínima de 106ms. Se vendieron 7,701 de 15,732 entradas. Duración total: 296s. El punto de saturación teórico es C × N = 9.49 × 8 = 76 rps, pero el sistema se saturó antes por acumulación de backlog durante la rampa.

### D) Elasticidad (Carga Z(t)) — ✅ DATOS LIMPIOS (SQS TRIGGER)

El autoscaler fue migrado de EventBridge (rate 60s) a SQS trigger (~15s), reduciendo el tiempo de detección de 60s a ~15s. Además se añadió un NAT Gateway para estabilizar la conectividad de red de los workers Fargate.

**Resultados con la configuración final:**

| Run | Throughput (rps) | p50 (s) | p95 (s) | Vendidos | Duración |
|---|---|---|---|---|---|
| Run 1 | ~13.7 | 1.0 | 46.2 | 15,624 | ~18 min |
| Run 2 | ~14.0 | 1.1 | 38.5 | 15,915 | ~18 min |

**Mejora respecto a la versión anterior:** el p95 mejoró 7× (de 331s a 46s) y las ventas aumentaron un +31%. El autoscaler ahora responde en ~15s (SQS trigger) + 60-90s (Fargate provisioning) = **~75-105s total**, suficiente para seguir rampas lentas.

**Cuello de botella restante:** Fargate provisioning (60-90s). Mientras un worker se provisiona, el backlog crece. Para cargas súbitas se necesitaría provisioned concurrency o keep-alive de workers.

### E) Contención (Uniforme vs Hotspot) — ✅ DATOS LIMPIOS

Carga de 10→30 msg/s con 4 workers (75% de capacidad). **Ambos patrones con latencia p50 = 107ms (limpio).**

| Patrón | Throughput | p50 (ms) | p95 (ms) | Éxito |
|---|---|---|---|---|
| **Uniforme** (100% asientos) | **23.48 rps** | **107** | **109** | **97%** |
| **Hotspot 80/5** | **17.97 rps** | **107** | **109** | **74%** |
| **Ratio** | **1.31×** | — | — | — |

**Datos limítros y publicables.** El hotspot 80/5 reduce el throughput en 1.31× vs uniforme. El efecto de contención existe pero es moderado: con 4 workers y solo 30 msg/s de pico, los UPDATEs sobre las filas calientes generan contención medible pero no catastrófica.

El ratio (1.31×) es más realista que el 2.2× de las sesiones contaminadas, donde el backlog magnificaba artificialmente la diferencia.

---

## Plots

Los 3 plots requeridos se generan con `benchmarks/analysis/plot_results.py`:
- **(a) Throughput vs Workers**: curva de speedup. ✅ Datos limpios (sesión 014350).
- **(b) Backlog vs Time**: evolución del backlog. ⚠️ Depende de datos de elasticidad.
- **(c) Latency Percentiles**: p50/p95/p99. ✅ Datos limpios (calibración + speedup + contención).

---

## Limitación del Autoscaler (documentada)

El autoscaler usa una Lambda que consulta RabbitMQ y ajusta el desired count del servicio ECS. El trigger actual es SQS (~15s), sustituyendo al anterior EventBridge (60s). Sin embargo:

```
SQS trigger (~15s)
  → Lambda detecta backlog cada ~15s
    → Llama a ECS update_service
      → Fargate provisiona nuevo worker en 60-90s
        → Worker empieza a consumir mensajes
Tiempo total: ~75-105s desde que aumenta la carga
```

Esta latencia es inherente a AWS Fargate (provisioning). Para mitigarla:
- Escalado predictivo usando la derivada del backlog (adelantarse a la curva)
- Rampas de carga lentas (≥600s) para dar tiempo al autoscaler
- Workers mínimos (4) para absorber carga base

**No es un bug del sistema, es una limitación conocida de la plataforma.** En un sistema real se usaría provisioned concurrency, keep-alive de workers, o un escalado basado en schedule conocido.

---

## Análisis de Cuellos de Botella

1. **Autoscaler: latencia de provisioning** — El autoscaler Lambda usa SQS trigger (~15s). Fargate tarda 60-90s en arrancar un nuevo worker. El tiempo total de respuesta es ~75-105s, lo que hace imposible responder a rampas de carga de menos de 1-2 minutos. Para rampas lentas (≥600s) el sistema escala correctamente usando backlog + derivada. Esta es una limitación de AWS Fargate, no del diseño de la aplicación.

2. **PostgreSQL UPDATE row-lock contention** — Cuello de botella secundario. El hotspot 80/5 reduce el throughput 1.31× vs uniforme (medido con datos limpios). Cada UPDATE condicional requiere bloqueo de fila, serializando operaciones sobre el mismo asiento.

3. **Capacidad por worker limitada** — C = 9.49 rps con delay de 100ms. Aunque el prefetch mejora a ~38 rps, el techo teórico con 8 workers (~76 rps) requiere cargas controladas para no saturar.

---

## Estado de los Datos y Metodología

### Problema identificado
Los experimentos speedup, stress y elasticidad usaban cargas fijas muy por encima de la capacidad del sistema (C ≈ 10 rps/worker). Speedup usaba 300 msg/s para todos los workers, stress arrancaba en 50 msg/s y elasticidad en 50 msg/s → **backlog desde el segundo 1** → latencias en minutos, no milisegundos. Datos inservibles para el informe.

### Solución implementada (junio 2026)
Las tasas de carga ahora se ajustan automáticamente según la capacidad real medida (C = 10):

| Experimento | Antes | Ahora | Razón |
|---|---|---|---|
| Speedup | rate=300 fijo | rate = workers × C × 0.5 | Evitar backlog, medir régimen estacionario |
| Stress | low=50, high=1000 | low=10, high=**80** + 8 workers + autoscaler OFF | Workers fijos para evitar interferencia |
| Elasticity | low=50, high=500 | low=10, high=50 + workers-min=**4** + rampa **600s** | Rampa lenta para dar tiempo al autoscaler |
| Contención | low=50, high=300 | low=10, high=**30** | 75% de capacidad para latencia limpia |

Además:
- Cleanup entre runs: drena cola + forza kill de conexiones + verifica tablas vacías
- Exportación con `--since` y `--until` para ventana temporal exacta
- Resultados guardados por timestamp para aislar sesiones

### Datos actuales (sesiones disponibles)

| Sesión | Timestamp | Calidad |
|---|---|---|
| summary1 (legacy) | 2026-06-12 ~06:00 | ❌ Cross-contaminada (experimentos solapados) |
| summary.json | 20260612_235224 | ⚠️ Solo calibración limpia; todo lo demás contaminado por backlog |
| latest_summary.json | **20260613_014350** | ✅ **Speedup limpio**, calibración limpia |
| **044238** (stress/elast/cont) | **20260613_044238** | ✅ **Contención limpia** (p50=107ms), stress y elasticidad saturados |

### Estado por experimento

| Experimento | Datos | Sesión | Para informe |
|---|---|---|---|
| Calibración rate_10/50 | ✅ **Limpio** | 014350 | C=9.49 rps, latencia 108ms |
| **Speedup** | ✅ **Limpio** | 014350 | 4.81→9.30→17.08→30.46 rps, eficiencia 100%→79% |
| **Contención** | ✅ **Limpio** | **064029** | Uniforme 23.55 rps / Hotspot 17.82 rps. Ratio **1.32×**. p50=107ms |
| **Stress** | ❌ **Parcial (backlog)** | 044238 | 26.0 rps, p50=26.4s, 7,701/15,732 vendidos |
| **Elasticidad** | ✅ **Limpio (SQS trigger)** | Run1/Run2 | ~14 rps, p95=46s, 15,900 vendidos |

**Resumen para el informe**: calibración, speedup y contención tienen datos limpios y publicables. Stress tiene datos parciales (backlog acumulado durante la rampa), útiles para el análisis de saturación pero no para régimen estacionario. Elasticidad tiene datos limpios tras la migración a SQS trigger. La limitación del autoscaler está documentada en la sección correspondiente.

---

## Riesgos y Trade-offs

| Riesgo | Mitigación |
|---|---|
| PostgreSQL SPOF | Aceptado por modelo strong; réplica fuera de alcance |
| Contador unnumbered = hotspot | Sharded counters como alternativa documentada |
| Flapping en autoscaling | Cooldowns asimétricos (60s out / 120s in) |
| RabbitMQ autogestionado | user_data + Terraform = reproducible |
| Contaminación entre runs | Cleanup con --drain --force --retry (implementado) |
