from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOCAL_FRONTEND_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Content-Type"]
CORS_EXPOSE_HEADERS = ["X-Request-ID", "X-Agent-Request-ID"]


def parse_frontend_cors_origins(raw_value: str | None, environment: str = "local") -> list[str]:
    origins = [origin.strip().rstrip("/") for origin in (raw_value or "").split(",") if origin.strip()]
    if not origins and environment in {"local", "test"}:
        return LOCAL_FRONTEND_CORS_ORIGINS
    if not origins:
        raise ValueError("FRONTEND_CORS_ORIGINS is required outside local/test")
    if any(origin == "*" for origin in origins):
        raise ValueError("FRONTEND_CORS_ORIGINS must not use wildcard origins")
    if environment not in {"local", "test"} and any("localhost" in origin or "127.0.0.1" in origin for origin in origins):
        raise ValueError("FRONTEND_CORS_ORIGINS must use exact deployed frontend origins outside local/test")
    return origins


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
    frontend_cors_origins: str = ""
    admin_username: str = ""
    admin_password: str = ""
    admin_session_secret: str = ""
    admin_session_ttl_hours: int = Field(default=8, gt=0)

    @model_validator(mode="after")
    def validate_environment(self) -> "Settings":
        if self.environment != "test" and not self.bedrock_model_id:
            raise ValueError("BEDROCK_MODEL_ID is required outside tests")
        parse_frontend_cors_origins(self.frontend_cors_origins, self.environment)
        return self

    def parsed_frontend_cors_origins(self) -> list[str]:
        return parse_frontend_cors_origins(self.frontend_cors_origins, self.environment)

    def cross_site_admin_cookie(self) -> bool:
        return self.environment in {"staging", "production"}

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
