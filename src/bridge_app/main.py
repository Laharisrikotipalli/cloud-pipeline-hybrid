import os
import json
import time
import logging
import signal
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, EndpointResolutionError
from google.cloud import pubsub_v1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger("bridge")

AWS_ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "test")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "test")

SQS_QUEUE_NAME = os.environ.get(
    "SQS_QUEUE_NAME",
    "data-processing-queue"
)

SQS_QUEUE_URL = None

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "")
GCP_PUBSUB_TOPIC = os.environ.get(
    "GCP_PUBSUB_TOPIC",
    "localstack-events"
)

DYNAMODB_TABLE = os.environ.get(
    "DYNAMODB_TABLE_NAME",
    "processed-records"
)

POLL_INTERVAL_SEC = int(
    os.environ.get("POLL_INTERVAL_SEC", "5")
)

MAX_MESSAGES = int(
    os.environ.get("MAX_MESSAGES", "10")
)

VISIBILITY_TIMEOUT = int(
    os.environ.get("VISIBILITY_TIMEOUT", "60")
)

MAX_RETRIES = int(
    os.environ.get("MAX_RETRIES", "3")
)

sqs_client = None
s3_client = None
dynamo_client = None
publisher = None
pubsub_topic = None


def _aws_client(service):
    return boto3.client(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_KEY,
        endpoint_url=AWS_ENDPOINT_URL,
    )


def init_aws_clients():
    global sqs_client
    global s3_client
    global dynamo_client

    sqs_client = _aws_client("sqs")
    s3_client = _aws_client("s3")
    dynamo_client = _aws_client("dynamodb")

    logger.info(
        "AWS clients initialised (endpoint: %s)",
        AWS_ENDPOINT_URL,
    )


def resolve_queue_url():
    global SQS_QUEUE_URL

    response = sqs_client.create_queue(
        QueueName=SQS_QUEUE_NAME
    )

    SQS_QUEUE_URL = response["QueueUrl"]

    logger.info(
        "Resolved queue URL: %s",
        SQS_QUEUE_URL
    )


def init_gcp_publisher():
    global publisher
    global pubsub_topic

    publisher = pubsub_v1.PublisherClient()

    pubsub_topic = publisher.topic_path(
        GCP_PROJECT_ID,
        GCP_PUBSUB_TOPIC,
    )

    logger.info(
        "GCP Pub/Sub publisher initialised (topic: %s)",
        pubsub_topic,
    )


def write_to_dynamodb(payload):
    record_id = payload.get("recordId")

    if not record_id:
        logger.warning(
            "Skipping DynamoDB write - no recordId in payload"
        )
        return

    try:
        dynamo_client.put_item(
            TableName=DYNAMODB_TABLE,
            Item={
                "recordId": {
                    "S": str(record_id)
                },
                "userEmail": {
                    "S": str(
                        payload.get(
                            "userEmail",
                            ""
                        )
                    )
                },
                "value": {
                    "N": str(
                        payload.get(
                            "value",
                            0
                        )
                    )
                },
                "processedAt": {
                    "S": datetime.now(
                        tz=timezone.utc
                    ).isoformat()
                },
            },
        )

        logger.info(
            "DynamoDB: wrote record recordId=%s",
            record_id,
        )

    except ClientError as exc:
        logger.error(
            "DynamoDB write failed: %s",
            exc,
        )


def fetch_s3_object(bucket, key):
    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):
        try:
            response = s3_client.get_object(
                Bucket=bucket,
                Key=key,
            )

            body = (
                response["Body"]
                .read()
                .decode("utf-8-sig")
            )

            data = json.loads(body)

            logger.info(
                "Fetched s3://%s/%s",
                bucket,
                key,
            )

            return data

        except ClientError as exc:
            logger.warning(
                "S3 error attempt %d/%d: %s",
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt == MAX_RETRIES:
                raise

            time.sleep(2 ** attempt)


def publish_to_pubsub(payload):
    message_bytes = json.dumps(
        payload
    ).encode("utf-8")

    future = publisher.publish(
        pubsub_topic,
        data=message_bytes,
    )

    message_id = future.result(
        timeout=30
    )

    logger.info(
        "Published to Pub/Sub, message_id=%s",
        message_id,
    )

    return message_id


def delete_sqs_message(
    receipt_handle,
):
    sqs_client.delete_message(
        QueueUrl=SQS_QUEUE_URL,
        ReceiptHandle=receipt_handle,
    )


def process_sqs_message(
    message,
):
    receipt_handle = message[
        "ReceiptHandle"
    ]

    body = json.loads(
        message.get(
            "Body",
            "{}"
        )
    )

    records = body.get(
        "Records",
        []
    )

    if not records:
        publish_to_pubsub(body)
        write_to_dynamodb(body)
        delete_sqs_message(
            receipt_handle
        )
        return

    for record in records:

        if not record.get(
            "eventName",
            ""
        ).startswith(
            "ObjectCreated"
        ):
            continue

        bucket = record["s3"][
            "bucket"
        ]["name"]

        key = record["s3"][
            "object"
        ]["key"]

        logger.info(
            "Processing S3 event bucket=%s key=%s",
            bucket,
            key,
        )

        payload = fetch_s3_object(
            bucket,
            key,
        )

        publish_to_pubsub(
            payload
        )

        write_to_dynamodb(
            payload
        )

    delete_sqs_message(
        receipt_handle
    )


def poll_once():
    try:
        response = sqs_client.receive_message(
            QueueUrl=SQS_QUEUE_URL,
            MaxNumberOfMessages=MAX_MESSAGES,
            WaitTimeSeconds=20,
            VisibilityTimeout=VISIBILITY_TIMEOUT,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )

    except (
        ClientError,
        EndpointResolutionError,
    ) as exc:

        logger.error(
            "SQS receive_message failed: %s",
            exc,
        )

        return

    messages = response.get(
        "Messages",
        []
    )

    if not messages:
        return

    logger.info(
        "Received %d message(s)",
        len(messages),
    )

    for msg in messages:
        process_sqs_message(msg)


_running = True


def _handle_signal(
    signum,
    _frame,
):
    global _running

    logger.info(
        "Signal %d received",
        signum,
    )

    _running = False


signal.signal(
    signal.SIGTERM,
    _handle_signal,
)

signal.signal(
    signal.SIGINT,
    _handle_signal,
)


def main():

    if not GCP_PROJECT_ID:
        logger.error(
            "GCP_PROJECT_ID not set"
        )
        sys.exit(1)

    init_aws_clients()

    resolve_queue_url()

    init_gcp_publisher()

    logger.info(
        "Bridge started - polling %s every %ds",
        SQS_QUEUE_URL,
        POLL_INTERVAL_SEC,
    )

    while _running:

        poll_once()

        if _running:
            time.sleep(
                POLL_INTERVAL_SEC
            )

    logger.info(
        "Bridge stopped."
    )


if __name__ == "__main__":
    main()