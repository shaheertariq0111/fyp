import boto3
from botocore.config import Config

from .config import Settings


def get_s3_client(settings: Settings):
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )
