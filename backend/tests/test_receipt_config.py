from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.infrastructure.receipt_config import (
    ReceiptProcessingSettings,
    ReceiptRecoverySettings,
)


RECOVERY_ENV = {
    "AWS_REGION": "us-west-2",
    "RECEIPT_JOBS_TABLE_NAME": "receipt-jobs-test",
    "RECEIPT_JOB_QUEUE_URL": "https://sqs.example.test/receipt",
}
PROCESSING_ENV = {
    **RECOVERY_ENV,
    "ORDERS_TABLE_NAME": "orders-test",
    "RECEIPT_BUCKET_NAME": "receipt-bucket-test",
    "RECEIPT_MERCHANT_NAME": "Test Merchant",
    "RECEIPT_WORKER_LEASE_SECONDS": "120",
    "AGENTFLO_GATEWAY_BASE_URL": "https://gateway.example.test",
    "AGENTFLO_GATEWAY_TENANT_ID": "tenant-test",
    "AGENTFLO_GATEWAY_AGENT_ID": "agent-test",
    "AGENTFLO_GATEWAY_ACTOR_ID": "actor-test",
    "AGENTFLO_GATEWAY_API_KEY_SECRET_ARN": (
        "arn:aws:secretsmanager:us-west-2:123456789012:secret:receipt"
    ),
}


def load_from_environment(settings_type, monkeypatch, values):
    unrelated = {
        "SESSION_TOKEN_SECRET",
        "MENU_SITE_BASE_URL",
        "MENU_TABLE_NAME",
        "CARTS_TABLE_NAME",
        "CUSTOMERS_TABLE_NAME",
        "AGENT_SESSIONS_TABLE_NAME",
        "AGENT_REQUESTS_TABLE_NAME",
        "CONVERSATION_MESSAGES_TABLE_NAME",
        "MENU_SESSIONS_TABLE_NAME",
        "AUDIT_TABLE_NAME",
        "TICKETS_TABLE_NAME",
        "RESTAURANT_ID",
        "BRANCH_ID",
        "BEDROCK_MODEL_ID",
        "AGENTCORE_RUNTIME_ARN",
        "VOICE_MEDIA_BUCKET_NAME",
    }
    for name in unrelated:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return settings_type(_env_file=None)


def test_recovery_settings_load_with_only_recovery_dependencies(monkeypatch):
    settings = load_from_environment(
        ReceiptRecoverySettings,
        monkeypatch,
        RECOVERY_ENV,
    )

    assert settings.aws_region == "us-west-2"
    assert settings.receipt_jobs_table_name == "receipt-jobs-test"
    assert settings.receipt_job_queue_url.endswith("/receipt")
    assert settings.receipt_job_ttl_hours == 336
    assert settings.receipt_enqueue_retry_seconds == 60
    assert not hasattr(settings, "orders_table_name")
    assert not hasattr(settings, "receipt_bucket_name")
    assert not hasattr(settings, "session_token_secret")
    assert not hasattr(settings, "agentflo_gateway_base_url")
    assert not hasattr(settings, "voice_media_bucket_name")


def test_processing_settings_load_with_only_processing_dependencies(monkeypatch):
    settings = load_from_environment(
        ReceiptProcessingSettings,
        monkeypatch,
        PROCESSING_ENV,
    )

    assert settings.orders_table_name == "orders-test"
    assert settings.receipt_bucket_name == "receipt-bucket-test"
    assert settings.receipt_merchant_name == "Test Merchant"
    assert settings.receipt_worker_lease_seconds == 120
    assert settings.agentflo_gateway_api_key_secret_arn.endswith(":receipt")
    assert not hasattr(settings, "session_token_secret")
    assert not hasattr(settings, "menu_site_base_url")
    assert not hasattr(settings, "restaurant_id")
    assert not hasattr(settings, "agentcore_runtime_arn")


@pytest.mark.parametrize(
    "missing_field",
    [
        "orders_table_name",
        "receipt_bucket_name",
        "receipt_merchant_name",
        "agentflo_gateway_base_url",
        "agentflo_gateway_tenant_id",
        "agentflo_gateway_agent_id",
        "agentflo_gateway_actor_id",
        "agentflo_gateway_api_key_secret_arn",
    ],
)
def test_processing_settings_fail_closed_when_required_value_is_missing(
    missing_field,
):
    values = {name.lower(): value for name, value in PROCESSING_ENV.items()}
    values.pop(missing_field)

    with pytest.raises(ValidationError):
        ReceiptProcessingSettings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("settings_type", "field", "value"),
    [
        (ReceiptRecoverySettings, "receipt_job_ttl_hours", 0),
        (ReceiptRecoverySettings, "receipt_job_ttl_hours", 337),
        (ReceiptRecoverySettings, "receipt_enqueue_retry_seconds", 0),
        (ReceiptRecoverySettings, "receipt_enqueue_retry_seconds", 3601),
        (ReceiptProcessingSettings, "receipt_worker_lease_seconds", 60),
        (ReceiptProcessingSettings, "receipt_worker_lease_seconds", 3601),
    ],
)
def test_narrow_receipt_settings_reject_unsafe_numeric_values(
    settings_type,
    field,
    value,
):
    base = {name.lower(): configured for name, configured in PROCESSING_ENV.items()}
    base[field] = value

    with pytest.raises(ValidationError):
        settings_type(_env_file=None, **base)
