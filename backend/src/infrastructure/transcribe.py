import boto3
from botocore.config import Config

from .config import Settings


def get_transcribe_client(settings: Settings):
    return boto3.client(
        "transcribe",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )
