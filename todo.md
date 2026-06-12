# TODO — Fixes de Performance

## Problemas identificados en benchmarks

### 1. Backlog contamina resultados entre runs
**Síntoma:** Latencias de minutos en vez de milisegundos. Ej: calibration rate_50 con p50=37.9s cuando debería ser <2s.

**Causa raíz:** El loadgen envía mensajes más rápido de lo que el worker consume. Los mensajes se acumulan en RabbitMQ. Cuando el test termina, el cleanup purga la cola, pero los mensajes que *ya se enviaron pero no se procesaron* no se reflejan en los resultados.

**Solución implementada:**
- `run_experiment.py` espera a que la cola de RabbitMQ se vacíe (workers terminen de consumir) antes de exportar resultados
- `export_results` ahora recibe `--since` y `--until` para filtrar por ventana temporal exacta
- `cleanup.py` drena la cola antes de purgar y verifica que quede vacía

**Archivos:** `benchmarks/cleanup.py`, `benchmarks/run_experiment.py`

---

### 2. Contención sin diferencia significativa
**Síntoma:** Uniforme (69.68 rps) ≈ Hotspot 80/5 (69.40 rps). No hay diferencia.

**Causa raíz:** El backlog acumulado enmascara la contención real. Los workers procesan mensajes viejos de la cola en vez de competir por asientos calientes en tiempo real.

**Solución propuesta:**
1. Asegurar cola vacía antes de iniciar contention
2. Verificar que workers estén consumiendo activamente
3. Reducir duración del test o aumentar tasa de envío

---

### 3. Speedup sospechosamente lineal
**Síntoma:** 1→8.64, 2→17.14, 4→33.88, 8→64.54 rps (7.47x con 8 workers).

**Causa raíz:** Los datos incluyen procesamiento de backlog acumulado, no solo tráfico en tiempo real. El speedup lineal es poco realista para un sistema con PostgreSQL como bottleneck.

**Solución propuesta:**
- Medir throughput solo durante estado estacionario (ignorar primeros 30s)
- Verificar que no haya backlog al inicio de cada run de speedup

---

### 4. Stress contaminado con datos de speedup
**Síntoma:** Los minutos 06:23-06:25 del stress tienen exactamente los mismos valores que speedup workers_8.

**Causa raíz:** Los tests se ejecutaron en horarios consecutivos y el cleanup no fue suficiente para separar los datos.

**Solución implementada:**
- `run_experiment.py` ahora pasa `--until experiment_end` a `export_results`, filtrando estrictamente la ventana temporal de cada experimento
- `cleanup.py` mata TODAS las conexiones worker de PostgreSQL antes de limpiar (flag `--force`)
- `cleanup.py` verifica que las tablas queden vacías después del reset y reintenta si falla (flag `--retry`)

**Archivos:** `benchmarks/cleanup.py`, `benchmarks/run_experiment.py`

---

### 5. Cleanup no maneja transacciones bloqueadas
**Síntoma:** El cleanup se cuelga cuando hay transacciones `idle in transaction` en PostgreSQL.

**Causa raíz:** `cleanup.py` intenta TRUNCATE/DELETE que quedan bloqueados por transacciones abiertas del worker.

**Solución implementada:**
- `cleanup.py --force` mata TODAS las conexiones del usuario `ticketapp` (no solo idle-in-transaction)
- `cleanup.py --retry N` reintenta si la verificación post-cleanup falla
- `cleanup.py --drain` espera a que la cola RabbitMQ se vacíe antes de limpiar

**Archivos:** `benchmarks/cleanup.py`, `benchmarks/run_all_benchmarks.sh`

---

### 6. Plots sin interpretación automática
**Síntoma:** Los plots se generan pero no incluyen análisis (líneas de tendencia, puntos de saturación, anotaciones).

**Solución propuesta:**
```python
# En plot_results.py: agregar anotaciones automáticas
# - Línea de speedup ideal (lineal)
# - Punto de saturación en stress
# - Regiones de interés en elasticity (spikes, cooldown)
```

**Archivo:** `analysis/plot_results.py`

---

## Estado actual

- ✅ 1. **Backlog entre runs** — Fix implementado (drain queue + --since/--until)
- ✅ 2. **Cleanup se cuelga** — Fix implementado (--force mata conexiones + --retry)
- ✅ 3. **Stress contaminado** — Fix implementado (--until + verify_clean)
- 🟡 4. **Contención sin efecto** — Pendiente de verificar con nueva metodología
- 🟢 5. **Plots con anotaciones** — Mejora estética, baja prioridad

---

## Setup mínimo para re-ejecutar

```bash
# 1. Reconstruir worker (si hay cambios)
cd worker
docker build -t awsticket/worker:latest .
docker tag awsticket/worker:latest 296742169132.dkr.ecr.us-east-1.amazonaws.com/awsticket/worker:latest
docker push 296742169132.dkr.ecr.us-east-1.amazonaws.com/awsticket/worker:latest

# 2. Redeploy
aws ecs update-service --cluster awsticket-cluster --service awsticket-worker-svc --desired-count 1 --force-new-deployment --region us-east-1

# 3. Esperar 2 min
# 4. Conectarse a loadgen y ejecutar:
cd /usr/bin/awsticketmaster/benchmarks
./run_all_benchmarks.sh

# 5. Copiar resultados
python3 collect_results.py
# Luego copiar benchmark_results/ y results.json a máquina local
```
