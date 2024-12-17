import os
import boto3
import json

from s3 import download_dir, upload_directory, empty_s3_bucket
from process import convert_to_md, split_and_create_metadata
import tempfile

from aws_lambda_powertools import Logger
from aws_lambda_powertools import Metrics
from aws_lambda_powertools import Tracer

logger = Logger()
metrics = Metrics()
tracer = Tracer(service="Radiuss")

raw_bucket_name = os.environ['raw_bucket_name']
processed_bucket_name = os.environ['processed_bucket_name']
kendra_index_id = os.environ['kendra_index_id']
kendra_data_source_id = os.environ['kendra_data_source_id']

kendra = boto3.client("kendra")
s3_client = boto3.client('s3')
s3_resource = boto3.resource('s3')

temp_dir = tempfile.TemporaryDirectory()

RST_PATH = tempfile.TemporaryDirectory()
MD_PATH = tempfile.TemporaryDirectory()
SPLIT_PATH = tempfile.TemporaryDirectory()
METADATA_PATH = tempfile.TemporaryDirectory()


def lambda_handler(event, context):
    empty_s3_bucket(
        s3_resource=s3_resource,
        bucket=processed_bucket_name
    )

    download_dir(
        client=s3_client,
        resource=s3_resource,
        dist='',
        bucket=raw_bucket_name,
        local=RST_PATH.name
    )

    convert_to_md(
        input_path=RST_PATH.name,
        output_path=MD_PATH.name
    )

    split_and_create_metadata(
        input_path=MD_PATH.name,
        split_path=SPLIT_PATH.name,
        metadata_path=METADATA_PATH.name,
    )

    upload_directory(s3_client, path=SPLIT_PATH.name, bucket=processed_bucket_name)
    upload_directory(s3_client, path=METADATA_PATH.name, bucket=processed_bucket_name)

    logger.info(f"Start data source sync index id: {kendra_index_id} data source id: {kendra_data_source_id}")
    response = kendra.start_data_source_sync_job(Id=kendra_data_source_id, IndexId=kendra_index_id)
    logger.info("response:" + json.dumps(response))

    logger.info("Done!")
    RST_PATH.cleanup()
    MD_PATH.cleanup()
    SPLIT_PATH.cleanup()
    METADATA_PATH.cleanup()

    return {
        'statusCode': 200,
        'body': json.dumps({'msg': "Preprocessing Completed!"})
    }
