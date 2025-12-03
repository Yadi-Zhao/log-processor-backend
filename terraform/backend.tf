terraform {
  backend "s3" {
    bucket = "log-processor-terraform-state-dev-523473407773"
    key    = "log-processor/terraform.tfstate"
    region = "us-east-1"
  }
}