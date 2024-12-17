import boto3
import os
import json
import urllib3
from constants import slack_post_channel_url, feedback_text

from aws_lambda_powertools import Logger

logger = Logger()

http = urllib3.PoolManager()

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


def respond_to_question(channel, slack_user, ts, msg, sources):

    if not verify_slack_token(slack_token):
        Exception("Invalid Slack token or wrong workspace.")

    # Respond in message thread
    chatbot_response = msg + '\n' + sources + feedback_text
    data = {
        'channel': channel,
        'text': f"<@{slack_user}>" + chatbot_response,
        'thread_ts': ts,
    }
    response = http.request('POST', slack_post_channel_url, headers=headers, body=json.dumps(data))
    response = json.loads(response.data.decode('utf-8'))
    logger.info(f"Chatbot response: {response}")
