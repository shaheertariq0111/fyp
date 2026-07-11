from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    aws_region: str = Field(min_length=1)
    bedrock_model_id: str = ""
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = ""
    knowledge_base_id: str = ""
    knowledge_base_max_results: int = Field(default=5, gt=0, le=100)

    allow_aws_resource_creation: bool = False
    dynamodb_deletion_protection: bool = True
    dynamodb_point_in_time_recovery: bool = True
    dynamodb_resource_tags: str = ""
    menu_table_name: str = Field(min_length=1)
    carts_table_name: str = Field(min_length=1)
    orders_table_name: str = Field(min_length=1)
    customers_table_name: str = Field(min_length=1)
    agent_sessions_table_name: str = Field(min_length=1)
    agent_requests_table_name: str = Field(min_length=1)
    menu_sessions_table_name: str = Field(min_length=1)
    audit_table_name: str = Field(min_length=1)

    menu_site_base_url: HttpUrl
    session_token_secret: str = Field(min_length=16)
    session_token_ttl_minutes: int = Field(default=60, gt=0)
    agent_session_ttl_hours: int = Field(default=24, gt=0)
    agent_request_ttl_hours: int = Field(default=24, gt=0)
    strands_session_storage_dir: str | None = None
    restaurant_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    log_level: str = "INFO"
    admin_username: str = ""
    admin_password: str = ""
    admin_session_secret: str = ""
    admin_session_ttl_hours: int = Field(default=8, gt=0)

    @model_validator(mode="after")
    def validate_environment(self) -> "Settings":
        if self.environment != "test" and not self.bedrock_model_id:
            raise ValueError("BEDROCK_MODEL_ID is required outside tests")
        return self

    def parsed_dynamodb_tags(self) -> list[dict[str, str]]:
        tags = []
        for pair in filter(None, (part.strip() for part in self.dynamodb_resource_tags.split(","))):
            if "=" not in pair:
                raise ValueError("DYNAMODB_RESOURCE_TAGS entries must use key=value format")
            key, value = pair.split("=", 1)
            if not key.strip() or not value.strip():
                raise ValueError("DYNAMODB_RESOURCE_TAGS keys and values cannot be empty")
            tags.append({"Key": key.strip(), "Value": value.strip()})
        return tags


@lru_cache
def get_settings() -> Settings:
    return Settings()
