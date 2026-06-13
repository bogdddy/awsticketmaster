variable "project_name" {
  description = "Nombre del proyecto"
  type        = string
}

variable "subnet_id" {
  description = "Subred publica para loadgen"
  type        = string
}

variable "security_group_id" {
  description = "Security Group del loadgen"
  type        = string
}

variable "instance_type" {
  description = "Tipo de instancia EC2"
  type        = string
}

variable "key_name" {
  description = "Key pair EC2 (opcional)"
  type        = string
  default     = null
}

variable "iam_instance_profile" {
  description = "Instance profile IAM"
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


