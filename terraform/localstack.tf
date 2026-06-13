# ============================================================
# LocalStack AWS Resources
# ============================================================

# ----------------------------
# S3 Bucket
# ----------------------------

resource "aws_s3_bucket" "hybrid_bucket" {
  bucket        = var.s3_bucket_name
  force_destroy = true

  tags = {
    Name        = var.s3_bucket_name
    Environment = "local"
    Project     = "hybrid-cloud-pipeline"
  }
}

resource "aws_s3_bucket_versioning" "hybrid_bucket_versioning" {
  bucket = aws_s3_bucket.hybrid_bucket.id

  versioning_configuration {
    status = "Enabled"
  }
}

# S3 Event Notification → SQS
resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket = aws_s3_bucket.hybrid_bucket.id

  queue {
    queue_arn     = aws_sqs_queue.data_processing_queue.arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".json"
  }

  depends_on = [aws_sqs_queue_policy.allow_s3_notifications]
}

# ----------------------------
# SQS — Dead-Letter Queue
# ----------------------------

resource "aws_sqs_queue" "data_processing_dlq" {
  name                       = "${var.sqs_queue_name}-dlq"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 30

  tags = {
    Name        = "${var.sqs_queue_name}-dlq"
    Environment = "local"
    Project     = "hybrid-cloud-pipeline"
  }
}

# ----------------------------
# SQS — Main Processing Queue
# ----------------------------

resource "aws_sqs_queue" "data_processing_queue" {
  name                       = var.sqs_queue_name
  visibility_timeout_seconds = 60
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.data_processing_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name        = var.sqs_queue_name
    Environment = "local"
    Project     = "hybrid-cloud-pipeline"
  }
}

resource "aws_sqs_queue_policy" "allow_s3_notifications" {
  queue_url = aws_sqs_queue.data_processing_queue.url

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowS3ToSendMessage"
        Effect    = "Allow"
        Principal = { Service = "s3.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.data_processing_queue.arn
        Condition = {
          ArnLike = {
            "aws:SourceArn" = "arn:aws:s3:::${var.s3_bucket_name}"
          }
        }
      }
    ]
  })
}

# ----------------------------
# DynamoDB Table
# ----------------------------

resource "aws_dynamodb_table" "processed_records" {
  name         = var.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "recordId"

  attribute {
    name = "recordId"
    type = "S"
  }

  global_secondary_index {
    name            = "UserEmailIndex"
    hash_key        = "userEmail"
    projection_type = "ALL"
  }

  attribute {
    name = "userEmail"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name        = var.dynamodb_table_name
    Environment = "local"
    Project     = "hybrid-cloud-pipeline"
  }
}

# ----------------------------
# IAM Role for the bridge app
# ----------------------------

resource "aws_iam_role" "bridge_role" {
  name = "bridge-app-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name    = "bridge-app-role"
    Project = "hybrid-cloud-pipeline"
  }
}

resource "aws_iam_role_policy" "bridge_policy" {
  name = "bridge-app-policy"
  role = aws_iam_role.bridge_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.data_processing_queue.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.hybrid_bucket.arn,
          "${aws_s3_bucket.hybrid_bucket.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = aws_dynamodb_table.processed_records.arn
      }
    ]
  })
}