variable "aws_region" {
  description = "AWS deployment region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project identifier for resource naming"
  type        = string
  default     = "log-processor"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"
}