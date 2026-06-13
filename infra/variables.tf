variable "project_name" {
  description = "Nombre del proyecto"
  type        = string
  default     = "awsticket"
}

variable "aws_region" {
  description = "Region AWS"
  type        = string
  default     = "us-east-1"
}

variable "aws_access_key_id" {
  description = "Access Key ID"
  type        = string
}

variable "aws_secret_access_key" {
  description = "Secret Access Key"
  type        = string
  sensitive   = true
}

variable "aws_session_token" {
  description = "Session Token"
  type        = string
  sensitive   = true
}

variable "vpc_cidr" {
  description = "CIDR de la VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR de la subred publica"
  type        = string
  default     = "10.0.1.0/24"
}

variable "key_name" {
  description = "Key pair EC2 (opcional)"
  type        = string
  default     = null
}

variable "rabbitmq_instance_type" {
  description = "Tipo de instancia para RabbitMQ"
  type        = string
  default     = "t3.medium"
}

variable "postgres_instance_type" {
  description = "Tipo de instancia para PostgreSQL"
  type        = string
  default     = "t3.medium"
}

variable "loadgen_instance_type" {
  description = "Tipo de instancia para load generator"
  type        = string
  default     = "t3.small"
}

variable "rabbitmq_private_ip" {
  description = "IP privada fija para RabbitMQ"
  type        = string
  default     = "10.0.1.10"
}

variable "postgres_private_ip" {
  description = "IP privada fija para PostgreSQL"
  type        = string
  default     = "10.0.1.20"
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
  default     = "ticketapp"
}

variable "postgres_password" {
  description = "Contrasena PostgreSQL"
  type        = string
  sensitive   = true
}

variable "postgres_db_name" {
  description = "Base de datos"
  type        = string
  default     = "ticketdb"
}

variable "worker_cpu" {
  description = "CPU Fargate (512 = 0.5 vCPU)"
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Memoria Fargate (MB)"
  type        = number
  default     = 1024
}

variable "worker_desired_count" {
  description = "Numero inicial de workers"
  type        = number
  default     = 1
}

variable "worker_min_count" {
  description = "Min workers (autoscaling)"
  type        = number
  default     = 1
}

variable "worker_max_count" {
  description = "Max workers (autoscaling)"
  type        = number
  default     = 20
}

variable "target_backlog_per_worker" {
  description = "Backlog objetivo por worker"
  type        = number
  default     = 10
}
