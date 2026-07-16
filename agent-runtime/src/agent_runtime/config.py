from functools import lru_cache

import boto3
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentCoreRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

    environment: str = "production"
    agentcore_memory_id: str = ""
    log_level: str = "INFO"
    aws_region: str = Field(default="us-east-1", min_length=1)
    session_token_secret_arn: str = ""


@lru_cache
def get_agentcore_runtime_settings() -> AgentCoreRuntimeSettings:
    return AgentCoreRuntimeSettings()


@lru_cache
def get_secret_value(secret_arn: str, region_name: str) -> str:
    client = boto3.client("secretsmanager", region_name=region_name)
    response = client.get_secret_value(SecretId=secret_arn)
    secret = response.get("SecretString")
    if not secret:
        raise ValueError("SecretString is empty")
    return secret
