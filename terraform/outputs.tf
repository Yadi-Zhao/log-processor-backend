output "api_gateway_url" {
  description = "Public API endpoint URL"
  value       = "${aws_api_gateway_stage.prod.invoke_url}/ingest"
}

output "dynamodb_table_name" {
  description = "DynamoDB table for log storage"
  value       = aws_dynamodb_table.logs.name
}

output "sqs_queue_url" {
  description = "SQS queue URL for message processing"
  value       = aws_sqs_queue.log_queue.url
}

output "sqs_dlq_url" {
  description = "Dead letter queue URL for failed messages"
  value       = aws_sqs_queue.dlq.url
}

output "ingestion_lambda_name" {
  description = "Ingestion Lambda function name"
  value       = aws_lambda_function.ingestion.function_name
}

output "worker_lambda_name" {
  description = "Worker Lambda function name"
  value       = aws_lambda_function.worker.function_name
}