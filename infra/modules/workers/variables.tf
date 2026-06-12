variable "project_name" {
  description = "Nombre del proyecto"
  type        = string
}

variable "subnet_ids" {
  description = "Subredes para workers"
  type        = list(string)
}

variable "security_group_id" {
  description = "Security Group de workers"
  type        = string
}

variable "lab_role_arn" {
  description = "ARN del LabRole"
  type        = string
}

variable "ecr_repository_url" {
  description = "URL del repositorio ECR"
  type        = string
}

variable "rabbitmq_endpoint" {
  description = "IP de RabbitMQ"
  type        = string
}

variable "postgres_endpoint" {
  description = "IP de PostgreSQL"
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

variable "postgres_user" {
  description = "Usuario PostgreSQL"
  type        = string
  sensitive   = true
}

variable "postgres_password" {
  description = "Contrasena PostgreSQL"
  type        = string
  sensitive   = true
}

variable "postgres_db_name" {
  description = "Base de datos"
  type        = string
}

variable "worker_cpu" {
  description = "CPU Fargate"
  type        = number
}

variable "worker_memory" {
  description = "Memoria Fargate"
  type        = number
}

variable "worker_desired_count" {
  description = "Numero inicial de workers"
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

variable "aws_region" {
  description = "Region de AWS"
  type        = string
}
