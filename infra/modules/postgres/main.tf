resource "aws_instance" "postgres" {
  ami                    = "ami-0521cb2d60cfbb1a6"
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  private_ip             = var.private_ip
  vpc_security_group_ids = [var.security_group_id]
  key_name               = var.key_name
  iam_instance_profile   = var.iam_instance_profile

  user_data = templatefile("${path.module}/user_data.sh", {
    db_name      = var.db_name
    db_user      = var.db_user
    db_password  = var.db_password
    project_name = var.project_name
    vpc_cidr     = var.vpc_cidr
  })

  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }

  ebs_block_device {
    device_name           = "/dev/xvdf"
    volume_type           = "gp3"
    volume_size           = 50
    encrypted             = true
    delete_on_termination = true
  }

  tags = {
    Name = "${var.project_name}-postgres"
  }
}

resource "aws_ssm_parameter" "postgres_endpoint" {
  name  = "/${var.project_name}/postgres-endpoint"
  type  = "String"
  value = aws_instance.postgres.private_ip
}

resource "aws_ssm_parameter" "postgres_user" {
  name  = "/${var.project_name}/postgres-user"
  type  = "SecureString"
  value = var.db_user
}

resource "aws_ssm_parameter" "postgres_password" {
  name  = "/${var.project_name}/postgres-password"
  type  = "SecureString"
  value = var.db_password
}

resource "aws_ssm_parameter" "postgres_db" {
  name  = "/${var.project_name}/postgres-db-name"
  type  = "String"
  value = var.db_name
}
