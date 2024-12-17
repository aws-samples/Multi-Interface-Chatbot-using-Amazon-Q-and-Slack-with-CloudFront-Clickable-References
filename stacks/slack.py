from aws_cdk import (
    Stack,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_secretsmanager as secretsmanager,
    aws_apigatewayv2 as apigwv2,
    aws_cloudwatch as cw,
    aws_ssm as ssm,
    aws_logs as logs,
    SecretValue,
    Duration,
)

import aws_cdk as cdk
from constructs import Construct
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from cdk_nag import NagSuppressions
import json


class SlackStack(Stack):
    def __init__(self, scope: Construct, id: str, data_stack, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        NagSuppressions.add_stack_suppressions(self, [
            {"id": "AwsSolutions-IAM4", "reason": "Custom roles are used with specific permissions"},
            {"id": "AwsSolutions-IAM5", "reason": "IAM roles require wildcard permissions for CloudWatch Logs"},
            {"id": "AwsSolutions-APIG4", "reason": "API does not have authorization"},
            {"id": "AwsSolutions-SMG4", "reason": "Secret cannot be rotated"},
            {"id": "AwsSolutions-APIG1", "reason": "Logging is enabled"},
        ])

        self.bedrock_model_id = "anthropic.claude-v2:1"
        self.kendra = data_stack.kendra_index
        self.parent_channel_param_name = "/Radiuss/Spack/ParentChannelId"
        self.child_channel_param_name = "/Radiuss/Spack/ChildChannelId"
        self.slackbot_member_id_param_name = "/Radiuss/Spack/SlackbotMemberId"

        self.slack_bot_token = secretsmanager.Secret(
            self, "SlackAccessKey",
            secret_object_value={
              "token": SecretValue.unsafe_plain_text("place-holder-access-key"),
            }
        )


        self.parent_channel_param = ssm.StringParameter(
            self, "ParentChannelStringParameter",
            allowed_pattern=".*",
            description="Slack parent channel ID",
            parameter_name=self.parent_channel_param_name,
            string_value="INPUT_PARENT_CHANNEL_ID_HERE",
            tier=ssm.ParameterTier.STANDARD
        )

        self.child_channel_param = ssm.StringParameter(
            self, "ChildChannelStringParameter",
            allowed_pattern=".*",
            description="Slack child/mirror channel ID",
            parameter_name=self.child_channel_param_name,
            string_value="INPUT_CHILD_CHANNEL_ID_HERE",
            tier=ssm.ParameterTier.STANDARD
        )

        self.slackbot_member_id_param = ssm.StringParameter(
            self, "SlackbotMemberIdStringParameter",
            allowed_pattern=".*",
            description="Slackbot member ID",
            parameter_name=self.slackbot_member_id_param_name,
            string_value="INPUT_SLACKBOT_MEMBER_ID_HERE",
            tier=ssm.ParameterTier.STANDARD
        )

        cdk.CfnOutput(
            self, "ChildChannelStringParameterOutput",
            value=self.child_channel_param.parameter_arn,
            description="Child channel ID parameter"
        )

        # Create Lambda role and policy
        self.slackbot_lambda_policy = iam.Policy(
            self, "SlackbotLambdaPolicy",
            policy_name="User_Policies_Slackbot_Lambda",
            statements=[
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel"],
                    resources=[f"arn:aws:bedrock:{cdk.Aws.REGION}::foundation-model/{self.bedrock_model_id}"],
                    effect=iam.Effect.ALLOW,
                ),
                iam.PolicyStatement(
                    actions=[
                        "kendra:Retrieve",
                    ],
                    resources=[self.kendra.attr_arn],
                    effect=iam.Effect.ALLOW,
                ),
            ],
        )
        self.slackbot_lambda_role = iam.Role(
            self, "RadiussLambdaRole",
            role_name="User_Roles_Radiuss_Lambda_Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        self.slack_bot_token.grant_read(self.slackbot_lambda_role)
        self.parent_channel_param.grant_read(self.slackbot_lambda_role)
        self.slackbot_member_id_param.grant_read(self.slackbot_lambda_role)

        self.slackbot_lambda_role.attach_inline_policy(self.slackbot_lambda_policy)
        self.slackbot_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        self.slackbot_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        )

        # Create Lambda function
        self.slackbot_lambda_function = lambda_.Function(
            self, "RadiussSlackLambda",
            function_name="slackbot",
            code=lambda_.Code.from_asset(
                "lambdas/slack_bot",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ]
                )
            ),
            handler="index.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            timeout=Duration.seconds(90),
            role=self.slackbot_lambda_role,
            environment={
                "kendra_index_id": self.kendra.attr_id,
                "model_id": self.bedrock_model_id,
                "slack_token_arn": self.slack_bot_token.secret_full_arn,
                "POWERTOOLS_METRICS_NAMESPACE": "radiuss",
                "POWERTOOLS_SERVICE_NAME": "radiuss",
                "parent_channel_param_name": self.parent_channel_param_name,
                "slackbot_member_id_param_name": self.slackbot_member_id_param_name,
            },
            vpc=data_stack.vpc
        )

        self.metrics_lambda_policy = iam.Policy(
            self, "MetricsLambdaPolicy",
            policy_name="User_Policies_Metrics_Lambda",
            statements=[
                iam.PolicyStatement(
                    actions=["cloudwatch:GetMetricStatistics"],
                    resources=["*"],
                    effect=iam.Effect.ALLOW,
                ),
            ]
        )

        self.metrics_lambda_role = iam.Role(
            self, "RadiussMetricsLambdaRole",
            role_name="Radiuss_Metrics_Lambda_Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        self.slack_bot_token.grant_read(self.metrics_lambda_role)
        self.child_channel_param.grant_read(self.metrics_lambda_role)

        self.metrics_lambda_role.attach_inline_policy(self.metrics_lambda_policy)
        self.metrics_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        self.metrics_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        )

        # Create Lambda function
        self.metrics_lambda_function = lambda_.Function(
            self, "RadiussMetricsLambda",
            function_name="metrics",
            code=lambda_.Code.from_asset(
                "lambdas/metrics",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ]
                )
            ),
            handler="index.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            timeout=Duration.seconds(90),
            role=self.metrics_lambda_role,
            environment={
                "slack_token_arn": self.slack_bot_token.secret_full_arn,
                "child_channel_param_name": self.child_channel_param_name,
                "POWERTOOLS_METRICS_NAMESPACE": "radiuss",
                "POWERTOOLS_SERVICE_NAME": "radiuss"
            },
            vpc=data_stack.vpc
        )

        # Define the HTTP API
        self.slack_endpoint = apigwv2.HttpApi(
            self, "SlackBotEndpoint",
            description="Proxy for Bedrock Slack bot backend."
        )

        # Create a CloudFormation output for the Slack bot endpoint URL
        cdk.CfnOutput(
            self, "SlackBotEndpointOutput",
            value=self.slack_endpoint.url,
            description="Slackbot Endpoint"
        )

        # Create a log group for the API Gateway access logs
        self.api_gateway_log_group = logs.LogGroup(
            self, "SlackBotApiAccessLog",
            retention=logs.RetentionDays.SIX_MONTHS
        )

        # Configure the access log settings for the default stage
        self.slack_endpoint.default_stage.access_log_settings = apigwv2.CfnStage.AccessLogSettingsProperty(
            destination_arn=self.api_gateway_log_group.log_group_arn,
            format=cdk.Stack.of(self).to_json_string({
                "requestId": "$context.requestId",
                "ip": "$context.identity.sourceIp",
                "requestTime": "$context.requestTime",
                "httpMethod": "$context.httpMethod",
                "routeKey": "$context.routeKey",
                "status": "$context.status",
                "protocol": "$context.protocol",
                "responseLength": "$context.responseLength",
                "userAgent": "$context.identity.userAgent"
            }),
        )

        # Add a route to the HTTP API that integrates with a Lambda function
        self.slack_endpoint.add_routes(
            path="/",
            methods=[apigwv2.HttpMethod.ANY],
            integration=HttpLambdaIntegration(
                "BotHandlerIntegration",
                handler=self.slackbot_lambda_function,
            )
        )

        self.cloudwatch_dashboard = cw.CfnDashboard(
            self, "SlackCloudwatchDashboard",
            dashboard_body=json.dumps(
                {
                    "widgets": [
                        {
                            "type": "metric",
                            "x": 0,
                            "y": 0,
                            "width": 24,
                            "height": 14,
                            "properties": {
                                "metrics": [
                                    ["radiuss", "LambdaInvocation", "service", "radiuss", "Application", "Radiuss", {"region": self.region}],
                                    [".", "UrlVerification", ".", ".", ".", ".", {"region": self.region}],
                                    [".", "SlackBotLambdaInvocation", ".", ".", ".", ".", {"region": self.region}],
                                    [".", "MetricsLambdaInvocation", ".", ".", ".", ".", {"region": self.region}],
                                    [".", "Retry", ".", ".", ".", ".", {"region": self.region}],
                                    [".", "RespondToMessage", ".", ".", ".", ".", {"region": self.region}]
                                ],
                                "view": "timeSeries",
                                "stacked": False,
                                "title": "spack",
                                "region": self.region,
                                "period": 604800,
                                "stat": "Sum"
                            }
                        }
                    ]
                }
            ),
            dashboard_name="SlackDashboard"
        )

        cdk.CfnOutput(
            self, "SlackCloudwatchDashboardOutput",
            value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home?region={self.region}#dashboards/dashboard/{self.cloudwatch_dashboard.dashboard_name}",
            description="Slackbot Dashboard"
        )

        self.slack_ingest_lambda_role = iam.Role(
            self, "SlackIngestionLambdaRole",
            role_name="Slack_Ingestion_Lambda_Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        self.slack_ingest_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        self.slack_ingest_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        )
        self.slack_bot_token.grant_read(self.metrics_lambda_role)
        data_stack.processed_slack_document_ingestion_bucket.grant_write(self.slack_ingest_lambda_role)
        data_stack.raw_slack_document_ingestion_bucket.grant_write(self.slack_ingest_lambda_role)
        self.parent_channel_param.grant_read(self.slack_ingest_lambda_role)
        self.slackbot_member_id_param.grant_read(self.slack_ingest_lambda_role)

        self.slack_ingest_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["kendra:StartDataSourceSyncJob"],
                resources=[
                    f'{data_stack.kendra_index.attr_arn}',
                    f'{data_stack.slack_kendra_data_source.attr_arn}'
                ]
            ),
        )

        # Create Slack Ingestion Lambda function
        self.slack_ingest_lambda_function = lambda_.Function(
            self, "SlackIngestLambda",
            function_name="slack_ingest",
            code=lambda_.Code.from_asset(
                "lambdas/slack_ingest",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ]
                )
            ),
            handler="index.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            timeout=Duration.minutes(15),
            role=self.slack_ingest_lambda_role,
            environment={
                "slack_token_arn": self.slack_bot_token.secret_full_arn,
                "raw_bucket_name": data_stack.raw_slack_document_ingestion_bucket.bucket_name,
                "processed_bucket_name": data_stack.processed_slack_document_ingestion_bucket.bucket_name,
                "kendra_index_id": self.kendra.attr_id,
                "kendra_data_source_id": data_stack.slack_kendra_data_source.attr_id,
                "parent_channel_param_name": self.parent_channel_param_name,
                "cloudfront_distribution_prefix": data_stack.cloudfront_slack_distribution_prefix,
                "POWERTOOLS_METRICS_NAMESPACE": "radiuss",
                "POWERTOOLS_SERVICE_NAME": "radiuss"
            },
            vpc=data_stack.vpc
        ) 

        self.slack_bot_token.grant_read(self.slack_ingest_lambda_function)

        self.daily_schedule = events.Rule(
            self, "ScheduleRule",
            schedule=events.Schedule.cron(hour="0", minute="0"),
            targets=[
                targets.LambdaFunction(self.metrics_lambda_function),
                targets.LambdaFunction(self.slack_ingest_lambda_function)
            ]
        )

