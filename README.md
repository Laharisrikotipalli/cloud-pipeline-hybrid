# Hybrid Cloud Data Pipeline

A production-ready event-driven data pipeline that bridges a simulated AWS environment (LocalStack) with Google Cloud Platform (GCP). File uploads to an S3 bucket trigger a multi-stage pipeline that ultimately persists records in both GCP Cloud SQL (PostgreSQL) and LocalStack DynamoDB.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        LOCAL ENVIRONMENT                        │
│                                                                 │
│  ┌──────────────┐   S3 Event    ┌──────────────────────────┐   │
│  │  S3 Bucket   │ ─────────────▶│   SQS Queue              │   │
│  │  (LocalStack)│  Notification │   (data-processing-queue)│   │
│  └──────────────┘               └──────────┬───────────────┘   │
│                                             │ Poll              │
│                                   ┌─────────▼─────────┐        │
│                                   │  Bridge App        │        │
│                                   │  (Docker container)│        │
│                                   └─────────┬──────────┘        │
└─────────────────────────────────────────────┼───────────────────┘
                                              │ Publish
                           ┌──────────────────▼───────────────────┐
                           │              GCP                      │
                           │                                       │
                           │   ┌────────────────────────┐         │
                           │   │  Pub/Sub Topic          │         │
                           │   │  (localstack-events)    │         │
                           │   └───────────┬────────────┘         │
                           │               │ Trigger               │
                           │   ┌───────────▼────────────┐         │
                           │   │  Cloud Function         │         │
                           │   │  (process-localstack-   │         │
                           │   │   event)                │         │
                           │   └──────┬─────────┬────────┘         │
                           │          │         │                  │
                           │   ┌──────▼──┐ ┌────▼──────────────┐  │
                           │   │Cloud SQL│ │LocalStack DynamoDB │  │
                           │   │(records)│ │(processed-records) │  │
                           │   └─────────┘ └───────────────────┘  │
                           └───────────────────────────────────────┘
```

**Data flow:**
1. A JSON file is uploaded to the `hybrid-cloud-bucket` S3 bucket in LocalStack.
2. LocalStack S3 sends an event notification to the `data-processing-queue` SQS queue.
3. The **Bridge App** polls the SQS queue, fetches the original file from S3, and publishes the file content to the GCP `localstack-events` Pub/Sub topic.
4. The **Cloud Function** is triggered by the Pub/Sub message, parses the payload, and writes the record to:
   - **GCP Cloud SQL** (`pipelinedb.records` table)
   - **LocalStack DynamoDB** (`processed-records` table)

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker + Docker Compose | 24+ | [docs.docker.com](https://docs.docker.com) |
| Terraform | 1.5+ | [terraform.io](https://terraform.io) |
| AWS CLI | 2.x | [aws.amazon.com/cli](https://aws.amazon.com/cli) |
| `awslocal` | latest | `pip install awscli-local` |
| gcloud CLI | latest | [cloud.google.com/sdk](https://cloud.google.com/sdk) |
| Python | 3.11+ | [python.org](https://python.org) |

---

## Quick Start

### 1. Clone & Configure

```bash
git clone <your-repo-url>
cd hybrid-cloud-pipeline

# Create your .env file from the example
cp .env.example .env
# Edit .env — fill in GCP_PROJECT_ID, PATH_TO_GCP_KEYFILE, CLOUD_SQL_PASSWORD
```

### 2. GCP Setup

```bash
# Authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Create a service account
gcloud iam service-accounts create pipeline-sa \
  --display-name "Hybrid Pipeline Service Account"

# Grant required roles
for ROLE in roles/pubsub.editor roles/cloudfunctions.admin \
            roles/cloudsql.client roles/storage.admin \
            roles/secretmanager.admin; do
  gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
    --member="serviceAccount:pipeline-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="$ROLE"
done

# Download the key file
gcloud iam service-accounts keys create keyfile.json \
  --iam-account pipeline-sa@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

### 3. Start LocalStack

```bash
docker-compose up localstack -d

# Wait for healthy status (~30s)
docker-compose ps
# localstack_main   running (healthy)
```

### 4. Apply Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

terraform init
terraform validate
terraform plan
terraform apply
cd ..
```

### 5. Start the Bridge App

```bash
docker-compose up bridge -d
docker-compose logs -f bridge
```

### 6. Verify the Full Pipeline

```bash
# Upload the test event
awslocal s3 cp test-event.json s3://hybrid-cloud-bucket/

# Check SQS received the message
awslocal sqs receive-message \
  --queue-url http://localhost:4566/000000000000/data-processing-queue

# Create a temporary Pub/Sub subscription for testing
gcloud pubsub subscriptions create test-sub \
  --topic=localstack-events \
  --expiration-period=1h

# Pull the message (wait ~30s for the bridge to forward it)
gcloud pubsub subscriptions pull test-sub --auto-ack --limit=5

# Check DynamoDB
awslocal dynamodb get-item \
  --table-name processed-records \
  --key '{"recordId":{"S":"xyz-789"}}'

# Verify Cloud SQL (replace with your instance IP)
psql "host=<CLOUD_SQL_IP> dbname=pipelinedb user=pipeline_user" \
  -c "SELECT * FROM records WHERE id = 'xyz-789';"
