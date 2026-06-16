import os
import json
import time
import logging
import signal
import sys

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError
from google.cloud import pubsub_v1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bridge")

AWS_ENDPOINT_URL   = os.environ.get("AWS_ENDPOINT_URL", "http://localstack:4566")
AWS_REGION         = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
AWS_ACCESS_KEY_ID  = os.environ.get("AWS_ACCESS_KEY_ID", "test")
AWS_SECRET_KEY     = os.environ.get("AWS_SECRET_ACCESS_KEY", "test")
SQS_QUEUE_URL      = os.environ.get("SQS_QUEUE_URL", "")
GCP_PROJECT_ID     = os.environ.get("GCP_PROJECT_ID", "")
GCP_PUBSUB_TOPIC   = os.environ.get("GCP_PUBSUB_TOPIC", "localstack-events")
POLL_INTERVAL_SEC  = int(os.environ.get("POLL_INTERVAL_SEC", "5"))
MAX_MESSAGES       = int(os.environ.get("MAX_MESSAGES", "10"))
MAX_RETRIES        = int(os.environ.get("MAX_RETRIES", "3"))

_running = True

def _handle_signal(signum, _frame):
    global _running
    logger.info("Signal %d received - shutting down", signum)
    _running = False

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _aws_client(service):
    return boto3.client(
        service,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_KEY,
        endpoint_url=AWS_ENDPOINT_URL,
    )


def wait_for_localstack():
    """Wait until LocalStack is reachable before starting."""
    import urllib.request
    health_url = AWS_ENDPOINT_URL.rstrip("/") + "/_localstack/health"
    logger.info("Waiting for LocalStack at %s ...", health_url)
    for attempt in range(30):
        try:
            with urllib.request.urlopen(health_url, timeout=3) as resp:
                if resp.status == 200:
                    logger.info("LocalStack is ready!")
                    return True
        except Exception as e:
            logger.info("LocalStack not ready yet (attempt %d/30): %s", attempt + 1, e)
            time.sleep(5)
    logger.error("LocalStack never became ready. Exiting.")
    sys.exit(1)


def fetch_s3_object(bucket, key):
    s3 = _aws_client("s3")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read().decode("utf-8")
            data = json.loads(body)
            logger.info("Fetched s3://%s/%s", bucket, key)
            return data
        except Exception as exc:
            logger.warning("S3 fetch attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)


def publish_to_pubsub(publisher, topic, payload):
    message_bytes = json.dumps(payload).encode("utf-8")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            future = publisher.publish(topic, data=message_bytes)
            message_id = future.result(timeout=30)
            logger.info("Published to Pub/Sub, message_id=%s", message_id)
            return message_id
        except Exception as exc:
            logger.warning("Pub/Sub publish attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 ** attempt)


def process_message(sqs, publisher, topic, message):
    receipt_handle = message["ReceiptHandle"]
    body_str = message.get("Body", "{}")

    try:
        body = json.loads(body_str)
    except json.JSONDecodeError:
        logger.error("Cannot parse SQS message body: %s", body_str[:200])
        sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
        return

    records = body.get("Records", [])
    if not records:
        logger.info("No S3 Records - treating body as direct payload")
        publish_to_pubsub(publisher, topic, body)
        sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)
        return

    for record in records:
        event_name = record.get("eventName", "")
        if not event_name.startswith("ObjectCreated"):
            continue
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        logger.info("Processing S3 event: bucket=%s key=%s", bucket, key)
        try:
            payload = fetch_s3_object(bucket, key)
            publish_to_pubsub(publisher, topic, payload)
        except Exception as exc:
            logger.error("Failed to process %s/%s: %s", bucket, key, exc)
            return

    sqs.delete_message(QueueUrl=SQS_QUEUE_URL, ReceiptHandle=receipt_handle)


def main():
    if not SQS_QUEUE_URL:
        logger.error("SQS_QUEUE_URL not set. Exiting.")
        sys.exit(1)
    if not GCP_PROJECT_ID:
        logger.error("GCP_PROJECT_ID not set. Exiting.")
        sys.exit(1)

    wait_for_localstack()

    sqs = _aws_client("sqs")
    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(GCP_PROJECT_ID, GCP_PUBSUB_TOPIC)

    logger.info("AWS clients initialised (endpoint: %s)", AWS_ENDPOINT_URL)
    logger.info("GCP Pub/Sub publisher initialised (topic: %s)", topic)
    logger.info("Bridge started - polling %s every %ds", SQS_QUEUE_URL, POLL_INTERVAL_SEC)

    while _running:
        try:
            response = sqs.receive_message(
                QueueUrl=SQS_QUEUE_URL,
                MaxNumberOfMessages=MAX_MESSAGES,
                WaitTimeSeconds=20,
                VisibilityTimeout=60,
            )
            messages = response.get("Messages", [])
            if messages:
                logger.info("Received %d message(s) from SQS", len(messages))
                for msg in messages:
                    process_message(sqs, publisher, topic, msg)
            else:
                logger.debug("No messages in queue")
        except Exception as exc:
            logger.error("Poll error: %s", exc)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if _running:
            time.sleep(POLL_INTERVAL_SEC)

    logger.info("Bridge stopped.")


if __name__ == "__main__":
    main()