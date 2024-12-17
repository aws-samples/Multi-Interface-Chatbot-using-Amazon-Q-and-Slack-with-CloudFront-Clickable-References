from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools import Metrics
from aws_lambda_powertools.metrics import MetricUnit, MetricResolution
from aws_lambda_powertools import Tracer

import json
import boto3
import os

from slack import respond_to_question
from prompts import get_question_prompt, get_source_prompt
from rag import call_bedrock, kendra_retrieve


parent_channel_param_name = os.environ.get('parent_channel_param_name')
slackbot_member_id_param_name = os.environ.get('slackbot_member_id_param_name')

logger = Logger()
metrics = Metrics()
tracer = Tracer(service="Radiuss")

ssm_client = boto3.client('ssm')


@logger.inject_lambda_context(log_event=True)
@metrics.log_metrics(raise_on_empty_metrics=True, capture_cold_start_metric=True)
@tracer.capture_lambda_handler
def lambda_handler(event: dict, context: LambdaContext):
    logger.info(f"event: {event}")
    logger.info(f"context: {context}")

    execution_id = event['requestContext']['requestId']

    metrics.add_dimension(name="Application", value="Radiuss")
    metrics.add_metric(name="SlackBotLambdaInvocation", unit=MetricUnit.Count, value=1, resolution=MetricResolution.High)
    metrics.add_metadata(key="execution_id", value=execution_id)

    logger.info(f"Loading parameters")
    parent_channel = ssm_client.get_parameter(Name=parent_channel_param_name)['Parameter']['Value']
    slackbot_member_id = ssm_client.get_parameter(Name=slackbot_member_id_param_name)['Parameter']['Value']
    general_channel = parent_channel

    # Dont do anything if retry
    if 'x-slack-retry-num' in event['headers']:
        slk_retry = event['headers']['x-slack-retry-num']
        logger.info({"slk_retry": slk_retry})
        metrics.add_metric(
            name="Retry",
            unit=MetricUnit.Count,
            value=1,
            resolution=MetricResolution.High
        )
        metrics.add_metadata(key="execution_id", value=execution_id)
        return {
            'statusCode': 200,
            'body': json.dumps({'msg': f"Retry: {slk_retry}"})
        }

    slack_body = json.loads(event['body'])
    if slack_body.get("type") == "url_verification":
        logger.info("URL verification")
        metrics.add_metric(
            name="UrlVerification",
            unit=MetricUnit.Count,
            value=1,
            resolution=MetricResolution.High
        )
        metrics.add_metadata(key="execution_id", value=execution_id)
        return {
            'statusCode': 200,
            'body': slack_body['challenge']
        }

    event = slack_body.get('event')

    # Thumbs down
    if event['type'] == "reaction_added" and event['item_user'] == slackbot_member_id and event["reaction"] == "-1":
        logger.info("Thumbs down detected!")
        metrics.add_metric(
            name="ThumbsDown",
            unit=MetricUnit.Count,
            value=1,
            resolution=MetricResolution.High
        )
        metrics.add_metadata(key="execution_id", value=execution_id)

        logger.info(f"slack_body -> event -> item: {slack_body['event']['item']}")

        return {
            'statusCode': 200,
            'body': json.dumps({'msg': "Thumbs down processed"})
        }

    # Thumbs up
    elif event['type'] == "reaction_added" and event['item_user'] == slackbot_member_id and event["reaction"] == "+1":
        logger.info("Thumbs up detected!")
        metrics.add_metric(
            name="ThumbsUp",
            unit=MetricUnit.Count,
            value=1,
            resolution=MetricResolution.High
        )
        metrics.add_metadata(key="execution_id", value=execution_id)

        return {
            'statusCode': 200,
            'body': json.dumps({'msg': "Thumbs up processed"})
        }

    # respond to a message
    if (general_channel in event['channel']) and (event['user'] != slackbot_member_id):
        logger.info("Respond to message")
        metrics.add_metric(
            name="RespondToMessage",
            unit=MetricUnit.Count,
            value=1,
            resolution=MetricResolution.High
        )
        metrics.add_metadata(key="execution_id", value=execution_id)

        slack_text = event.get('text')
        user_prompt = slack_text.replace('<@U06D5B8AR8R>', '').replace("<@SpackChatbot>", "")

        passage_str = ""
        passage_w_links_str = ""
        for passages in kendra_retrieve(user_prompt)['ResultItems']:
            logger.info(f"passage: {passages['Content']}")
            passage_str += "\n\n\n" + passages['Content']
            passage_w_links_str += "\nSource Link: " + passages['DocumentURI']
            passage_w_links_str += "\n\n\n" + passages['Content'] + "---------------------------------------------"

        question_prompt = get_question_prompt(user_prompt, passage_str)
        source_prompt = get_source_prompt(user_prompt, passage_w_links_str)

        respond_to_question(
            channel=event.get('channel'),
            slack_user=event.get('user'),
            ts=event.get('ts'),
            msg=call_bedrock(question_prompt),
            sources=call_bedrock(source_prompt),
        )

        return {
            'statusCode': 200,
            'body': json.dumps({'msg': "message received"})
        }

    logger.info(f"Not meeting any criteria. Event type: {event['type']} Event Channel: {event['channel']}")
    metrics.add_metric(
        name="NotMeetingCriteria",
        unit=MetricUnit.Count,
        value=1,
        resolution=MetricResolution.High
    )

    metrics.add_metadata(key="execution_id", value=execution_id)

    return {
        'statusCode': 200,
        'body': json.dumps({'msg': "Unknown Criteria"})
    }
