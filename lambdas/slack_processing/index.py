import os
import boto3
import json

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit, MetricResolution
from aws_lambda_powertools import Tracer

logger = Logger()
metrics = Metrics()
tracer = Tracer(service="Radiuss")

s3_client = boto3.client('s3')
s3_resource = boto3.resource('s3')

# Get the CloudFront distribution prefix from environment variable
cloudfront_modifier = os.environ.get("cloudfront_distribution_prefix")
raw_bucket = os.environ.get("raw_bucket")
processed_bucket = os.environ.get("processed_bucket")
kendra_index_id = os.environ['kendra_index_id']
kendra_data_source_id = os.environ['kendra_data_source_id']

kendra = boto3.client("kendra")


def create_metadata(title, source_uri):
    return json.dumps(
        {
            "Attributes": {
                "_source_uri": source_uri,
                "data_source": "slack"
            },
            "Title": f"{title}",
            "ContentType": "PLAIN_TEXT",
        }
    )


@logger.inject_lambda_context(log_event=True)
@metrics.log_metrics(capture_cold_start_metric=True)
@tracer.capture_lambda_handler
def lambda_handler(event, context):
    logger.info(f"event: {event}")

    metrics.add_dimension(
        name="Application",
        value="Radiuss"
    )
    metrics.add_metric(
        name="SlackProcessingLambdaInvocation",
        unit=MetricUnit.Count,
        value=1,
        resolution=MetricResolution.High
    )

    paginator = s3_client.get_paginator('list_objects')
    for page in paginator.paginate(Bucket=raw_bucket, Delimiter='/'):
        logger.info(page)
        for file in page.get('Contents', []):
            file = file.get('Key')
            logger.info(f"Processing file: {file}")

            # Create modified source URI with CloudFront modifier
            source_uri_modified = f"https://{cloudfront_modifier}/{file}"
            logger.info(f"source_uri_modified: {source_uri_modified}")

            # Copy raw slack data form raw bucket to processed bucket
            bucket = s3_resource.Bucket(processed_bucket)
            bucket.copy({'Bucket': raw_bucket, 'Key': file}, file)

            # Create metadata in processed bucket
            s3_client.put_object(
                Body=create_metadata(
                    title=file,
                    source_uri=source_uri_modified
                ),
                Bucket=processed_bucket,
                Key=file + ".metadata.json"
            )

    logger.info(f"Start data source sync index id: {kendra_index_id} data source id: {kendra_data_source_id}")
    response = kendra.start_data_source_sync_job(Id=kendra_data_source_id, IndexId=kendra_index_id)
    logger.info("response:" + json.dumps(response))

    return {
        'statusCode': 200,
        'body': json.dumps({'msg': "Preprocessing Completed!"})
    }

