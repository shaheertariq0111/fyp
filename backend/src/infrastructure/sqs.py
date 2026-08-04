from __future__ import annotations

import boto3
from botocore.config import Config


def create_sqs_client(*, region_name: str, client=None):
    if client is not None:
        return client
    return boto3.client(
        "sqs",
        region_name=region_name,
        config=Config(retries={"mode": "standard", "max_attempts": 3}),
    )
