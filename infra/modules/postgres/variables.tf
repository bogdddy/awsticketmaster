variable "project_name" {
  description = "Nombre del proyecto"
  type        = string
}

variable "subnet_id" {
  description = "Subred para PostgreSQL"
  type        = string
}

variable "security_group_id" {
  description = "Security Group de PostgreSQL"
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

variable "db_name" {
  description = "Nombre de la base de datos"
  type        = string
}

variable "db_user" {
  description = "Usuario BD"
  type        = string
}

variable "db_password" {
  description = "Contrasena BD"
  type        = string
  sensitive   = true
}

variable "vpc_cidr" {
  description = "CIDR de la VPC"
  type        = string
}

variable "private_ip" {
  description = "IP privada fija para PostgreSQL"
  type        = string
}
