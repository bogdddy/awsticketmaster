resource "aws_ecs_cluster" "main" {
  name = "${var.project_name}-cluster"
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.project_name}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = var.lab_role_arn
  task_role_arn            = var.lab_role_arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = "${var.ecr_repository_url}:latest"
      essential = true

      environment = [
        { name = "RABBITMQ_HOST",     value = var.rabbitmq_endpoint },
        { name = "RABBITMQ_PORT",     value = "5672" },
        { name = "RABBITMQ_USER",     value = var.rabbitmq_user },
        { name = "RABBITMQ_PASS",     value = var.rabbitmq_password },
        { name = "RABBITMQ_PREFETCH", value = "10" },
        { name = "POSTGRES_HOST",     value = var.postgres_endpoint },
        { name = "POSTGRES_PORT",     value = "5432" },
        { name = "POSTGRES_DB",       value = var.postgres_db_name },
        { name = "POSTGRES_USER",     value = var.postgres_user },
        { name = "POSTGRES_PASS",     value = var.postgres_password },
        { name = "WORKER_ID",              value = "${var.project_name}-worker" },
        { name = "SQS_QUEUE_URL",          value = var.sqs_queue_url },
        { name = "AWS_REGION",             value = var.aws_region },
        { name = "MAX_RETRIES",            value = "3" },
        { name = "RETRY_BACKOFF_BASE_S",   value = "1" },
        { name = "RETRY_BACKOFF_MAX_S",    value = "30" },
        { name = "RABBITMQ_EXCHANGE",      value = "tickets" },
        { name = "RABBITMQ_ROUTING_KEY",   value = "buy" },
      ]

      log_configuration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = "/ecs/${var.project_name}/worker"
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "worker"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "worker" {
  name            = "${var.project_name}-worker-svc"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [var.security_group_id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.project_name}/worker"
  retention_in_days = 14
}
