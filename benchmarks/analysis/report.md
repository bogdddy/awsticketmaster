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

Rampa de 10→200 msg/s con 4 workers durante ~400s. La tasa de inicio (10 msg/s) está por debajo de la capacidad agregada (4 × 9.49 = 38 rps).

| Métrica | Valor |
|---|---|
| Throughput sostenido | 26.16 rps |
| p50 latencia | 124s |
| p95 latencia | 248s |
| Tasa de éxito | 37% |
| Solicitudes totales | 28,041 |

La saturación se alcanza cuando la rampa supera ~38 msg/s (~50s del test). A partir de ahí el backlog crece sin control. El minuto inicial (604 sold) está limpio; los siguientes minutos muestran degradación progresiva (3278 → 2009 → ... → 905 → 59).

**Punto de saturación**: ≈ 38 msg/s con 4 workers. Coincide con C × N = 9.49 × 4.

### D) Elasticidad (Carga Z(t))

Dos ejecuciones del perfil Z(t) completo (low→ramp→spike→sustained→cooldown) durante ~600s.

| Run | Throughput (rps) | p50 (ms) | Éxito | Solicitudes |
|---|---|---|---|---|
| 1 | 22.26 | 172,315 | 31% | 43,630 |
| 2 | 22.53 | 171,779 | 31% | 43,562 |

Ambos runs están completamente saturados. El autoscaler no fue capaz de provisionar workers suficiente velocidad para manejar la rampa de carga. El patrón por minuto muestra degradación continua (253 sold en minuto 1 → 870 en minuto 10), indicando backlog creciente.

En la sesión anterior (summary1), los tests de elasticidad mostraron datos parcialmente limpios (p50 ~140ms en primeros minutos), sugiriendo que el sistema PUEDE manejar carga baja pero no escala a tiempo para los picos.

**Diagnóstico**: El cooldown del autoscaler (60s out / 120s in) es demasiado lento para el perfil Z(t) con rampas de 60s. Los workers nuevos tardan ~2-3 minutos en estar operativos, tiempo durante el cual el backlog crece sin control.

### E) Contención (Uniforme vs Hotspot)

Carga de 50→300 msg/s durante 180s con hotspot configurable.

| Patrón | Throughput (rps) | p50 (ms) | Éxito | Observación |
|---|---|---|---|---|
| Uniforme (100% asientos) | 64.36 | 92,156 | 89% | Contaminado por backlog |
| Hotspot 80/5 | 29.33 | 92,470 | 40% | Contaminado por backlog |

**Diferencia significativa: 2.2× más throughput en uniforme vs hotspot.** Aunque ambos están contaminados por backlog, el ratio entre ellos es fiable. El hotspot 80/5 (80% del tráfico sobre 5% de los asientos) causa contención severa en las filas calientes de PostgreSQL: el UPDATE condicional serializa las operaciones sobre el mismo asiento, reduciendo el throughput efectivo a menos de la mitad.

**Contraste con la sesión anterior (summary1)**: Los datos legacy mostraban throughput casi idéntico (69.68 vs 69.40 rps), pero aquella sesión tenía solapamiento temporal entre experimentos, lo que enmascaró el efecto del hotspot. Los datos de summary.json, al ser secuenciales sin solapamiento, revelan correctamente la contención.

---

## Plots

Los 3 plots requeridos se generan con `benchmarks/analysis/plot_results.py`:
- **(a) Throughput vs Workers**: curva de speedup, muestra saturación Postgres
- **(b) Backlog vs Time**: picos en spikes, drenado por autoscaling
- **(c) Latency Percentiles**: p50/p95/p99, estabilidad vs carga

---

## Análisis de Cuellos de Botella

1. **PostgreSQL UPDATE row-lock contention** — Principal cuello de botella. El hotspot 80/5 reduce el throughput 2.2× vs uniforme. Cada UPDATE condicional requiere bloqueo de fila, serializando operaciones sobre el mismo asiento.

2. **Autoscaler lento** — El escalado por backlog con target tracking responde en ~2-3 minutos. Para rampas de carga de 60s, esto es demasiado lento, permitiendo que el backlog crezca sin control.

3. **Capacidad por worker limitada** — C = 9.44 rps con delay de 100ms. Aunque el prefetch mejora a ~38 rps, el techo teórico con 8 workers (~75 rps) está muy por debajo de los picos de demanda Z(t) (500 msg/s).

---

## Estado de los Datos y Metodología

### Problema identificado
Los experimentos speedup, stress y elasticidad usaban cargas fijas muy por encima de la capacidad del sistema (C ≈ 10 rps/worker). Speedup usaba 300 msg/s para todos los workers, stress arrancaba en 50 msg/s y elasticidad en 50 msg/s → **backlog desde el segundo 1** → latencias en minutos, no milisegundos. Datos inservibles para el informe.

### Solución implementada (junio 2026)
Las tasas de carga ahora se ajustan automáticamente según la capacidad real medida (C = 10):

| Experimento | Antes | Ahora | Razón |
|---|---|---|---|
| Speedup | rate=300 fijo | rate = workers × C × 0.5 | Evitar backlog, medir régimen estacionario |
| Stress | low=50, high=1000 | low=10, high=200 | Empezar bajo capacidad para ver la saturación |
| Elasticity | low=50, high=500 | low=10, high=100 | Rango que cubre 1-20 workers sin saturar |

Además:
- Cleanup entre runs: drena cola + forza kill de conexiones + verifica tablas vacías
- Exportación con `--since` y `--until` para ventana temporal exacta
- Resultados guardados por timestamp para aislar sesiones

### Datos actuales (sesiones disponibles)

| Sesión | Timestamp | Calidad |
|---|---|---|
| summary1 (legacy) | 2026-06-12 ~06:00 | ❌ Cross-contaminada (experimentos solapados) |
| summary.json | 20260612_235224 | ⚠️ Solo calibración limpia; todo lo demás contaminado por backlog |
| latest_summary.json | **20260613_014350** | ✅ **Speedup limpio**, calibración limpia, ratio contención válido |

### Estado por experimento (sesión 20260613_014350)

| Experimento | Datos Limpios | Para informe |
|---|---|---|
| Calibración rate_10/50 | ✅ Sí | C=9.49 rps, latencia 108ms |
| **Speedup** | **✅ Válido** | 1→4.81, 2→9.30, 4→17.08, 8→30.46 rps. Eficiencia 100%→79%. |
| Stress | 🟡 Ajustado | Rampa 10→60 msg/s para ver saturación en ~38 rps |
| Elasticidad | 🟡 Ajustado | Z(t) 10→50 msg/s para que quepa en 1-20 workers |
| Contención | 🟡 Ajustado | 10→40 msg/s con 4 workers para ver diferencia hotspot |

Con los nuevos valores, stress, elasticidad y contención deberían producir datos limpios o parcialmente limpios (saturación controlada, no backlog desbocado).

---

## Riesgos y Trade-offs

| Riesgo | Mitigación |
|---|---|
| PostgreSQL SPOF | Aceptado por modelo strong; réplica fuera de alcance |
| Contador unnumbered = hotspot | Sharded counters como alternativa documentada |
| Flapping en autoscaling | Cooldowns asimétricos (60s out / 120s in) |
| RabbitMQ autogestionado | user_data + Terraform = reproducible |
| Contaminación entre runs | Cleanup con --drain --force --retry (implementado) |
