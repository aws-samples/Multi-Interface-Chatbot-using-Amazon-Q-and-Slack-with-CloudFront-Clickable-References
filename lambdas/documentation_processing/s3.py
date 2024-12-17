import os
from aws_lambda_powertools import Logger
logger = Logger()


def download_dir(client, resource, dist, bucket, local):
    paginator = client.get_paginator('list_objects')
    logger.info(f"Downloading files from {bucket} bucket to local")
    for result in paginator.paginate(Bucket=bucket, Delimiter='/', Prefix=dist):
        if result.get('CommonPrefixes') is not None:
            for subdir in result.get('CommonPrefixes'):
                download_dir(client, resource, subdir.get('Prefix'), bucket, local)
        for file in result.get('Contents', []):
            dest_pathname = os.path.join(local, file.get('Key'))
            if not os.path.exists(os.path.dirname(dest_pathname)):
                os.makedirs(os.path.dirname(dest_pathname))
            if not file.get('Key').endswith('/'):
                resource.meta.client.download_file(bucket, file.get('Key'), dest_pathname)


def upload_directory(client, path, bucket):
    logger.info(f"Uploading data from path: {path}, to bucket: {bucket}")
    for root, dirs, files in os.walk(path):
        for file in files:
            client.upload_file(os.path.join(root, file), bucket, file)


def empty_s3_bucket(s3_resource, bucket):
    logger.info(f"Emptying bucket: {bucket}")
    _bucket = s3_resource.Bucket(bucket)
    _bucket.objects.all().delete()
