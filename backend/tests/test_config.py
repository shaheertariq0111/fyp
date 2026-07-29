from pathlib import Path

from src.infrastructure.config import Settings


BASE = {
    "environment": "test",
    "aws_region": "us-west-2",
    "menu_table_name": "menu-test",
    "carts_table_name": "carts-test",
    "orders_table_name": "orders-test",
    "customers_table_name": "customers-test",
    "agent_sessions_table_name": "agent-sessions-test",
    "agent_requests_table_name": "agent-requests-test",
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
