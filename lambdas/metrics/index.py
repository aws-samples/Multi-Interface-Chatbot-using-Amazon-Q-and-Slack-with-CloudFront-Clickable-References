import boto3
from datetime import datetime, timedelta
import json
import os
import urllib3

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit, MetricResolution
from aws_lambda_powertools import Tracer


logger = Logger()
metrics = Metrics()
tracer = Tracer(service="Radiuss")

ssm_client = boto3.client('ssm')
http = urllib3.PoolManager()

today = datetime.today()
yesterday = today - timedelta(days=1)
cloudwatch = boto3.client('cloudwatch')

namespace = 'radiuss'
dimensions = [
    {
        'Name': 'Application',
        'Value': 'Radiuss'
    },
    {
        'Name': 'service',
        'Value': 'radiuss'
    },
]

report_metrics = [
    'SlackBotLambdaInvocation',
    'RespondToMessage',
    'ThumbsUp',
    'ThumbsDown',
    'Retry',
    'SlackDailyIngestLambdaInvocation'
]

slack_post_channel_url = 'https://slack.com/api/chat.postMessage'

secretsmanager_client = boto3.client('secretsmanager')
slack_token = json.loads(
    secretsmanager_client.get_secret_value(
        SecretId=os.environ.get('slack_token_arn')
    )['SecretString']
)['token']

headers = {
    'Authorization': f'Bearer {slack_token}',
    'Content-Type': 'application/json',
}

child_channel_param_name = os.environ.get('child_channel_param_name')


def get_metric(metric_name):
    total = 0
    response = cloudwatch.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=yesterday,
        EndTime=today,
        Period=60*24,
        Statistics=['Sum'],
        Unit='Count',
    )

    for datapoint in response['Datapoints']:
        total += datapoint['Sum']

    return total


def send_message(channel, message):
    logger.info(f"Sending message: {message} to channel: {channel}")
    data = {
        'channel': channel,
        'text': message,
    }
    response = http.request('POST', slack_post_channel_url, headers=headers, body=json.dumps(data))


def format_message(message: dict):
    output = f":rotating_light: *Slackbot Daily Report For {yesterday.strftime('%m/%d/%Y')}*: :rotating_light:\n"

    for k, v in message.items():
        output += f" - {k} = {int(v)} \n"

    return output


@logger.inject_lambda_context(log_event=True)
@metrics.log_metrics(raise_on_empty_metrics=True, capture_cold_start_metric=True)
@tracer.capture_lambda_handler
def lambda_handler(event: dict, context: LambdaContext):
    child_channel = ssm_client.get_parameter(Name=child_channel_param_name)['Parameter']['Value']

    data = {}
    for metric in report_metrics:
        data[metric] = get_metric(metric_name=metric)

    message = format_message(data)

    metrics.add_dimension(
        name="Application",
        value="Radiuss"
    )
    metrics.add_metric(
        name="MetricsLambdaInvocation",
        unit=MetricUnit.Count,
        value=1,
        resolution=MetricResolution.High
    )

    send_message(
        channel=child_channel,
        message=message
    )

    return {
        'statusCode': 200,
        'body': json.dumps({'msg': "Success!"})
    }




