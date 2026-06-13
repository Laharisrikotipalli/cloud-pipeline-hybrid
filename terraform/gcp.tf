

resource "google_project_service" "pubsub_api" {
  service            = "pubsub.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "sqladmin_api" {
  service            = "sqladmin.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudfunctions_api" {
  service            = "cloudfunctions.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbuild_api" {
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "storage_api" {
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager_api" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}


resource "google_pubsub_topic" "localstack_events" {
  name    = "localstack-events"
  project = var.gcp_project_id

  message_retention_duration = "86400s"

  depends_on = [google_project_service.pubsub_api]

  labels = {
    environment = "dev"
    project     = "hybrid-cloud-pipeline"
  }
}

resource "google_pubsub_topic" "localstack_events_dlq" {
  name    = "localstack-events-dlq"
  project = var.gcp_project_id

  depends_on = [google_project_service.pubsub_api]
}

resource "google_pubsub_subscription" "localstack_events_sub" {
  name    = "localstack-events-subscription"
  topic   = google_pubsub_topic.localstack_events.name
  project = var.gcp_project_id

  message_retention_duration = "86400s"
  ack_deadline_seconds       = 60

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.localstack_events_dlq.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
}

resource "google_sql_database_instance" "pipeline_instance" {
  name             = "pipeline-sql-instance"
  database_version = "POSTGRES_14"
  region           = var.gcp_region
  project          = var.gcp_project_id

  deletion_protection = false

  settings {
    tier              = "db-f1-micro"
    availability_type = "ZONAL"
    disk_size         = 10
    disk_type         = "PD_SSD"

    backup_configuration {
      enabled    = true
      start_time = "02:00"
    }

    ip_configuration {
      ipv4_enabled = true

      authorized_networks {
        name  = "all"
        value = "0.0.0.0/0"
      }
    }

    database_flags {
      name  = "max_connections"
      value = "100"
    }
  }

  depends_on = [google_project_service.sqladmin_api]
}

resource "google_sql_database" "pipeline_db" {
  name     = "pipelinedb"
  instance = google_sql_database_instance.pipeline_instance.name
  project  = var.gcp_project_id
}

resource "google_sql_user" "pipeline_user" {
  name     = var.cloud_sql_user
  instance = google_sql_database_instance.pipeline_instance.name
  password = var.cloud_sql_password
  project  = var.gcp_project_id
}


resource "google_storage_bucket" "function_source_bucket" {
  name                        = "${var.gcp_project_id}-function-source"
  location                    = var.gcp_region
  project                     = var.gcp_project_id
  force_destroy               = true
  uniform_bucket_level_access = true

  depends_on = [google_project_service.storage_api]
}

data "archive_file" "processor_function_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src/processor_function"
  output_path = "${path.module}/../src/processor_function.zip"
}

resource "google_storage_bucket_object" "processor_function_source" {
  name   = "processor_function_${data.archive_file.processor_function_zip.output_md5}.zip"
  bucket = google_storage_bucket.function_source_bucket.name
  source = data.archive_file.processor_function_zip.output_path
}


data "google_project" "project" {
  project_id = var.gcp_project_id
}

resource "google_project_iam_member" "gcf_build_sa_storage" {
  project = var.gcp_project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}


resource "google_cloudfunctions_function" "processor_function" {
  name        = "process-localstack-event"
  description = "Processes Pub/Sub messages from LocalStack pipeline"
  runtime     = "python310"
  project     = var.gcp_project_id
  region      = var.gcp_region

  available_memory_mb   = 256
  source_archive_bucket = google_storage_bucket.function_source_bucket.name
  source_archive_object = google_storage_bucket_object.processor_function_source.name
  entry_point           = "process_event"
  timeout               = 120

  event_trigger {
    event_type = "google.pubsub.topic.publish"
    resource   = google_pubsub_topic.localstack_events.id

    failure_policy {
      retry = true
    }
  }

  environment_variables = {
    GCP_PROJECT_ID            = var.gcp_project_id
    CLOUD_SQL_CONNECTION_NAME = google_sql_database_instance.pipeline_instance.connection_name
    CLOUD_SQL_DB_NAME         = "pipelinedb"
    CLOUD_SQL_USER            = var.cloud_sql_user
    DYNAMODB_ENDPOINT_URL     = var.localstack_dynamodb_endpoint
    DYNAMODB_TABLE_NAME       = var.dynamodb_table_name
    AWS_ACCESS_KEY_ID         = "test"
    AWS_SECRET_ACCESS_KEY     = "test"
    AWS_DEFAULT_REGION        = "us-east-1"
  }

  secret_environment_variables {
    key        = "CLOUD_SQL_PASSWORD"
    project_id = var.gcp_project_id
    secret     = google_secret_manager_secret.sql_password.secret_id
    version    = "latest"
  }

  depends_on = [
    google_project_service.cloudfunctions_api,
    google_project_service.cloudbuild_api,
    google_sql_database_instance.pipeline_instance,
    google_project_iam_member.gcf_build_sa_storage,
  ]
}

resource "google_secret_manager_secret" "sql_password" {
  secret_id = "pipeline-sql-password"
  project   = var.gcp_project_id

  # FIX: `automatic = true` is deprecated, replaced with `auto {}`
  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager_api]
}

resource "google_secret_manager_secret_version" "sql_password_version" {
  secret      = google_secret_manager_secret.sql_password.id
  secret_data = var.cloud_sql_password
}

resource "google_secret_manager_secret_iam_member" "function_secret_access" {
  secret_id = google_secret_manager_secret.sql_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_cloudfunctions_function_iam_member" "pubsub_invoker" {
  project        = var.gcp_project_id
  region         = var.gcp_region
  cloud_function = google_cloudfunctions_function.processor_function.name
  role           = "roles/cloudfunctions.invoker"
  member         = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}
