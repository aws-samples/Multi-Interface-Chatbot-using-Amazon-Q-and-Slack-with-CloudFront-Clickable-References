import boto3
import json
import os

from aws_lambda_powertools import Logger
logger = Logger()

bedrock = boto3.client("bedrock-runtime", region_name=os.environ['AWS_REGION'])
kendra = boto3.client("kendra")
kendra_index_id = os.environ['kendra_index_id']
model_id = os.environ['model_id']


def call_bedrock(prompt):
    native_request = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "temperature": 0.5,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
    }

    request = json.dumps(native_request)

    try:
        response = bedrock.invoke_model(modelId=model_id, body=request)

    except Exception as e:
        logger.error(f"ERROR: Can't invoke '{model_id}'. Reason: {e}")
        raise e

    model_response = json.loads(response["body"].read())

    response_text = model_response["content"][0]["text"]
    logger.info({"response_text": response_text})
    return response_text.replace("<template>", "").replace("</template>", "")


def kendra_retrieve(query):
    return kendra.retrieve(
        IndexId=kendra_index_id,
        QueryText=query[:999]
    )
