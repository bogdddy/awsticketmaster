resource "aws_instance" "loadgen" {
  ami                    = "ami-0521cb2d60cfbb1a6"
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.security_group_id]
  key_name               = var.key_name
  iam_instance_profile   = var.iam_instance_profile
  associate_public_ip_address = true

  user_data = templatefile("${path.module}/user_data.sh", {
    rabbitmq_host = var.rabbitmq_endpoint
    rabbitmq_user = var.rabbitmq_user
    rabbitmq_pass = var.rabbitmq_password
    project_name  = var.project_name
  })

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  tags = { Name = "${var.project_name}-loadgen" }
}
