resource "aws_appautoscaling_target" "ecs" {
  max_capacity       = var.worker_max_count
  min_capacity       = var.worker_min_count
  resource_id        = "service/${var.ecs_cluster_name}/${var.ecs_service_name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_lambda_function" "controller" {
  function_name = "${var.project_name}-scaling-controller"
  role          = var.lab_role_arn
  handler       = "controller.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 128
  publish       = true

  filename         = data.archive_file.controller.output_path
  source_code_hash = data.archive_file.controller.output_base64sha256

  reserved_concurrent_executions = 1

  environment {
    variables = {
      RABBITMQ_HOST  = var.rabbitmq_endpoint
      RABBITMQ_PORT  = "15672"
      RABBITMQ_USER  = var.rabbitmq_user
      RABBITMQ_PASS  = var.rabbitmq_password
      ECS_CLUSTER    = var.ecs_cluster_name
      ECS_SERVICE    = var.ecs_service_name
      PROJECT_NAME   = var.project_name
      TARGET_BACKLOG = tostring(var.target_backlog_per_worker)
      WORKER_MIN     = tostring(var.worker_min_count)
      WORKER_MAX     = tostring(var.worker_max_count)
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [var.security_group_id]
  }
}

data "archive_file" "controller" {
  type        = "zip"
  source_file = "${path.module}/controller.py"
  output_path = "${path.module}/controller_payload.zip"
}

resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = var.sqs_queue_arn
  function_name    = aws_lambda_function.controller.arn
  batch_size       = 1
  enabled          = true
}

resource "aws_lambda_permission" "allow_sqs" {
  statement_id  = "AllowSQS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.controller.function_name
  principal     = "sqs.amazonaws.com"
  source_arn    = var.sqs_queue_arn
}
