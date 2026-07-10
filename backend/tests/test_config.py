from src.infrastructure.config import Settings


BASE = {
    "environment": "test",
    "aws_region": "us-west-2",
    "menu_table_name": "menu-test",
    "carts_table_name": "carts-test",
    "orders_table_name": "orders-test",
    "menu_sessions_table_name": "sessions-test",
    "audit_table_name": "audit-test",
    "menu_site_base_url": "http://localhost:3000/menu",
    "session_token_secret": "test-secret-at-least-sixteen",
    "restaurant_id": "restaurant-test",
    "branch_id": "branch-test",
}


def test_test_environment_allows_empty_bedrock_model():
    settings = Settings(**BASE)
    assert settings.environment == "test"


def test_dynamodb_tags_are_parsed():
    settings = Settings(**BASE, dynamodb_resource_tags="Environment=test,Application=agent")
    assert settings.parsed_dynamodb_tags() == [
        {"Key": "Environment", "Value": "test"},
        {"Key": "Application", "Value": "agent"},
    ]
