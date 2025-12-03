# Data source to automatically package ingestion Lambda
data "archive_file" "ingestion" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/ingestion"
  output_path = "${path.module}/../lambda-packages/ingestion.zip"
}

# Data source to automatically package worker Lambda
data "archive_file" "worker" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda/worker"
  output_path = "${path.module}/../lambda-packages/worker.zip"
}

# Ingestion Lambda - handles API requests
resource "aws_lambda_function" "ingestion" {
  filename         = data.archive_file.ingestion.output_path
  function_name    = "${var.project_name}-ingestion"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.11"
  timeout          = 30
  memory_size      = 256
  source_code_hash = data.archive_file.ingestion.output_base64sha256

  environment {
    variables = {
      SQS_QUEUE_URL = aws_sqs_queue.log_queue.url
    }
  }

  tags = {
    Name        = "${var.project_name}-ingestion"
    Environment = var.environment
  }
}

# Worker Lambda - processes queued messages
resource "aws_lambda_function" "worker" {
  filename         = data.archive_file.worker.output_path
  function_name    = "${var.project_name}-worker"
  role             = aws_iam_role.lambda_exec.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.11"
  timeout          = 900 # 15 minutes for long processing
  memory_size      = 512
  source_code_hash = data.archive_file.worker.output_base64sha256

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.logs.name
    }
  }

  # Reserve concurrency to prevent throttling under load
  # reserved_concurrent_executions = 100

  tags = {
    Name        = "${var.project_name}-worker"
    Environment = var.environment
  }
}

# Connect SQS to worker Lambda
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.log_queue.arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 10

  # Wait up to 5 seconds to collect more messages before triggering
  maximum_batching_window_in_seconds = 5

  # Allow up to 100 concurrent Lambda invocations
  scaling_config {
    maximum_concurrency = 100
  }
}

# Allow API Gateway to invoke ingestion Lambda
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingestion.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/*"
}