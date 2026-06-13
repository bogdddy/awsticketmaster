variable "project_name" {
  description = "Nombre del proyecto"
  type        = string
}

variable "ecs_cluster_name" {
  description = "Nombre del cluster ECS"
  type        = string
}

variable "ecs_service_name" {
  description = "Nombre del servicio ECS"
  type        = string
}

variable "rabbitmq_instance_id" {
  description = "Instance ID de RabbitMQ"
  type        = string
}

variable "postgres_instance_id" {
  description = "Instance ID de PostgreSQL"
  type        = string
}

variable "log_group_name" {
  description = "Nombre del log group de workers"
  type        = string
}
