variable "project_name" {
  description = "Nombre del proyecto"
  type        = string
}

variable "rabbitmq_endpoint" {
  description = "IP de RabbitMQ"
  type        = string
}

variable "rabbitmq_user" {
  description = "Usuario RabbitMQ"
  type        = string
  sensitive   = true
}

variable "rabbitmq_password" {
  description = "Contrasena RabbitMQ"
  type        = string
  sensitive   = true
}

variable "ecs_cluster_name" {
  description = "Nombre del cluster ECS"
  type        = string
}

variable "ecs_service_name" {
  description = "Nombre del servicio ECS"
  type        = string
}

variable "lab_role_arn" {
  description = "ARN del LabRole"
  type        = string
}

variable "subnet_ids" {
  description = "Subredes para la Lambda"
  type        = list(string)
}

variable "security_group_id" {
  description = "Security Group para Lambda"
  type        = string
}

variable "target_backlog_per_worker" {
  description = "Backlog objetivo por worker"
  type        = number
}

variable "worker_min_count" {
  description = "Min workers"
  type        = number
}

variable "worker_max_count" {
  description = "Max workers"
  type        = number
}

variable "sqs_queue_arn" {
  description = "ARN de la cola SQS para scaling trigger"
  type        = string
}
