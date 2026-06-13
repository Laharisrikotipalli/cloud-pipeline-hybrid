# ============================================================
# Terraform Variables
# ============================================================

# ----------------------------
# GCP Variables
# ----------------------------

variable "gcp_project_id" {
  description = "The Google Cloud project ID"
  type        = string
}

variable "gcp_region" {
  description = "The GCP region for resource deployment"
  type        = string
  default     = "us-central1"
}

variable "gcp_keyfile_path" {
  description = "Path to the GCP service account key JSON file"
  type        = string
  default     = "../keyfile.json"
}

variable "cloud_sql_user" {
  description = "Cloud SQL database user"
  type        = string
  default     = "pipeline_user"
}

variable "cloud_sql_password" {
  description = "Cloud SQL database password"
  type        = string
  sensitive   = true
}

variable "localstack_dynamodb_endpoint" {
  description = "Endpoint URL for LocalStack DynamoDB (used by Cloud Function)"
  type        = string
  default     = "http://localhost:4566"
}

# ----------------------------
# AWS / LocalStack Variables
# ----------------------------

variable "aws_region" {
  description = "AWS region (simulated by LocalStack)"
  type        = string
  default     = "us-east-1"
}

variable "s3_bucket_name" {
  description = "Name of the S3 bucket in LocalStack"
  type        = string
  default     = "hybrid-cloud-bucket"
}

variable "sqs_queue_name" {
  description = "Name of the SQS queue in LocalStack"
  type        = string
  default     = "data-processing-queue"
}

variable "dynamodb_table_name" {
  description = "Name of the DynamoDB table in LocalStack"
  type        = string
  default     = "processed-records"
}
