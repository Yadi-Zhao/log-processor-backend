resource "aws_sqs_queue" "log_queue" {
  name                       = "${var.project_name}-queue"
  visibility_timeout_seconds = 900     # Match Lambda timeout
  message_retention_seconds  = 1209600 # 14 days
  receive_wait_time_seconds  = 20      # Enable long polling

  # Retry failed messages up to 3 times before moving to DLQ
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name        = "${var.project_name}-queue"
    Environment = var.environment
  }
}

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.project_name}-dlq"
  message_retention_seconds = 1209600

  tags = {
    Name        = "${var.project_name}-dlq"
    Environment = var.environment
  }
}