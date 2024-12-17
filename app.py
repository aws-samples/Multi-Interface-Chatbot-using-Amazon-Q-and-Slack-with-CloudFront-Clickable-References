#!/usr/bin/env python3
import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from aws_cdk import Aspects
from stacks.amazonq import AmazonQStack
from stacks.slack import SlackStack
from stacks.data import DataStack

app = cdk.App()
data_stack = DataStack(app, "DataStack")
q_stack = AmazonQStack(app, "AmazonQStack", data_stack)
slack_stack = SlackStack(app, "SlackStack", data_stack)

Aspects.of(app).add(AwsSolutionsChecks())
app.synth()
