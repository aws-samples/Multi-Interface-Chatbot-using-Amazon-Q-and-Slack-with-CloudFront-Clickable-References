import aws_cdk as cdk
from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_kendra as kendra,
    aws_lambda as lambda_,
    custom_resources as cr,
    aws_ec2 as ec2
)
from cdk_nag import NagSuppressions

REGION = cdk.Aws.REGION
ACCOUNT_ID = cdk.Aws.ACCOUNT_ID


class DataStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        NagSuppressions.add_stack_suppressions(self, [
            {"id": "AwsSolutions-IAM4", "reason": "Lambda basic execution role is exempt"},
            {"id": "AwsSolutions-IAM5", "reason": "IAM roles require wildcard permissions for CloudWatch Logs and "
                                                  "service needs access to the entire S3 Bucket"},
            {"id": "AwsSolutions-L1", "reason": "AwsCustomResource is controlled internally by CDK"},
            {"id": "AwsSolutions-CFR4", "reason": "Only for a POC"},
            {"id": "AwsSolutions-CFR7", "reason": "Only for POC"},
        ])



        self.vpc = ec2.Vpc(self, "VPC")

        self.vpc.add_flow_log(
            "FlowLogS3",
            destination=ec2.FlowLogDestination.to_cloud_watch_logs()
        )

        self.vpc.add_flow_log(
            "FlowLogCloudWatch",
            traffic_type=ec2.FlowLogTrafficType.REJECT,
            max_aggregation_interval=ec2.FlowLogMaxAggregationInterval.ONE_MINUTE
        )

        self.logs_bucket = s3.Bucket(
            self, f"LogsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED
        )

        self.raw_documentation_document_ingestion_bucket = s3.Bucket(
            self, "RawDocumentationDocumentIngestionBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            server_access_logs_bucket=self.logs_bucket
        )

        s3_deploy.BucketDeployment(
            self, "DocumentationDeployDocuments",
            sources=[s3_deploy.Source.asset("documents/raw_documentation")],
            destination_bucket=self.raw_documentation_document_ingestion_bucket,
        )

        self.processed_documentation_document_ingestion_bucket = s3.Bucket(
            self, "ProcessedDocumentationDocumentIngestionBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            server_access_logs_bucket=self.logs_bucket
        )

        self.raw_slack_document_ingestion_bucket = s3.Bucket(
            self, "RawSlackDocumentIngestionBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            server_access_logs_bucket=self.logs_bucket
        )

        s3_deploy.BucketDeployment(
            self, "SlackDeployDocuments",
            sources=[s3_deploy.Source.asset("documents/slack")],
            destination_bucket=self.raw_slack_document_ingestion_bucket,
        )

        self.processed_slack_document_ingestion_bucket = s3.Bucket(
            self, "ProcessedSlackDocumentIngestionBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            server_access_logs_bucket=self.logs_bucket
        )

        # Creates a Cloudfront distribution distribution from an S3 bucket.
        self.slack_document_distribution = cloudfront.Distribution(
            self, "SlackDocumentDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(self.raw_slack_document_ingestion_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS
            ),
            enable_logging=True,
            log_bucket=self.logs_bucket,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,

        )

        self.cloudfront_slack_distribution_prefix = self.slack_document_distribution.distribution_domain_name

        self.kendra_data_source_role = iam.Role(
            self, "KendraRole",
            assumed_by=iam.ServicePrincipal("kendra.amazonaws.com")
        )

        self.kendra_bucket_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetObject",
                "s3:ListBucket",
            ],
            resources=[
                f"{self.processed_slack_document_ingestion_bucket.bucket_arn}",
                f"{self.processed_slack_document_ingestion_bucket.bucket_arn}/*",
                f"{self.processed_documentation_document_ingestion_bucket.bucket_arn}",
                f"{self.processed_documentation_document_ingestion_bucket.bucket_arn}/*",
            ]
        )

        self.kendra_index_role = iam.Role(
            self, "KendraIndexRole",
            assumed_by=iam.ServicePrincipal("kendra.amazonaws.com")
        )

        self.logs_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "cloudwatch:PutMetricData",
                "logs:DescribeLogGroups",
                "logs:CreateLogGroup",
                'logs:DescribeLogStreams',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            resources=["*"]
        )
        self.kendra_index_role.add_to_policy(self.logs_policy)

        self.create_network_interface_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:CreateNetworkInterface",
                "ec2:CreateNetworkInterfacePermission",
                "ec2:DescribeSubnets",
                "ec2:DescribeNetworkInterfaces",
                "ec2:CreateTags"
            ],
            resources=["*"]
        )
        self.kendra_data_source_role.add_to_policy(self.create_network_interface_policy)
        self.kendra_data_source_role.add_to_policy(self.kendra_bucket_policy)

        self.kendra_index_role.add_to_policy(self.logs_policy)

        # Kendra Index
        self.kendra_index = kendra.CfnIndex(
            self, "KendraIndex",
            name="RadiussKendraIndex",
            edition="ENTERPRISE_EDITION",
            role_arn=self.kendra_index_role.role_arn,
            document_metadata_configurations=[
                kendra.CfnIndex.DocumentMetadataConfigurationProperty(
                    name="data_source",
                    type="STRING_VALUE",
                    relevance=kendra.CfnIndex.RelevanceProperty(
                        value_importance_items=[
                            kendra.CfnIndex.ValueImportanceItemProperty(
                                key="documentation",
                                value=8
                            ),
                            kendra.CfnIndex.ValueImportanceItemProperty(
                                key="slack",
                                value=3
                            )
                        ]
                    ),
                    search=kendra.CfnIndex.SearchProperty(
                        displayable=True,
                        facetable=True,
                        searchable=True,
                        sortable=True
                    )
                ),

                # Remove this code block
                kendra.CfnIndex.DocumentMetadataConfigurationProperty(
                    name="Title",
                    type="STRING_VALUE",
                    relevance=kendra.CfnIndex.RelevanceProperty(
                        importance=10
                    ),
                    search=kendra.CfnIndex.SearchProperty(
                        displayable=True,
                        facetable=True,
                        searchable=True,
                        sortable=True
                    )
                ),


                kendra.CfnIndex.DocumentMetadataConfigurationProperty(
                    name="_document_title",
                    type="STRING_VALUE",
                    relevance=kendra.CfnIndex.RelevanceProperty(
                        importance=10
                    ),
                    search=kendra.CfnIndex.SearchProperty(
                        displayable=True,
                        facetable=True,
                        searchable=True,
                        sortable=True
                    )
                )
            ]
        )

        self.kendra_mapping_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "kendra:PutPrincipalMapping",
                "kendra:DeletePrincipalMapping",
                "kendra:ListGroupsOlderThanOrderingId",
                "kendra:DescribePrincipalMapping",
                "kendra:BatchPutDocument",
                "kendra:BatchDeleteDocument"
            ],
            resources=[self.kendra_index.attr_arn]
        )

        self.kendra_data_source_role.add_to_policy(self.kendra_mapping_policy)

        # Create a Kendra Data Source
        self.slack_kendra_data_source = kendra.CfnDataSource(
            self, "SlackKendraDataSource",
            name="SlackDataSource",
            index_id=self.kendra_index.attr_id,
            type="S3",
            data_source_configuration={
                "s3Configuration": {
                    "bucketName": self.processed_slack_document_ingestion_bucket.bucket_name
                }
            },
            role_arn=self.kendra_data_source_role.role_arn,
        )
        self.slack_kendra_data_source.node.add_dependency(self.kendra_index)

        self.documentation_kendra_data_source = kendra.CfnDataSource(
            self, "DocumentationKendraDataSource",
            name="DocumentationDataSource",
            index_id=self.kendra_index.attr_id,
            type="S3",
            data_source_configuration={
                "s3Configuration": {
                    "bucketName": self.processed_documentation_document_ingestion_bucket.bucket_name
                }
            },
            role_arn=self.kendra_data_source_role.role_arn,
        )
        self.documentation_kendra_data_source.node.add_dependency(self.kendra_index)

        self.documentation_processing_lambda_role = iam.Role(
            self, "RadiussDocumentationProcessingLambdaRole",
            role_name="Radiuss_Documentation_Processing_Lambda_Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )

        self.documentation_processing_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        self.documentation_processing_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        )

        self.raw_documentation_document_ingestion_bucket.grant_read(self.documentation_processing_lambda_role)
        self.processed_documentation_document_ingestion_bucket.grant_read_write(self.documentation_processing_lambda_role)

        self.documentation_processing_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["kendra:StartDataSourceSyncJob"],
                resources=[
                    f'{self.kendra_index.attr_arn}',
                    f'{self.documentation_kendra_data_source.attr_arn}'
                ]
            ),
        )

        self.documentation_processing_lambda = lambda_.Function(
            self, "DocumentationProcessingLambda",
            function_name="documentation_processing_lambda",
            code=lambda_.Code.from_asset(
                "lambdas/documentation_processing",
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
            memory_size=1024,
            role=self.documentation_processing_lambda_role,
            environment={
                "POWERTOOLS_METRICS_NAMESPACE": "radiuss",
                "POWERTOOLS_SERVICE_NAME": "radiuss",
                "raw_bucket_name": self.raw_documentation_document_ingestion_bucket.bucket_name,
                "processed_bucket_name": self.processed_documentation_document_ingestion_bucket.bucket_name,
                "kendra_index_id": self.kendra_index.attr_id,
                "kendra_data_source_id": self.documentation_kendra_data_source.attr_id,
            },
            vpc=self.vpc
        )

        self.slack_processing_lambda_role = iam.Role(
            self, "SlackProcessingLambdaRole",
            role_name="Slack_Processing_Lambda_Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )

        self.slack_processing_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
        )
        self.slack_processing_lambda_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        )

        self.slack_processing_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["kendra:StartDataSourceSyncJob"],
                resources=[
                    f'{self.kendra_index.attr_arn}',
                    f'{self.slack_kendra_data_source.attr_arn}'
                ]
            ),
        )
        self.raw_slack_document_ingestion_bucket.grant_read(self.slack_processing_lambda_role)
        self.processed_slack_document_ingestion_bucket.grant_write(self.slack_processing_lambda_role)

        self.slack_processing_lambda = _lambda.Function(
            self, "SlackProcessingLambda",
            function_name="slack_processing_lambda",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset(
                "lambdas/slack_processing",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ]
                )
            ),
            environment={
                "cloudfront_distribution_prefix": self.cloudfront_slack_distribution_prefix,
                "raw_bucket": self.raw_slack_document_ingestion_bucket.bucket_name,
                "processed_bucket": self.processed_slack_document_ingestion_bucket.bucket_name,
                "kendra_index_id": self.kendra_index.attr_id,
                "kendra_data_source_id": self.slack_kendra_data_source.attr_id,
                "POWERTOOLS_METRICS_NAMESPACE": "radiuss",
                "POWERTOOLS_SERVICE_NAME": "radiuss",
            },
            timeout=Duration.minutes(15),
            memory_size=1024,
            role=self.slack_processing_lambda_role,
            vpc=self.vpc
        )

        cr.AwsCustomResource(
            scope=self,
            id="DocumentationProcessingLambdaCustomResource",
            policy=(
                cr.AwsCustomResourcePolicy.from_statements(
                    statements=[
                        iam.PolicyStatement(
                            actions=["lambda:InvokeFunction"],
                            effect=iam.Effect.ALLOW,
                            resources=[self.documentation_processing_lambda.function_arn],
                        ),
                        iam.PolicyStatement(
                            actions=["ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"],
                            effect=iam.Effect.ALLOW,
                            resources=["*"],
                        ),
                    ],
                )
            ),
            timeout=Duration.minutes(15),
            on_create=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": self.documentation_processing_lambda.function_arn,
                    "InvocationType": "RequestResponse",
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    "JobSenderTriggerPhysicalId",
                ),
            ),
        )

        cr.AwsCustomResource(
            scope=self,
            id="SlackProcessingLambdaCustomResource",
            policy=(
                cr.AwsCustomResourcePolicy.from_statements(
                    statements=[
                        iam.PolicyStatement(
                            actions=["lambda:InvokeFunction"],
                            effect=iam.Effect.ALLOW,
                            resources=[self.slack_processing_lambda.function_arn],
                        ),
                        iam.PolicyStatement(
                            actions=["ec2:DescribeSubnets", "ec2:DescribeSecurityGroups"],
                            effect=iam.Effect.ALLOW,
                            resources=["*"],
                        ),
                    ],
                )
            ),
            timeout=Duration.minutes(15),
            on_create=cr.AwsSdkCall(
                service="Lambda",
                action="invoke",
                parameters={
                    "FunctionName": self.slack_processing_lambda.function_arn,
                    "InvocationType": "RequestResponse",
                },
                physical_resource_id=cr.PhysicalResourceId.of(
                    "JobSenderTriggerPhysicalId",
                ),
            ),
        )

