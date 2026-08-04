from pathlib import Path

import pytest
from pydantic import ValidationError

from src.infrastructure.config import (
    BedrockModelSettings,
    Settings,
    parse_voice_media_allowed_hosts,
)


BASE = {
    "environment": "test",
    "aws_region": "us-west-2",
    "menu_table_name": "menu-test",
    "carts_table_name": "carts-test",
    "orders_table_name": "orders-test",
    "customers_table_name": "customers-test",
    "agent_sessions_table_name": "agent-sessions-test",
    "agent_requests_table_name": "agent-requests-test",
    "conversation_messages_table_name": "conversation-messages-test",
    "menu_sessions_table_name": "sessions-test",
    "audit_table_name": "audit-test",
    "tickets_table_name": "tickets-test",
    "support_phone_number": "+1 555 0100",
    "menu_site_base_url": "http://localhost:3000/menu",
    "session_token_secret": "test-secret-at-least-sixteen",
    "restaurant_id": "restaurant-test",
    "branch_id": "branch-test",
}


def make_test_settings(**overrides):
    return Settings(_env_file=None, **{**BASE, **overrides})


def test_test_environment_allows_empty_bedrock_model():
    settings = make_test_settings()
    assert settings.environment == "test"


def test_bedrock_model_settings_do_not_require_backend_session_secret():
    settings = BedrockModelSettings(
        _env_file=None,
        aws_region="us-east-1",
        bedrock_model_id="configured-model",
    )

    assert settings.bedrock_model_id == "configured-model"
    assert not hasattr(settings, "session_token_secret")


def test_backend_settings_still_require_session_token_secret():
    values = dict(BASE)
    values.pop("session_token_secret")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, **values)

    assert "session_token_secret" in str(error.value)


def test_dynamodb_tags_are_parsed():
    settings = make_test_settings(dynamodb_resource_tags="Environment=test,Application=agent")
    assert settings.parsed_dynamodb_tags() == [
        {"Key": "Environment", "Value": "test"},
        {"Key": "Application", "Value": "agent"},
    ]


def test_admin_auth_settings_have_safe_defaults():
    settings = make_test_settings()
    assert settings.admin_username == ""
    assert settings.admin_password == ""
    assert settings.admin_session_secret == ""
    assert settings.admin_session_ttl_hours == 8


def test_ticket_settings_are_loaded():
    settings = make_test_settings()
    assert settings.tickets_table_name == "tickets-test"
    assert settings.support_phone_number == "+1 555 0100"


def test_conversation_history_settings_are_loaded():
    settings = make_test_settings()
    assert settings.conversation_messages_table_name == "conversation-messages-test"
    assert settings.conversation_message_ttl_days == 90


def test_agentflo_webhook_secret_is_optional_and_configurable(monkeypatch):
    assert make_test_settings().agentflo_whatsapp_webhook_secret == ""
    assert make_test_settings(
        agentflo_whatsapp_webhook_secret="synthetic-shared-secret",
    ).agentflo_whatsapp_webhook_secret == "synthetic-shared-secret"
    monkeypatch.setenv(
        "AGENTFLO_WHATSAPP_WEBHOOK_SECRET",
        "synthetic-environment-secret",
    )
    assert Settings(
        _env_file=None,
        **BASE,
    ).agentflo_whatsapp_webhook_secret == "synthetic-environment-secret"


