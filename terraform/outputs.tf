# ============================================================
# Terraform Outputs
# ============================================================

# ----------------------------
# LocalStack Outputs
# ----------------------------

output "s3_bucket_name" {
  description = "Name of the S3 bucket in LocalStack"
  value       = aws_s3_bucket.hybrid_bucket.bucket
}

output "sqs_queue_url" {
  description = "URL of the SQS queue in LocalStack"
  value       = aws_sqs_queue.data_processing_queue.url
}

output "sqs_dlq_url" {
  description = "URL of the SQS dead-letter queue in LocalStack"
  value       = aws_sqs_queue.data_processing_dlq.url
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table in LocalStack"
  value       = aws_dynamodb_table.processed_records.name
}

# ----------------------------
# GCP Outputs
# ----------------------------

output "pubsub_topic_id" {
  description = "ID of the GCP Pub/Sub topic"
  value       = google_pubsub_topic.localstack_events.id
}

output "cloud_sql_instance_connection_name" {
  description = "Cloud SQL instance connection name"
  value       = google_sql_database_instance.pipeline_instance.connection_name
}

output "cloud_sql_instance_name" {
  description = "Cloud SQL instance name"
  value       = google_sql_database_instance.pipeline_instance.name
}

output "cloud_sql_public_ip" {
  description = "Cloud SQL instance public IP address"
  value       = google_sql_database_instance.pipeline_instance.public_ip_address
}

output "cloud_function_name" {
  description = "Name of the deployed Cloud Function"
  value       = google_cloudfunctions_function.processor_function.name
}

output "cloud_function_https_trigger_url" {
  description = "HTTPS trigger URL of the Cloud Function"
  value       = google_cloudfunctions_function.processor_function.https_trigger_url
}
