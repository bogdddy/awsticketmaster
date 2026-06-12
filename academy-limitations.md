# AWS Academy — Limitaciones y adaptaciones

## Entorno: Learner Lab ($50 credit)

El Learner Lab con crédito proporciona permisos amplios. La mayoría de servicios AWS
funcionan sin restricciones. Solo se usa LabRole/LabInstanceProfile por simplicidad
en vez de crear roles IAM custom.

### APIs que funcionan (verificado)

| API | Recurso |
|---|---|
| `ec2:RunInstances` | EC2 instances |
| `ec2:CreateSecurityGroup` | Security groups |
| `ec2:CreateVpc` | VPC propia |
| `ec2:CreateSubnet` | Subnets |
| `ec2:CreateInternetGateway` | IGW |
| `ecs:CreateCluster` | ECS cluster |
| `ecs:RegisterTaskDefinition` | Task definition |
| `ecs:CreateService` | ECS service |
| `ecr:CreateRepository` | ECR repository |
| `ssm:PutParameter` | SSM parameters |
| `logs:CreateLogGroup` | CloudWatch log group |
| `events:PutRule` / `events:PutTargets` | EventBridge (sin tags) |
| `cloudwatch:PutDashboard` | CloudWatch dashboard |
| `s3:CreateBucket` | S3 bucket |
| `lambda:CreateFunction` | Lambda function |
| `iam:PassRole` | LabRole pass to services |
| `sts:GetCallerIdentity` | Account identity |

### APIs que NO funcionan

| API | Workaround |
|---|---|
| `events:TagResource` | No poner `tags` en `aws_cloudwatch_event_rule` |
| `cloudwatch:PutMetricAlarm` | No usar `aws_cloudwatch_metric_alarm`; el Lambda escala directamente |

### Adaptaciones respecto al diseño original del roadmap

| Componente | Roadmap original | Learner Lab |
|---|---|---|
| IAM | 5 roles custom + policies | LabRole + LabInstanceProfile |
| VPC | VPC nueva con NAT Gateways | VPC propia sin NAT (subred publica) |
| Workers IP | `assign_public_ip = false` | `assign_public_ip = true` (sin NAT) |
| EventBridge | Rule con tags | Rule sin tags |
| CloudWatch | Metric Alarm + target tracking | Lambda escala directamente via API |
| Todo lo demas | Igual | Igual |

### Justificación para la memoria

1. **LabRole en vez de roles custom**: El Learner Lab proporciona LabRole con permisos
   broad. Crear roles adicionales es redundante y consume tiempo del despliegue. Se
   documenta que en producción se usarían roles con mínimo privilegio.

2. **VPC propia sin NAT**: Se crea una VPC dedicada con una subred pública e IGW.
   Los workers Fargate usan IP pública para acceder a ECR. En producción se usaría
   VPC privada + NAT o VPC endpoints.

3. **Workers con IP pública**: Sin NAT Gateways, los workers Fargate necesitan IP
   pública para acceder a ECR/Docker Hub. En producción se usaría VPC privada + NAT.

4. **EventBridge sin tags**: El Learner Lab no permite `events:TagResource`. Se
   omiten los tags en las reglas EventBridge.

5. **Sin CloudWatch Alarms**: El Learner Lab no permite `cloudwatch:PutMetricAlarm`.
   El Lambda controlador escala directamente via `ecs:UpdateService` en vez de
   depender de alarmas + target tracking policies.