def test_agentflo_gateway_settings_are_optional_and_configurable(monkeypatch):
    settings = make_test_settings()
    assert settings.agentflo_gateway_base_url == ""
    assert settings.agentflo_gateway_api_key == ""
    assert settings.agentflo_gateway_tenant_id == "fyp-dev"
    assert settings.agentflo_gateway_agent_id == "restaurant-agent"
    assert settings.agentflo_gateway_actor_id == ""

    monkeypatch.setenv(
        "AGENTFLO_GATEWAY_BASE_URL",
        "https://communicationgateway.agentflo.com",
    )
    monkeypatch.setenv("AGENTFLO_GATEWAY_API_KEY", "synthetic-api-key")
    monkeypatch.setenv("AGENTFLO_GATEWAY_TENANT_ID", "tenant-synthetic")
    monkeypatch.setenv("AGENTFLO_GATEWAY_AGENT_ID", "agent-synthetic")
    monkeypatch.setenv("AGENTFLO_GATEWAY_ACTOR_ID", "actor-synthetic")
    configured = Settings(_env_file=None, **BASE)

    assert configured.agentflo_gateway_base_url == (
        "https://communicationgateway.agentflo.com"
    )
    assert configured.agentflo_gateway_api_key == "synthetic-api-key"
    assert configured.agentflo_gateway_tenant_id == "tenant-synthetic"
    assert configured.agentflo_gateway_agent_id == "agent-synthetic"
    assert configured.agentflo_gateway_actor_id == "actor-synthetic"


def test_support_phone_has_no_invented_default():
    values = dict(BASE)
    values.pop("support_phone_number")
    settings = Settings(_env_file=None, **values)
    assert settings.support_phone_number == ""


def test_backend_env_example_documents_ticket_configuration():
    example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text(encoding="utf-8")
    assert "TICKETS_TABLE_NAME=" in example
    assert "SUPPORT_PHONE_NUMBER=" in example


def test_backend_env_example_documents_conversation_history_configuration():
    example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text(encoding="utf-8")
    assert "CONVERSATION_MESSAGES_TABLE_NAME=" in example
    assert "CONVERSATION_MESSAGE_TTL_DAYS=90" in example


def test_backend_env_example_documents_agentflo_webhook_secret():
    example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text(encoding="utf-8")
    assert "AGENTFLO_WHATSAPP_WEBHOOK_SECRET=" in example


def test_backend_env_example_documents_agentflo_gateway_configuration():
    example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text(encoding="utf-8")
    assert (
        "AGENTFLO_GATEWAY_BASE_URL="
        "https://communicationgateway.agentflo.com"
    ) in example
    assert "AGENTFLO_GATEWAY_API_KEY=" in example
    assert "AGENTFLO_GATEWAY_TENANT_ID=fyp-dev" in example
    assert "AGENTFLO_GATEWAY_AGENT_ID=restaurant-agent" in example
    assert "AGENTFLO_GATEWAY_ACTOR_ID=restaurant-agent" in example


def test_frontend_cors_origins_default_to_local_in_tests():
    settings = make_test_settings()
    assert settings.parsed_frontend_cors_origins() == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def test_frontend_cors_origins_parse_exact_deployed_origins():
    settings = make_test_settings(
        environment="production",
        bedrock_model_id="us.amazon.nova-pro-v1:0",
        frontend_cors_origins="https://main.example.amplifyapp.com, https://preview.example.amplifyapp.com/",
    )

    assert settings.parsed_frontend_cors_origins() == [
        "https://main.example.amplifyapp.com",
        "https://preview.example.amplifyapp.com",
    ]


def test_frontend_cors_origins_reject_wildcard():
    try:
        make_test_settings(frontend_cors_origins="*")
    except ValueError as exc:
        assert "FRONTEND_CORS_ORIGINS must not use wildcard" in str(exc)
    else:
        raise AssertionError("Wildcard CORS origin should fail validation")


def test_frontend_cors_origins_reject_localhost_outside_local_test():
    try:
        make_test_settings(
            environment="production",
            bedrock_model_id="us.amazon.nova-pro-v1:0",
            frontend_cors_origins="http://localhost:3000",
        )
    except ValueError as exc:
        assert "exact deployed frontend origins" in str(exc)
    else:
        raise AssertionError("Production localhost CORS origin should fail validation")


def test_admin_cookie_is_cross_site_in_staging_and_production():
    assert make_test_settings(
        environment="staging",
        bedrock_model_id="us.amazon.nova-pro-v1:0",
        frontend_cors_origins="https://app.amplifyapp.com",
    ).cross_site_admin_cookie()
    assert make_test_settings(
        environment="production",
        bedrock_model_id="us.amazon.nova-pro-v1:0",
        frontend_cors_origins="https://app.amplifyapp.com",
    ).cross_site_admin_cookie()
    assert not make_test_settings().cross_site_admin_cookie()


def test_voice_configuration_has_disabled_safe_defaults():
    settings = make_test_settings()

    assert settings.whatsapp_voice_enabled is False
    assert settings.voice_media_bucket_name == ""
    assert settings.voice_media_input_prefix == "voice-input/"
    assert settings.voice_job_queue_url == ""
    assert settings.whatsapp_voice_jobs_table_name == ""
    assert settings.voice_sqs_wait_time_seconds == 20
    assert settings.voice_sqs_visibility_timeout_seconds == 300
    assert settings.voice_sqs_heartbeat_seconds == 60
    assert settings.voice_job_lease_seconds == 180
    assert settings.voice_job_ttl_hours == 24
    assert settings.voice_transcription_job_prefix == ""
    assert settings.voice_max_media_bytes == 10_485_760
    assert settings.voice_download_timeout_seconds == 10
    assert settings.voice_transcription_timeout_seconds == 180
    assert settings.parsed_voice_media_allowed_hosts() == []
    assert settings.voice_transcription_language_code == ""
    assert settings.voice_transcription_identify_language is False


def test_voice_environment_variables_are_parsed(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VOICE_ENABLED", "true")
    monkeypatch.setenv("VOICE_MEDIA_BUCKET_NAME", "voice-bucket")
    monkeypatch.setenv("VOICE_JOB_QUEUE_URL", "https://sqs.example.test/queue")
    monkeypatch.setenv("WHATSAPP_VOICE_JOBS_TABLE_NAME", "voice-jobs-test")
    monkeypatch.setenv(
        "VOICE_MEDIA_ALLOWED_HOSTS",
        "Media.Example.Test, media.example.test,cdn.example.test",
    )
    monkeypatch.setenv("VOICE_TRANSCRIPTION_IDENTIFY_LANGUAGE", "true")

    settings = Settings(_env_file=None, **BASE)

    assert settings.whatsapp_voice_enabled is True
    assert settings.parsed_voice_media_allowed_hosts() == [
        "media.example.test",
        "cdn.example.test",
    ]
    assert settings.voice_transcription_identify_language is True


@pytest.mark.parametrize(
    "hosts",
    [
        "*.example.test",
        "https://media.example.test",
        "media.example.test:443",
        "media.example.test/path",
        "media.example.test?query=true",
        "media.example.test#fragment",
        "localhost",
        "127.0.0.1",
        "media.example.test,",
    ],
)
def test_voice_allowed_hosts_reject_unsafe_or_non_exact_values(hosts):
    with pytest.raises(ValueError):
        parse_voice_media_allowed_hosts(hosts)


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"voice_media_bucket_name": "voice-bucket"},
        {
            "voice_media_bucket_name": "voice-bucket",
            "voice_job_queue_url": "https://sqs.example.test/queue",
        },
        {
            "voice_media_bucket_name": "voice-bucket",
            "voice_job_queue_url": "https://sqs.example.test/queue",
            "voice_media_allowed_hosts": "media.example.test",
        },
    ],
)
def test_enabled_voice_requires_bucket_queue_hosts_and_language(overrides):
    with pytest.raises(ValidationError):
        make_test_settings(whatsapp_voice_enabled=True, **overrides)


