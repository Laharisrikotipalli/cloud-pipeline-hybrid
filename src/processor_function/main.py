import os
import json
import base64
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
import pg8000.native


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("processor_function")


GCP_PROJECT_ID            = os.environ.get("GCP_PROJECT_ID", "")
CLOUD_SQL_CONNECTION_NAME = os.environ.get("CLOUD_SQL_CONNECTION_NAME", "")
CLOUD_SQL_DB_NAME         = os.environ.get("CLOUD_SQL_DB_NAME", "pipelinedb")
CLOUD_SQL_USER            = os.environ.get("CLOUD_SQL_USER", "pipeline_user")
CLOUD_SQL_PASSWORD        = os.environ.get("CLOUD_SQL_PASSWORD", "")
CLOUD_SQL_HOST            = os.environ.get("CLOUD_SQL_HOST", "")  

DYNAMODB_ENDPOINT_URL     = os.environ.get("DYNAMODB_ENDPOINT_URL", "http://localhost:4566")
DYNAMODB_TABLE_NAME       = os.environ.get("DYNAMODB_TABLE_NAME", "processed-records")
AWS_REGION                = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
AWS_ACCESS_KEY_ID         = os.environ.get("AWS_ACCESS_KEY_ID", "test")
AWS_SECRET_ACCESS_KEY     = os.environ.get("AWS_SECRET_ACCESS_KEY", "test")


def _get_sql_connection():
    """
    Return a pg8000 connection.
    Prefers Unix socket (Cloud SQL Auth Proxy) when running on GCP;
    falls back to direct TCP for local testing.
    """
    unix_socket = f"/cloudsql/{CLOUD_SQL_CONNECTION_NAME}"

    if CLOUD_SQL_CONNECTION_NAME and os.path.exists("/cloudsql"):
        logger.info("Connecting via Cloud SQL Unix socket: %s", unix_socket)
        conn = pg8000.native.Connection(
            user=CLOUD_SQL_USER,
            password=CLOUD_SQL_PASSWORD,
            database=CLOUD_SQL_DB_NAME,
            unix_sock=unix_socket,
        )
    else:
        host = CLOUD_SQL_HOST or "localhost"
        logger.info("Connecting via TCP to %s", host)
        conn = pg8000.native.Connection(
            user=CLOUD_SQL_USER,
            password=CLOUD_SQL_PASSWORD,
            database=CLOUD_SQL_DB_NAME,
            host=host,
            port=5432,
        )
    return conn


def _ensure_table(conn):
    """Create the records table if it does not already exist."""
    conn.run("""
        CREATE TABLE IF NOT EXISTS records (
            id           VARCHAR(255) PRIMARY KEY NOT NULL,
            user_email   VARCHAR(255) NOT NULL,
            value        INTEGER      NOT NULL,
            processed_at TIMESTAMP    NOT NULL
        );
    """)


def write_to_cloud_sql(record_id: str, user_email: str, value: int,
                       processed_at: datetime) -> bool:
    """
    Insert a record into Cloud SQL.
    Uses ON CONFLICT DO NOTHING for idempotency.
    Returns True on insert, False when already present.
    """
    conn = None
    try:
        conn = _get_sql_connection()
        _ensure_table(conn)
        conn.run(
            """
            INSERT INTO records (id, user_email, value, processed_at)
            VALUES (:id, :user_email, :value, :processed_at)
            ON CONFLICT (id) DO NOTHING;
            """,
            id=record_id,
            user_email=user_email,
            value=value,
            processed_at=processed_at,
        )
        logger.info("Cloud SQL: upserted record id=%s", record_id)
        return True
    except Exception as exc:
        logger.error("Cloud SQL write failed for id=%s: %s", record_id, exc)
        raise
    finally:
        if conn:
            conn.close()


def _get_dynamodb_client():
    return boto3.client(
        "dynamodb",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        endpoint_url=DYNAMODB_ENDPOINT_URL,
    )


def write_to_dynamodb(record_id: str, user_email: str, value: int,
                      processed_at: datetime):
    """
    Write a record to LocalStack DynamoDB.
    PutItem is inherently idempotent (overwrites same PK).
    """
    client = _get_dynamodb_client()
    item = {
        "recordId":    {"S": record_id},
        "userEmail":   {"S": user_email},
        "value":       {"N": str(value)},
        "processedAt": {"S": processed_at.isoformat()},
    }
    try:
        client.put_item(TableName=DYNAMODB_TABLE_NAME, Item=item)
        logger.info("DynamoDB: wrote record recordId=%s endpoint=%s",
                    record_id, DYNAMODB_ENDPOINT_URL)
    except ClientError as exc:
        logger.error("DynamoDB write failed for recordId=%s: %s", record_id, exc)
        raise



def process_event(event: dict, context):
    """
    Cloud Function entry point.

    Args:
        event:   Pub/Sub event dict with a base64-encoded `data` field.
        context: Cloud Function context object (unused).
    """

    pubsub_data = event.get("data", "")
    if not pubsub_data:
        logger.error("Empty Pub/Sub message — skipping")
        return "ERROR: empty message", 400

    try:
        raw_payload = base64.b64decode(pubsub_data).decode("utf-8")
        logger.info("Decoded Pub/Sub payload: %s", raw_payload[:500])
        data = json.loads(raw_payload)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error("Failed to decode/parse Pub/Sub payload: %s", exc)
        return "ERROR: invalid payload", 400

    record_id  = data.get("recordId")
    user_email = data.get("userEmail")
    value      = data.get("value")

    missing = [f for f, v in [("recordId", record_id),
                               ("userEmail", user_email),
                               ("value", value)] if v is None]
    if missing:
        logger.error("Missing required fields: %s in payload: %s", missing, data)
        return f"ERROR: missing fields {missing}", 400

    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        logger.error("'value' must be an integer, got %r: %s", value, exc)
        return "ERROR: value must be integer", 400

    processed_at = datetime.now(tz=timezone.utc)

    logger.info("Processing record: id=%s email=%s value=%d ts=%s",
                record_id, user_email, value, processed_at.isoformat())

    sql_ok = False
    try:
        write_to_cloud_sql(record_id, user_email, value, processed_at)
        sql_ok = True
    except Exception as exc: 
        logger.error("Cloud SQL write failed: %s", exc)

    dynamo_ok = False
    try:
        write_to_dynamodb(record_id, user_email, value, processed_at)
        dynamo_ok = True
    except Exception as exc: 
        logger.error("DynamoDB write failed: %s", exc)

    if not sql_ok or not dynamo_ok:
       
        return "PARTIAL_FAILURE", 500

    logger.info("Record %s processed successfully (sql=%s dynamo=%s)",
                record_id, sql_ok, dynamo_ok)
    return "OK", 200
