import boto3
from botocore.config import Config

from .config import Settings


def get_bedrock_runtime_client(settings: Settings):
    return boto3.client(
        "bedrock-runtime",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


def get_bedrock_agent_runtime_client(settings: Settings):
    return boto3.client(
        "bedrock-agent-runtime",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )
