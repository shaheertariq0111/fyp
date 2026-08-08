from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ReceiptRecoverySettings(BaseSettings):
    """Only the configuration needed to recover the receipt enqueue outbox."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    aws_region: str = Field(min_length=1)
    receipt_jobs_table_name: str = Field(min_length=1)
    receipt_job_queue_url: str = Field(min_length=1)
    receipt_job_ttl_hours: int = Field(default=336, ge=1, le=336)
    receipt_enqueue_retry_seconds: int = Field(default=60, ge=1, le=3600)

    @field_validator(
        "aws_region",
        "receipt_jobs_table_name",
        "receipt_job_queue_url",
    )
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RECEIPT_RECOVERY_CONFIGURATION_INCOMPLETE")
        return value.strip()


class ReceiptProcessingSettings(ReceiptRecoverySettings):
    """The complete and isolated receipt-processing Lambda configuration."""

    orders_table_name: str = Field(min_length=1)
    receipt_bucket_name: str = Field(min_length=1)
    receipt_merchant_name: str = Field(min_length=1)
    receipt_worker_lease_seconds: int = Field(default=120, gt=60, le=3600)
    agentflo_gateway_base_url: str = Field(min_length=1)
    agentflo_gateway_tenant_id: str = Field(min_length=1)
    agentflo_gateway_agent_id: str = Field(min_length=1)
    agentflo_gateway_actor_id: str = Field(min_length=1)
    agentflo_gateway_api_key_secret_arn: str = Field(min_length=1)

    @field_validator(
        "orders_table_name",
        "receipt_bucket_name",
        "receipt_merchant_name",
        "agentflo_gateway_base_url",
        "agentflo_gateway_tenant_id",
        "agentflo_gateway_agent_id",
        "agentflo_gateway_actor_id",
        "agentflo_gateway_api_key_secret_arn",
    )
    @classmethod
    def reject_blank_processing_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("RECEIPT_PROCESSING_CONFIGURATION_INCOMPLETE")
        return value.strip()


@lru_cache
def get_receipt_recovery_settings() -> ReceiptRecoverySettings:
    return ReceiptRecoverySettings()


@lru_cache
def get_receipt_processing_settings() -> ReceiptProcessingSettings:
    return ReceiptProcessingSettings()
