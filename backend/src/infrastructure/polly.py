from __future__ import annotations

import boto3
from botocore.config import Config

from .config import Settings


def get_polly_client(settings: Settings, *, client_factory=None):
    """Create a same-account Polly client from the worker credential chain."""
    factory = client_factory or boto3.client
    timeout = settings.voice_reply_synthesis_timeout_seconds
    return factory(
        "polly",
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )
