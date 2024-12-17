import aws_cdk as cdk
from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    aws_iam as iam,
    custom_resources as cr,
    aws_qbusiness as qbusiness,
    aws_cloudwatch as cw,
)
import json

REGION = cdk.Aws.REGION
ACCOUNT_ID = cdk.Aws.ACCOUNT_ID
Q_APPLICATION_NAME = "Radiuss"
Q_APPLICATION_DESCRIPTION = "Spack Slack Chatbot"
Q_APPLICATION_WELCOME_MESSAGE = "Hi, I'm your Spack Slack Chatbot AI assistant, ask some Spack related questions. I'll respond by using Retrieval-Augmented Generation (RAG) to reference the data from the S3 bucket and the Spack website documentation."
DATASOURCE_NAME = "DocumentIngestion"
DATASOURCE_DESCRIPTION = "PDF Document ingestion"

from cdk_nag import NagSuppressions

class AmazonQStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, data_stack, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        NagSuppressions.add_stack_suppressions(self, [
            {"id": "AwsSolutions-L1", "reason": "AwsCustomResource is controlled internally by CDK"},
            {"id": "AwsSolutions-IAM5", "reason": "IAM roles require wildcard permissions for CloudWatch Logs"},
        ])

        # create a custom resouce execution role to have sso access
        custom_res_role = iam.Role(
            self,
            "QStackS3CustomResRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            role_name="QStackS3CustomResRole",
        )

        # fetch identity center arn
        sso_instance_arn_fetcher = cr.AwsCustomResource(
            self, "SSOInstanceArnFetcher",
            role=custom_res_role,
            timeout=Duration.minutes(10),
            install_latest_aws_sdk=True,
            on_create=cr.AwsSdkCall(
                service="@aws-sdk/client-sso-admin",
                action="ListInstances",
                physical_resource_id=cr.PhysicalResourceId.of("SSOInstanceArnFetcher"),
                output_paths=[
                    "Instances.0.InstanceArn"
                ],  # Extract the first InstanceArn
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
        )

        idc_instance_arn = sso_instance_arn_fetcher.get_response_field(
            "Instances.0.InstanceArn"
        )

        # Q Application Access Policy 1
        q_app_cloudwatch_policy = iam.PolicyStatement(
            sid="AmazonQApplicationPutMetricDataPermission",
            effect=iam.Effect.ALLOW,
            actions=[
                "cloudwatch:PutMetricData",
            ],
            conditions={
                "StringEquals": {"cloudwatch:namespace": "AWS/QBusiness"}
            },
            resources=["*"]
        )

        # Q Application Access Policy 2
        q_app_describe_logs_policy = iam.PolicyStatement(
            sid="AmazonQApplicationDescribeLogGroupsPermission",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:DescribeLogGroups",
            ],
            resources=["*"]
        )

        # Q Application Access Policy 3
        q_app_create_logs_policy = iam.PolicyStatement(
            sid="AmazonQApplicationCreateLogGroupPermission",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
            ],
            resources=[f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/qbusiness/*"]
        )

        # Q Application Access Policy 4
        q_app_logstream_policy = iam.PolicyStatement(
            sid="AmazonQApplicationLogStreamPermission",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:DescribeLogStreams",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources=[f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/qbusiness/*:log-stream:*"]
        )

        # Q Application Access Policy 5
        q_app_create_app_policy = iam.PolicyStatement(
            sid="AmazonQApplicationCreateApplication",
            effect=iam.Effect.ALLOW,
            actions=[
                "qbusiness:CreateApplication",
                "qbusiness:DescribeApplication",
                "qbusiness:DeleteApplication",
            ],
            resources=["*"]
        )

        # Create q application role
        q_app_role = iam.Role(
            self, "QBusinessApplication",
            assumed_by=iam.ServicePrincipal(
                service="qbusiness.amazonaws.com",
            ),
        )

        q_app_role.add_to_policy(q_app_cloudwatch_policy)
        q_app_role.add_to_policy(q_app_describe_logs_policy)
        q_app_role.add_to_policy(q_app_create_logs_policy)
        q_app_role.add_to_policy(q_app_logstream_policy)
        q_app_role.add_to_policy(q_app_create_app_policy)


        # Create Q Application
        q_application_res = qbusiness.CfnApplication(
            self, "QApplication",
            display_name=Q_APPLICATION_NAME,
            attachments_configuration=qbusiness.CfnApplication.AttachmentsConfigurationProperty(
                attachments_control_mode="ENABLED"
            ),
            description=Q_APPLICATION_DESCRIPTION,
            role_arn=q_app_role.role_arn,
            identity_center_instance_arn=idc_instance_arn,
            q_apps_configuration=qbusiness.CfnApplication.QAppsConfigurationProperty(
                q_apps_control_mode="DISABLED"
            ),
        )

        # Wait for Instance ID of IdC
        q_application_res.node.add_dependency(sso_instance_arn_fetcher)

        retriever_role = iam.Role(
            self, "RetrieverRole",
            assumed_by=iam.ServicePrincipal("application.qbusiness.amazonaws.com"),
        )

        kendra_retrieve_policy = iam.PolicyStatement(
            actions=[
                "kendra:Retrieve",
                "kendra:DescribeIndex"
            ],
            resources=[data_stack.kendra_index.attr_arn],
        )

        q_app_role.add_to_policy(kendra_retrieve_policy)

        kms_policy = iam.PolicyStatement(
            actions=["kms:Decrypt"],
            resources=[
                f"arn:aws:kms:{REGION}:{ACCOUNT_ID}:key/*"  # Update key id if needed
            ],
        )
        retriever_role.add_to_policy(kms_policy)

        retriever_res = qbusiness.CfnRetriever(
            self, "QApplicationRetriever",
            role_arn=q_app_role.role_arn,
            type="KENDRA_INDEX",
            display_name=f"{Q_APPLICATION_NAME}-retriever",
            application_id=q_application_res.attr_application_id,
            configuration=qbusiness.CfnRetriever.RetrieverConfigurationProperty(
                kendra_index_configuration=qbusiness.CfnRetriever.KendraIndexConfigurationProperty(
                    index_id=data_stack.kendra_index.attr_id
                )
            )
        )
        retriever_res.node.add_dependency(data_stack.kendra_index)

        # create a webexperience role
        webexperience_role = iam.Role(
            self, "WebExperienceRole",
            assumed_by=iam.ServicePrincipal("application.qbusiness.amazonaws.com"),
        )

        webexperience_role.add_to_policy(q_app_create_app_policy)

        webexperience_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "qbusiness:Chat",
                    "qbusiness:ChatSync",
                    "qbusiness:ListMessages",
                    "qbusiness:ListConversations",
                    "qbusiness:DeleteConversation",
                    "qbusiness:PutFeedback",
                    "qbusiness:GetWebExperience",
                    "qbusiness:GetApplication",
                    "qbusiness:ListPlugins",
                    "qbusiness:GetChatControlsConfiguration",
                    "qbusiness:DeleteRetriever",
                ],
                resources=[q_application_res.attr_application_arn],
            )
        )

        webexperience_role.add_to_policy(kms_policy)
        webexperience_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "qapps:CreateQApp",
                    "qapps:PredictProblemStatementFromConversation",
                    "qapps:PredictQAppFromProblemStatement",
                    "qapps:CopyQApp",
                    "qapps:GetQApp",
                    "qapps:ListQApps",
                    "qapps:UpdateQApp",
                    "qapps:DeleteQApp",
                    "qapps:AssociateQAppWithUser",
                    "qapps:DisassociateQAppFromUser",
                    "qapps:ImportDocumentToQApp",
                    "qapps:ImportDocumentToQAppSession",
                    "qapps:CreateLibraryItem",
                    "qapps:GetLibraryItem",
                    "qapps:UpdateLibraryItem",
                    "qapps:CreateLibraryItemReview",
                    "qapps:ListLibraryItems",
                    "qapps:CreateSubscriptionToken",
                    "qapps:StartQAppSession",
                    "qapps:StopQAppSession",
                ],
                resources=[q_application_res.attr_application_arn],
            )
        )


        # add the assumeRolePolicy to the role
        webexperience_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                sid="QBusinessTrustPolicy",
                effect=iam.Effect.ALLOW,
                principals=[
                    iam.ServicePrincipal("application.qbusiness.amazonaws.com")
                ],
                actions=["sts:AssumeRole", "sts:SetContext"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": f"{ACCOUNT_ID}"},
                    "ArnEquals": {
                        "aws:SourceArn": q_application_res.attr_application_arn
                    },
                },
            )
        )

        # create Amazon Q Web Experience
        webexperience_res = qbusiness.CfnWebExperience(
            self,
            "QApplicationWebExperience",
            application_id=q_application_res.attr_application_id,
            role_arn=webexperience_role.role_arn,
            subtitle=Q_APPLICATION_DESCRIPTION,
            title=Q_APPLICATION_NAME,
            welcome_message=Q_APPLICATION_WELCOME_MESSAGE,
        )

        webexperience_res.node.add_dependency(q_application_res)

        self.cloudwatch_dashboard = cw.CfnDashboard(
            self, "AmazonQCloudwatchDashboard",
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
                                "view": "timeSeries",
                                "stacked": False,
                                "metrics": [
                                    ["AWS/QBusiness", "NewConversations", "ApplicationId", q_application_res.attr_application_id],
                                    [".", "ChatMessages", ".", "."],
                                    [".", "ChatMessagesWithAttachment", ".", "."],
                                    [".", "ChatMessagesWithNoAnswer", ".", "."]
                                ],
                                "title": "spack",
                                "region": self.region,
                                "period": 604800,
                                "stat": "Sum"
                            }
                        }
                    ]
                }
            ),
            dashboard_name="AmazonQDashboard"
        )

        cdk.CfnOutput(
            self, "AmazonQCloudwatchDashboardOutput",
            value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home?region={self.region}#dashboards/dashboard/{self.cloudwatch_dashboard.dashboard_name}",
            description="Amazon Q Dashboard"
        )