```

---

## Project Structure

```
hybrid-cloud-pipeline/
├── docker-compose.yml            # Orchestrates LocalStack + Bridge
├── Dockerfile                    # Root Dockerfile for Bridge App
├── .env.example                  # Environment variable documentation
├── .gitignore
├── submission.json               # Automated evaluation config
├── test-event.json               # Test payload for pipeline verification
│
├── terraform/
│   ├── providers.tf              # AWS (LocalStack) + GCP providers
│   ├── variables.tf              # All configurable variables
│   ├── outputs.tf                # Resource outputs
│   ├── localstack.tf             # S3, SQS, DynamoDB, IAM
│   ├── gcp.tf                    # Pub/Sub, Cloud SQL, Cloud Function
│   └── terraform.tfvars.example  # Example variable values
│
└── src/
    ├── bridge_app/
    │   ├── main.py               # SQS → Pub/Sub bridge logic
    │   ├── requirements.txt
    │   └── Dockerfile
    └── processor_function/
        ├── main.py               # Cloud Function: Pub/Sub → SQL + DynamoDB
        └── requirements.txt
```

---

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `GCP_PROJECT_ID` | GCP project ID | `my-project-123` |
| `GCP_REGION` | GCP deployment region | `us-central1` |
| `PATH_TO_GCP_KEYFILE` | Path to service account JSON key | `./keyfile.json` |
| `AWS_ACCESS_KEY_ID` | LocalStack dummy credential | `test` |
| `AWS_SECRET_ACCESS_KEY` | LocalStack dummy credential | `test` |
| `AWS_DEFAULT_REGION` | AWS region for LocalStack | `us-east-1` |
| `SQS_QUEUE_URL` | Full SQS queue URL | `http://localhost:4566/000000000000/data-processing-queue` |
| `CLOUD_SQL_PASSWORD` | PostgreSQL user password | `your-secure-password` |

---

## Infrastructure Resources

### LocalStack (AWS)

| Resource | Name | Type |
|----------|------|------|
| S3 Bucket | `hybrid-cloud-bucket` | Event source |
| SQS Queue | `data-processing-queue` | Message buffer |
| SQS DLQ | `data-processing-queue-dlq` | Failed messages |
| DynamoDB Table | `processed-records` | Output store |

### GCP

| Resource | Name | Type |
|----------|------|------|
| Pub/Sub Topic | `localstack-events` | Event bus |
| Pub/Sub DLQ | `localstack-events-dlq` | Failed messages |
| Cloud SQL | `pipeline-sql-instance` | PostgreSQL 14 |
| Database | `pipelinedb` | Records store |
| Cloud Function | `process-localstack-event` | Event processor |

---

## Database Schema

### Cloud SQL — `records` table

```sql
CREATE TABLE records (
    id           VARCHAR(255) PRIMARY KEY NOT NULL,
    user_email   VARCHAR(255) NOT NULL,
    value        INTEGER      NOT NULL,
    processed_at TIMESTAMP    NOT NULL
);
```

### DynamoDB — `processed-records` table

| Attribute | Type | Role |
|-----------|------|------|
| `recordId` | String | Partition key (PK) |
| `userEmail` | String | GSI hash key |
| `value` | Number | Data |
| `processedAt` | String | ISO 8601 timestamp |

---

## Design Decisions

### Idempotency
Both storage writes use upsert semantics:
- **Cloud SQL**: `INSERT ... ON CONFLICT (id) DO NOTHING` prevents duplicate rows.
- **DynamoDB**: `PutItem` with the same `recordId` simply overwrites — safe for repeated delivery.

### Dead-Letter Queues
Both SQS and Pub/Sub are configured with DLQs. Messages that fail 3 times (SQS) or 5 times (Pub/Sub) are moved to the respective DLQ for inspection without blocking the main pipeline.

### Retry with Backoff
The Bridge App and Cloud Function implement exponential backoff (2ˢ seconds) for transient API failures, preventing thundering-herd problems during LocalStack restarts.

### Security
- GCP credentials are never hardcoded — loaded from a mounted key file or `GOOGLE_APPLICATION_CREDENTIALS`.
- Terraform variables marked `sensitive = true` are never printed in plan output.
- `terraform.tfvars` and `keyfile.json` are in `.gitignore`.
- LocalStack IAM role follows least-privilege principle.

---

## Troubleshooting

**LocalStack not healthy after 2 minutes**
```bash
docker-compose logs localstack | tail -50
# Ensure Docker has at least 4GB RAM allocated
```

**Terraform `apply` fails with connection refused**
```bash
# Confirm LocalStack is healthy first
docker-compose ps
awslocal s3 ls  # should return empty list, not error
```

**Bridge app not forwarding messages**
```bash
docker-compose logs bridge
# Check GCP_PROJECT_ID and PATH_TO_GCP_KEYFILE are set correctly in .env
```

**Cloud Function not writing to DynamoDB**
```bash
gcloud functions logs read process-localstack-event --limit=50
# Ensure DYNAMODB_ENDPOINT_URL is reachable from GCP (requires tunnel/VPN in production)
```
