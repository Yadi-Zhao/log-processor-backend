resource "aws_dynamodb_table" "logs" {
  name         = "${var.project_name}-logs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "processed_at"
    type = "S"
  }

  # Enable time-based queries within tenant scope
  global_secondary_index {
    name            = "ProcessedAtIndex"
    hash_key        = "PK"
    range_key       = "processed_at"
    projection_type = "ALL"
  }

  tags = {
    Name        = "${var.project_name}-logs"
    Environment = var.environment
  }
}