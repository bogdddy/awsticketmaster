resource "aws_instance" "rabbitmq" {
  ami                    = "ami-0521cb2d60cfbb1a6"
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  private_ip             = var.private_ip
  vpc_security_group_ids = [var.security_group_id]
  key_name               = var.key_name
  iam_instance_profile   = var.iam_instance_profile

  user_data = templatefile("${path.module}/user_data.sh", {
    rabbitmq_user     = var.rabbitmq_user
    rabbitmq_password = var.rabbitmq_password
    project_name      = var.project_name
  })

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  tags = {
    Name = "${var.project_name}-rabbitmq"
  }
}

resource "aws_ssm_parameter" "rabbitmq_endpoint" {
  name  = "/${var.project_name}/rabbitmq-endpoint"
  type  = "String"
  value = aws_instance.rabbitmq.private_ip
}

resource "aws_ssm_parameter" "rabbitmq_user" {
  name  = "/${var.project_name}/rabbitmq-user"
  type  = "SecureString"
  value = var.rabbitmq_user
}

resource "aws_ssm_parameter" "rabbitmq_password" {
  name  = "/${var.project_name}/rabbitmq-password"
  type  = "SecureString"
  value = var.rabbitmq_password
}
