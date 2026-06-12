# Deploy Guide — AWSTicket (AWS Academy Learner Lab)

## 1. Prerrequisitos

| Herramienta | Version | Como verificar |
|---|---|---|
| AWS CLI | >= 2.x | `aws --version` |
| Terraform | >= 1.5 | `terraform --version` |
| Docker | >= 24.0 | `docker --version` |
| Python | >= 3.11 | `python --version` |

**Credenciales del Learner Lab:**
1. Inicia tu Learner Lab desde AWS Academy
2. Copia Access Key, Secret Key y Session Token de "AWS Details"
3. Edita `infra/terraform.tfvars` y pega las credenciales

---

## 2. Configurar variables

Edita `infra/terraform.tfvars`:

```hcl
aws_access_key_id     = "<ACCESS_KEY>"
aws_secret_access_key = "<SECRET_KEY>"
aws_session_token     = "<SESSION_TOKEN>"

ami_id = "<AMI_ID>"

rabbitmq_user     = "admin"
rabbitmq_password = "tu-password-seguro"

postgres_password = "otra-password-segura"
```

Si las credenciales expiran, exporta las nuevas:

```bash
export AWS_ACCESS_KEY_ID="NUEVA_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="NUEVA_SECRET_KEY"
export AWS_SESSION_TOKEN="NUEVO_SESSION_TOKEN"
export AWS_DEFAULT_REGION="us-east-1"
```

---

## 3. Desplegar infraestructura

```bash
cd infra
terraform init
terraform plan
terraform apply -auto-approve
```

**Duración:** 8-12 minutos.

**Outputs:**
```
rabbitmq_private_ip  = 10.0.1.10
postgres_private_ip  = 10.0.1.20
ecr_repository_url   = <account>.dkr.ecr.us-east-1.amazonaws.com/awsticket/worker
s3_bucket_id         = awsticket-results-<account>
ecs_cluster_name     = awsticket-cluster
ecs_service_name     = awsticket-worker-svc
```

---

## 4. Build & Push imagen worker a ECR

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ECR_URL>

cd worker
docker build -t awsticket/worker .
docker tag awsticket/worker:latest <ECR_URL>/awsticket/worker:latest
docker push <ECR_URL>/awsticket/worker:latest

aws ecs update-service --cluster awsticket-cluster --service awsticket-worker-svc --force-new-deployment --region us-east-1
```

---

## 5. Verificar servicios

```bash
aws ec2 describe-instances \
  --query "Reservations[*].Instances[*].{ID:InstanceId,Name:Tags[?Key=='Name'].Value | [0]}" \
  --output table \
  --region us-east-1
```

### RabbitMQ

```bash
aws ssm start-session --target <rabbitmq-instance-id> --region us-east-1

sudo systemctl status rabbitmq-server
sudo rabbitmqctl list_queues

curl -u admin:<password> http://localhost:15672/api/queues
```

### PostgreSQL

```bash
aws ssm start-session --target <postgres-instance-id> --region us-east-1

sudo -u postgres psql -d ticketdb -c "SELECT COUNT(*) FROM seats;"
sudo -u postgres psql -d ticketdb -c "SELECT * FROM events;"
```

---

## 6. Benchmarks

Desde la instancia **loadgen** (conectada por SSM):

```bash
# Instalar dependencias
pip3 install pika psycopg2-binary

# Dar permisos a ticketapp en PostgreSQL (desde instancia postgres)
sudo -u postgres psql -d ticketdb -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ticketapp;"

# Navegar al directorio de benchmarks
cd /path/to/awsticket/benchmarks
```

### Opción A: Script automático (recomendado)

Ejecuta todos los benchmarks con cleanup automático entre runs:

```bash
chmod +x run_all_benchmarks.sh
./run_all_benchmarks.sh
```

El script:
- Ejecuta todos los benchmarks en orden (calibration, speedup, stress, elasticity, contention)
- Hace cleanup automático entre runs (purga RabbitMQ y PostgreSQL)
- Guarda resultados en `benchmark_results/<experimento>/<run>/`
- Pausa en speedup para escalar workers manualmente

**Nota:** Para speedup, abre otra terminal y escala workers:

```bash
aws ecs update-service \
  --cluster awsticket-cluster \
  --service awsticket-worker-svc \
  --desired-count N \
  --region us-east-1
```

### Opción B: Ejecución manual

```bash
# A) Calibración
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type calibration \
  --rates 10,50,100 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

# B) Speedup
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type speedup \
  --workers 1,2,4,8 \
  --rate 300 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

# C) Stress
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type stress \
  --workers 4 \
  --max-rate 1000 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

# D) Elasticidad
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type elasticity \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10

# E) Contención
PYTHONPATH=../loadgen python3 run_experiment.py \
  --type contention \
  --hotspot-pct 5 \
  --hotspot-traffic 80 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd \
  --rabbitmq-host 10.0.1.10
```

**Limpiar entre benchmarks:**

```bash
python3 cleanup.py \
  --rabbitmq-host 10.0.1.10 \
  --pg-host 10.0.1.20 \
  --pg-user ticketapp \
  --pg-password ddd
```

### Estructura de resultados

Los benchmarks generan dos tipos de directorios:

- `results/` - CSVs del último benchmark ejecutado (sobrescribe en cada run)
- `benchmark_results/` - Archivo histórico de todos los benchmarks

```
benchmark_results/
├── calibration/
│   ├── rate_10/
│   │   ├── summary.csv
│   │   ├── throughput_by_minute.csv
│   │   └── results.csv
│   ├── rate_50/
│   └── rate_100/
├── speedup/
│   ├── workers_1/
│   └── ...
├── stress/
├── elasticity/
└── contention/
```

---

## 7. Autoscaling (automático con EventBridge)

El Lambda se ejecuta cada 60s automáticamente. Verificar en CloudWatch Logs:

```bash
aws logs tail /aws/lambda/awsticket-scaling-controller --follow --region us-east-1
```

---

## 8. Resultados y plots

Los benchmarks generan CSVs en `results/` (último run) y archivan todo en `benchmark_results/`.

**Generar plots desde benchmark_results:**

```bash
cd analysis

# Opción 1: Plot de un benchmark específico
python3 plot_results.py \
  --input-dir ../benchmarks/benchmark_results/calibration/rate_10 \
  --output-dir ./plots/calibration_rate_10

# Opción 2: Plot del último benchmark ejecutado
python3 plot_results.py \
  --input-dir ../benchmarks/results \
  --output-dir ./plots/latest

# Opción 3: Generar todos los plots de benchmark_results
python3 plot_results.py \
  --input-dir ../benchmarks/benchmark_results \
  --output-dir ./plots
```

**Exportar a S3 (opcional):**

```bash
export POSTGRES_HOST="10.0.1.20"
export POSTGRES_PASS="ddd"
export POSTGRES_USER="ticketapp"

python3 export_results.py --s3-bucket awsticket-results-<account>
```

---

## 9. Dashboard CloudWatch

```
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=awsticket-dashboard
```

---

## 10. Limpieza

```bash
cd infra
terraform destroy -auto-approve
```

---

## Checklist

- [ ] 1. `terraform.tfvars` con credenciales y AMI
- [ ] 2. `terraform init` + `apply`
- [ ] 3. Docker build + push a ECR
- [ ] 4. Force new deployment ECS
- [ ] 5. Verificar RabbitMQ + PostgreSQL
- [ ] 6. Ejecutar benchmarks (`run_all_benchmarks.sh` o manual)
- [ ] 7. Generar plots desde `benchmark_results/`
- [ ] 8. Dashboard CloudWatch
- [ ] 9. `terraform destroy`
