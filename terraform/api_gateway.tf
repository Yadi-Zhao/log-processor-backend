# Create REST API
resource "aws_api_gateway_rest_api" "api" {
  name        = "${var.project_name}-api"
  description = "Log ingestion API for multi-tenant processing"

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# Create /ingest resource
resource "aws_api_gateway_resource" "ingest" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "ingest"
}

# Configure POST method
resource "aws_api_gateway_method" "post_ingest" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.ingest.id
  http_method   = "POST"
  authorization = "NONE"
}

# Integrate with Lambda using proxy mode
resource "aws_api_gateway_integration" "lambda_integration" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = aws_api_gateway_resource.ingest.id
  http_method = aws_api_gateway_method.post_ingest.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.ingestion.invoke_arn
}

# Deploy API to prod stage
resource "aws_api_gateway_deployment" "deployment" {
  depends_on = [
    aws_api_gateway_integration.lambda_integration
  ]

  rest_api_id = aws_api_gateway_rest_api.api.id

  # Force redeployment when integration changes
  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.ingest.id,
      aws_api_gateway_method.post_ingest.id,
      aws_api_gateway_integration.lambda_integration.id,
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Create prod stage
resource "aws_api_gateway_stage" "prod" {
  deployment_id = aws_api_gateway_deployment.deployment.id
  rest_api_id   = aws_api_gateway_rest_api.api.id
  stage_name    = "prod"
}