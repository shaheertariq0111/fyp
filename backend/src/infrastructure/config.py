from functools import lru_cache
from ipaddress import ip_address
import re
from typing import Literal

from pydantic import Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


LOCAL_FRONTEND_CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]
CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "OPTIONS"]
CORS_ALLOW_HEADERS = ["Content-Type"]
CORS_EXPOSE_HEADERS = ["X-Request-ID", "X-Agent-Request-ID"]
VOICE_MEDIA_INPUT_PREFIX = "voice-input/"
VOICE_MAX_MEDIA_BYTES = 10_485_760
VOICE_DOWNLOAD_TIMEOUT_SECONDS = 10
VOICE_TRANSCRIPTION_TIMEOUT_SECONDS = 180
HOSTNAME_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
VOICE_TRANSCRIPTION_JOB_PREFIX = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62})-whatsapp-voice-$"
)


class BedrockModelSettings(BaseSettings):
    """Configuration needed to construct a Bedrock model, without app secrets."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    aws_region: str = Field(min_length=1)
    bedrock_model_id: str = ""
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = ""


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


def parse_voice_media_allowed_hosts(raw_value: str | None) -> list[str]:
    raw = (raw_value or "").strip()
    if not raw:
        return []
    entries = raw_value.split(",") if raw_value is not None else []
    if any(not entry.strip() for entry in entries):
        raise ValueError("VOICE_MEDIA_ALLOWED_HOSTS contains a blank hostname")
    hosts: list[str] = []
    for entry in entries:
        host = entry.strip().lower()
        if (
            "://" in host
            or any(character in host for character in "/?#@:*[]")
            or host.endswith(".")
        ):
            raise ValueError(
                "VOICE_MEDIA_ALLOWED_HOSTS must contain exact hostnames only"
            )
        try:
            ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("VOICE_MEDIA_ALLOWED_HOSTS must not contain IP literals")
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("VOICE_MEDIA_ALLOWED_HOSTS must not contain localhost")
        labels = host.split(".")
        if len(host) > 253 or len(labels) < 2 or not all(
            HOSTNAME_LABEL.fullmatch(label) for label in labels
        ):
            raise ValueError(
                "VOICE_MEDIA_ALLOWED_HOSTS contains an invalid hostname"
            )
        if host not in hosts:
            hosts.append(host)
    return hosts


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    aws_region: str = Field(min_length=1)
    bedrock_model_id: str = ""
    bedrock_guardrail_id: str = ""
    bedrock_guardrail_version: str = ""
    knowledge_base_id: str = ""
    knowledge_base_max_results: int = Field(default=5, gt=0, le=100)
    agentcore_runtime_arn: str = ""

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
    conversation_messages_table_name: str = Field(min_length=1)
    menu_sessions_table_name: str = Field(min_length=1)
    audit_table_name: str = Field(min_length=1)
    tickets_table_name: str = Field(min_length=1)
    support_phone_number: str = ""
    agentflo_whatsapp_webhook_secret: str = ""
    agentflo_gateway_base_url: str = ""
    agentflo_gateway_api_key: str = ""
    agentflo_gateway_tenant_id: str = "fyp-dev"
    agentflo_gateway_agent_id: str = "restaurant-agent"
    agentflo_gateway_actor_id: str = ""
    whatsapp_voice_enabled: bool = False
    voice_media_bucket_name: str = ""
    voice_media_input_prefix: str = VOICE_MEDIA_INPUT_PREFIX
    voice_job_queue_url: str = ""
    whatsapp_voice_jobs_table_name: str = ""
    voice_sqs_wait_time_seconds: int = 20
    voice_sqs_visibility_timeout_seconds: int = 300
    voice_sqs_heartbeat_seconds: int = 60
    voice_job_lease_seconds: int = 180
    voice_job_ttl_hours: int = 24
    voice_transcription_job_prefix: str = ""
    voice_max_media_bytes: int = VOICE_MAX_MEDIA_BYTES
    voice_download_timeout_seconds: float = VOICE_DOWNLOAD_TIMEOUT_SECONDS
    voice_transcription_timeout_seconds: int = VOICE_TRANSCRIPTION_TIMEOUT_SECONDS
    voice_media_allowed_hosts: str = ""
    voice_transcription_language_code: str = ""
    voice_transcription_identify_language: bool = False

    menu_site_base_url: HttpUrl
    session_token_secret: str = Field(min_length=16)
    session_token_ttl_minutes: int = Field(default=60, gt=0)
    agent_session_ttl_hours: int = Field(default=24, gt=0)
    agent_request_ttl_hours: int = Field(default=24, gt=0)
    conversation_message_ttl_days: int = Field(default=90, gt=0)
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
        if self.voice_media_input_prefix != VOICE_MEDIA_INPUT_PREFIX:
            raise ValueError("VOICE_MEDIA_INPUT_PREFIX must be exactly voice-input/")
        if not 0 < self.voice_max_media_bytes <= VOICE_MAX_MEDIA_BYTES:
            raise ValueError("VOICE_MAX_MEDIA_BYTES exceeds the deployed limit")
        if not 0 < self.voice_download_timeout_seconds <= VOICE_DOWNLOAD_TIMEOUT_SECONDS:
            raise ValueError("VOICE_DOWNLOAD_TIMEOUT_SECONDS exceeds the deployed limit")
        if not 0 < self.voice_transcription_timeout_seconds <= VOICE_TRANSCRIPTION_TIMEOUT_SECONDS:
            raise ValueError("VOICE_TRANSCRIPTION_TIMEOUT_SECONDS exceeds the deployed limit")
        if not 0 < self.voice_sqs_wait_time_seconds <= 20:
            raise ValueError("VOICE_SQS_WAIT_TIME_SECONDS must be between 1 and 20")
        if self.voice_sqs_visibility_timeout_seconds <= 0:
            raise ValueError("VOICE_SQS_VISIBILITY_TIMEOUT_SECONDS must be positive")
        if self.voice_sqs_heartbeat_seconds <= 0:
            raise ValueError("VOICE_SQS_HEARTBEAT_SECONDS must be positive")
        if self.voice_job_lease_seconds < self.voice_sqs_heartbeat_seconds * 2:
            raise ValueError("VOICE_JOB_LEASE_SECONDS must tolerate a delayed heartbeat")
        if self.voice_sqs_heartbeat_seconds >= min(
            self.voice_job_lease_seconds, self.voice_sqs_visibility_timeout_seconds
        ):
            raise ValueError("VOICE_SQS_HEARTBEAT_SECONDS must be shorter than lease and visibility")
        if not 0 < self.voice_job_ttl_hours <= 168:
            raise ValueError("VOICE_JOB_TTL_HOURS must be between 1 and 168")
        allowed_hosts = self.parsed_voice_media_allowed_hosts()
        language_code_configured = bool(
            self.voice_transcription_language_code.strip()
        )
        if self.whatsapp_voice_enabled:
            if not self.voice_media_bucket_name.strip():
                raise ValueError("VOICE_MEDIA_BUCKET_NAME is required when voice is enabled")
            if not self.voice_job_queue_url.strip():
                raise ValueError("VOICE_JOB_QUEUE_URL is required when voice is enabled")
            if not self.whatsapp_voice_jobs_table_name.strip():
                raise ValueError("WHATSAPP_VOICE_JOBS_TABLE_NAME is required when voice is enabled")
            if not allowed_hosts:
                raise ValueError("VOICE_MEDIA_ALLOWED_HOSTS is required when voice is enabled")
            if language_code_configured == self.voice_transcription_identify_language:
                raise ValueError(
                    "Exactly one voice transcription language mode is required"
                )
        return self

    def validate_voice_worker_settings(self) -> None:
        """Fail closed only in the standalone worker, never during disabled API startup."""
        required = {
            "WHATSAPP_VOICE_JOBS_TABLE_NAME": self.whatsapp_voice_jobs_table_name,
            "VOICE_JOB_QUEUE_URL": self.voice_job_queue_url,
            "VOICE_MEDIA_BUCKET_NAME": self.voice_media_bucket_name,
            "AGENTFLO_GATEWAY_BASE_URL": self.agentflo_gateway_base_url,
            "AGENTFLO_GATEWAY_API_KEY": self.agentflo_gateway_api_key,
            "AGENTCORE_RUNTIME_ARN": self.agentcore_runtime_arn,
            "VOICE_TRANSCRIPTION_JOB_PREFIX": self.voice_transcription_job_prefix,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError("VOICE_WORKER_CONFIGURATION_INCOMPLETE")
        if not self.parsed_voice_media_allowed_hosts():
            raise ValueError("VOICE_MEDIA_ALLOWED_HOSTS is required for the worker")
        language_code = bool(self.voice_transcription_language_code.strip())
        if language_code == self.voice_transcription_identify_language:
            raise ValueError("Exactly one voice transcription language mode is required")
        if not VOICE_TRANSCRIPTION_JOB_PREFIX.fullmatch(self.voice_transcription_job_prefix):
            raise ValueError("VOICE_TRANSCRIPTION_JOB_PREFIX does not match the deployed IAM scope")

    def parsed_frontend_cors_origins(self) -> list[str]:
        return parse_frontend_cors_origins(self.frontend_cors_origins, self.environment)

    def parsed_voice_media_allowed_hosts(self) -> list[str]:
        return parse_voice_media_allowed_hosts(self.voice_media_allowed_hosts)

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


@lru_cache
def get_bedrock_model_settings() -> BedrockModelSettings:
    return BedrockModelSettings()
