import os
import boto3
import json
import urllib3
from botocore.exceptions import ClientError
from datetime import datetime, timedelta
import uuid

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit, MetricResolution
from aws_lambda_powertools import Tracer

logger = Logger()
metrics = Metrics()
tracer = Tracer(service="Radiuss")


# boto
kendra = boto3.client("kendra")
s3_client = boto3.client('s3')
secretsmanager_client = boto3.client('secretsmanager')
ssm_client = boto3.client('ssm')

# Get the environment variables
secret_name = os.environ.get("slack_token_arn")
processed_bucket_name = os.environ.get("processed_bucket_name")
raw_bucket_name = os.environ.get("raw_bucket_name")
kendra_index_id = os.environ['kendra_index_id']
kendra_data_source_id = os.environ['kendra_data_source_id']
cloudfront_distribution_prefix = os.environ['cloudfront_distribution_prefix']
parent_channel_param_name = os.environ.get('parent_channel_param_name')


# Channel ID
channel_id = ssm_client.get_parameter(Name=parent_channel_param_name)['Parameter']['Value']

http = urllib3.PoolManager()

# Get token
slack_token = json.loads(
	secretsmanager_client.get_secret_value(
		SecretId=os.environ.get('slack_token_arn')
	)['SecretString']
)['token']

import re
CLEANR = re.compile('<.*?>')


def remove_tags(raw):
	cleantext = re.sub(CLEANR, '', raw)
	return cleantext


# Verify slack token works and correct workspace
def verify_slack_token(slack_token):
	test_url = "https://slack.com/api/auth.test"
	headers = {"Authorization": f"Bearer {slack_token}"}
	response = http.request('POST', test_url, headers=headers)
	data = json.loads(response.data.decode('utf-8'))
	if data['ok']:
		logger.info(f"Token is valid for workspace: {data['team']}")
		logger.info(f"Associated with user: {data['user']}")
		return True
	else:
		logger.error(f"Token validation failed: {data['error']}")
		return False


# Verify channel exists
def verify_channel(slack_token, channel_id):
	url = f"https://slack.com/api/conversations.info?channel={channel_id}"
	headers = {"Authorization": f"Bearer {slack_token}"}
	response = http.request('GET', url, headers=headers)
	data = json.loads(response.data.decode('utf-8'))
	if data['ok']:
		logger.info(f"Channel verified: {data['channel']['name']}")
		return True
	else:
		logger.error(f"Channel verification failed: {data.get('error')}")
		return False


# Lambda function for slack ingestion
def fetch_channel_history(slack_token, channel_id, oldest_timestamp, limit):
	url = "https://slack.com/api/conversations.history"
	headers = {
		"Authorization": f"Bearer {slack_token}"
	}
	params = {
		"channel": channel_id,
		"oldest": oldest_timestamp,
		"limit": limit
	}

	try:
		# urllib3 HTTP request
		response = http.request('GET', url, fields=params, headers=headers)

		if response.status == 200:
			data = json.loads(response.data.decode('utf-8'))
			if data['ok']:
				logger.info(f"Slack data retrieved.")
				return data['messages']
			else:
				raise Exception(f"Error fetching Slack data: {data.get('error')}")
		else:
			raise Exception(f"Error fetching Slack data: HTTP {response.status}")

	except Exception as e:
		logger.error(f"Failed to fetch channel history. An error occured: {e}")
		return None


def upload_metadata_to_s3(data, bucket_name, object_key):
	try:
		s3_client.put_object(Bucket=bucket_name, Key=object_key, Body=data)
		logger.info(f"Metadata uploaded to raw bucket")
	except ClientError as e:
		logger.error(f"Erroring uploading metadata to raw bucket")
		raise e


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


def get_thread(slack_token, ts, channel_id):
	url = "https://slack.com/api/conversations.replies"
	headers = {
		"Content-Type": "application/x-www-form-urlencoded",
		"Authorization": f"Bearer {slack_token}"
	}
	data = {
		"channel": channel_id,
		"ts": ts
	}
	output = ""

	response = http.request("GET", url, fields=data, headers=headers)
	data = json.loads(response.data.decode('utf-8'))
	logger.info(f"data: {data}")
	messages = data['messages']
	for message in messages:
		if "bot_id" not in message:
			output += remove_tags(message['text']) + "\n"
	logger.info(f"get_thread output: {output}")
	return output


def save_message_to_s3(messages, timestamp):
	for message in messages:
		file_name = f"{channel_id}-{timestamp}-{str(uuid.uuid4())}"
		source_uri_modified = f"https://{cloudfront_distribution_prefix}/{file_name}.txt"
		save_text = ""

		if "reply_count" in message and message["reply_count"] > 0:
			# Retrieve the thread for the current message
			save_text += get_thread(
				slack_token=slack_token,
				ts=message["ts"],
				channel_id=channel_id,
			)
		else:
			save_text += message['text'] + "\n"
		save_text += "\n"

		logger.info(f"save_text: {save_text}")
		s3_client.put_object(
			Body=save_text,
			Bucket=raw_bucket_name,
			Key=file_name + ".txt"
		)

		logger.info(f"Uploading text to {processed_bucket_name}")
		s3_client.put_object(
			Body=save_text,
			Bucket=processed_bucket_name,
			Key=file_name + ".txt"
		)

		logger.info(f"Uploading metadata to {processed_bucket_name}")
		s3_client.put_object(
			Body=create_metadata(
				title=file_name,
				source_uri=source_uri_modified
			),
			Bucket=processed_bucket_name,
			Key=file_name + ".txt.metadata.json"
		)


@logger.inject_lambda_context(log_event=True)
@metrics.log_metrics(capture_cold_start_metric=True)
@tracer.capture_lambda_handler
def lambda_handler(event, context: LambdaContext):

	metrics.add_dimension(
		name="Application",
		value="Radiuss"
	)
	metrics.add_metric(
		name="SlackDailyIngestLambdaInvocation",
		unit=MetricUnit.Count,
		value=1,
		resolution=MetricResolution.High
	)

	# Verify workspace
	if not verify_slack_token(slack_token):
		raise Exception("Invalid Slack token or wrong workspace.")

	# Verify channel
	if not verify_channel(slack_token, channel_id):
		raise Exception("Channel not found or not accessible.")

	# Calc 24-hour timestamp
	current_time = datetime.now()
	oldest_time = current_time - timedelta(days=1)
	oldest_timestamp = oldest_time.timestamp()  # Unix

	limit = 1000

	history_data = fetch_channel_history(slack_token, channel_id, oldest_timestamp, limit)

	if history_data:
		# Save messages to S3
		save_message_to_s3(history_data, timestamp=datetime.now().strftime('%Y-%m-%d'))
		logger.info("Channel messages uploaded to S3")
	else:
		logger.info("No data retrieved from Slack API.")

	logger.info(f"Start data source sync index id: {kendra_index_id} data source id: {kendra_data_source_id}")
	response = kendra.start_data_source_sync_job(Id=kendra_data_source_id, IndexId=kendra_index_id)
	logger.info("response:" + json.dumps(response))

	return {
		'statusCode': 200,
		'body': json.dumps({'msg': "Success!"})
	}