def test_enabled_voice_accepts_exactly_one_language_mode():
    common = {
        "whatsapp_voice_enabled": True,
        "voice_media_bucket_name": "voice-bucket",
        "voice_job_queue_url": "https://sqs.example.test/queue",
        "whatsapp_voice_jobs_table_name": "voice-jobs-test",
        "voice_media_allowed_hosts": "media.example.test",
    }

    assert make_test_settings(
        **common,
        voice_transcription_language_code="ur-PK",
    ).voice_transcription_language_code == "ur-PK"
    assert make_test_settings(
        **common,
        voice_transcription_identify_language=True,
    ).voice_transcription_identify_language is True
    with pytest.raises(ValidationError):
        make_test_settings(
            **common,
            voice_transcription_language_code="en-US",
            voice_transcription_identify_language=True,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"voice_media_input_prefix": "other/"},
        {"voice_max_media_bytes": 0},
        {"voice_max_media_bytes": 10_485_761},
        {"voice_download_timeout_seconds": 0},
        {"voice_download_timeout_seconds": 10.1},
        {"voice_transcription_timeout_seconds": 0},
        {"voice_transcription_timeout_seconds": 181},
    ],
)
def test_voice_configuration_enforces_deployed_bounds(overrides):
    with pytest.raises(ValidationError):
        make_test_settings(**overrides)


def test_worker_configuration_fails_closed_and_accepts_iam_scoped_prefix():
    with pytest.raises(ValueError, match="VOICE_WORKER_CONFIGURATION_INCOMPLETE"):
        make_test_settings().validate_voice_worker_settings()

    settings = make_test_settings(
        agentcore_runtime_arn="arn:aws:bedrock-agentcore:region:account:runtime/test",
        agentflo_gateway_base_url="https://gateway.example.test",
        agentflo_gateway_api_key="synthetic-key",
        voice_media_bucket_name="voice-bucket",
        voice_job_queue_url="https://sqs.example.test/queue",
        whatsapp_voice_jobs_table_name="voice-jobs-test",
        voice_media_allowed_hosts="media.example.test",
        voice_transcription_language_code="en-US",
        voice_transcription_job_prefix="fyp-dev-whatsapp-voice-",
    )
    settings.validate_voice_worker_settings()


@pytest.mark.parametrize("overrides", [
    {"voice_sqs_wait_time_seconds": 21},
    {"voice_sqs_wait_time_seconds": 0},
    {"voice_sqs_heartbeat_seconds": 180},
    {"voice_job_lease_seconds": 119},
    {"voice_job_ttl_hours": 0},
])
def test_voice_worker_timing_bounds(overrides):
    with pytest.raises(ValidationError):
        make_test_settings(**overrides)


def test_backend_env_example_documents_voice_configuration():
    example = (
        Path(__file__).resolve().parents[1] / ".env.example"
    ).read_text(encoding="utf-8")
    expected = {
        "WHATSAPP_VOICE_ENABLED=false",
        "VOICE_MEDIA_BUCKET_NAME=",
        "VOICE_MEDIA_INPUT_PREFIX=voice-input/",
        "VOICE_JOB_QUEUE_URL=",
        "WHATSAPP_VOICE_JOBS_TABLE_NAME=",
        "VOICE_SQS_WAIT_TIME_SECONDS=20",
        "VOICE_SQS_VISIBILITY_TIMEOUT_SECONDS=300",
        "VOICE_SQS_HEARTBEAT_SECONDS=60",
        "VOICE_JOB_LEASE_SECONDS=180",
        "VOICE_JOB_TTL_HOURS=24",
        "VOICE_TRANSCRIPTION_JOB_PREFIX=",
        "VOICE_MAX_MEDIA_BYTES=10485760",
        "VOICE_DOWNLOAD_TIMEOUT_SECONDS=10",
        "VOICE_TRANSCRIPTION_TIMEOUT_SECONDS=180",
        "VOICE_MEDIA_ALLOWED_HOSTS=",
        "VOICE_TRANSCRIPTION_LANGUAGE_CODE=",
        "VOICE_TRANSCRIPTION_IDENTIFY_LANGUAGE=false",
    }

    assert expected.issubset(set(example.splitlines()))
