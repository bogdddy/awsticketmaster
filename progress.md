# AWSTicket — Progress Tracker

## Environment
**AWS Academy Learner Lab** — $50 credit, LabRole + LabInstanceProfile.

## Architecture (restored for Learner Lab)

| Component | Implementation |
|---|---|
| IAM | LabRole + LabInstanceProfile (pre-existing) |
| Network | Default VPC (data source) + 4 custom Security Groups |
| RabbitMQ | EC2 t3.medium, AL2023 AMI, SSM parameters |
| PostgreSQL | EC2 t3.medium + 50GB EBS, AL2023 AMI, SSM parameters |
| Workers | ECS Fargate, own cluster, task definition, CloudWatch logs |
| ECR | Repository with lifecycle policy |
| Autoscaling | Lambda + EventBridge rule (1 min) + CloudWatch alarm |
| LoadGen | EC2 t3.small, AL2023 AMI |
| Storage | S3 bucket (results, versioning, encryption, lifecycle) |
| Observability | CloudWatch dashboard (metrics + logs) |

---

## Phases

### [x] Phase 1: Terraform Infrastructure
- [x] `main.tf` — default VPC, SGs, ECR, S3, EC2, ECS, Lambda, EventBridge
- [x] `variables.tf` / `outputs.tf` / `terraform.tfvars`
- [x] All 10 modules restored to functional state
- [ ] **terraform init + plan + apply pending**

### [x] Phase 2: Worker Application (Python -> ECR)
- [x] Worker Python app
- [x] Dockerfile
- [ ] Build & push to ECR

### [x] Phase 3: Load Generator
- [x] Z(t) workload generator

### [x] Phase 4: Autoscaling Controller
- [x] Lambda + EventBridge + CloudWatch alarm
- [x] RabbitMQ Management API poller

### [x] Phase 5: Observability
- [x] CloudWatch dashboard
- [x] Plot generation scripts

### [x] Phase 6: Experiments
- [x] `experiments/run_experiment.py`

### [x] Phase 7: Report
- [x] `analysis/report.md` + analysis scripts

---

## Current Status

**Infraestructura desplegada y operativa en Learner Lab.**

- [x] `terraform apply` completado
- [x] RabbitMQ instalado y configurado (vía packagecloud repos)
- [x] PostgreSQL instalado con schema y datos
- [x] Worker Docker image construida y subida a ECR
- [x] ECS service corriendo con `desiredCount=1`
- [x] Experimentos de calibración ejecutados (10, 50, 100 msg/s)
- [x] Script `cleanup.py` para reset entre experimentos

**Pendiente:**
- [ ] Ejecutar experimentos completos (speedup, stress, elasticity, contention)
- [ ] Generar plots finales
- [ ] Completar análisis en `report.md` con datos reales
