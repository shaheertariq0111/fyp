from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .config import Settings


SECRET_LOAD_FAILED = "SECRET_VALUE_LOAD_FAILED"
SECRET_VALUE_INVALID = "SECRET_VALUE_INVALID"


class SecretLoadError(RuntimeError):
    """Sanitized failure raised while loading one configured secret."""


class SecretsManagerSecretLoader:
    def __init__(self, client, secret_arn: str) -> None:
        if not isinstance(secret_arn, str) or not secret_arn.strip():
            raise ValueError("SECRET_ARN_REQUIRED")
        self.client = client
        self.secret_arn = secret_arn.strip()

    def load(self) -> str:
        try:
            response = self.client.get_secret_value(SecretId=self.secret_arn)
        except (ClientError, BotoCoreError):
            raise SecretLoadError(SECRET_LOAD_FAILED) from None
        value = response.get("SecretString") if isinstance(response, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise SecretLoadError(SECRET_VALUE_INVALID)
        return value


def get_secretsmanager_client(settings: Settings, *, client=None):
    if client is not None:
        return client
    return boto3.client(
        "secretsmanager",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )
