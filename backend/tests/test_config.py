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
