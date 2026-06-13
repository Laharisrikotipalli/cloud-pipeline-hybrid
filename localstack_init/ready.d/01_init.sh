#!/bin/bash
echo '=== Initialising LocalStack resources ==='
awslocal s3 mb s3://hybrid-cloud-bucket --region us-east-1 2>/dev/null || true
awslocal sqs create-queue --queue-name data-processing-queue --region us-east-1 2>/dev/null || true
awslocal dynamodb create-table --table-name processed-records --attribute-definitions AttributeName=recordId,AttributeType=S --key-schema AttributeName=recordId,KeyType=HASH --billing-mode PAY_PER_REQUEST --region us-east-1 2>/dev/null || true
echo '=== LocalStack resources ready ==='