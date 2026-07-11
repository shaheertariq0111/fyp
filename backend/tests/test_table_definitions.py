from src.infrastructure.config import Settings
import pytest

from src.scripts import create_dynamodb_tables
from src.scripts.create_dynamodb_tables import table_definitions

from test_config import BASE


def test_creates_customer_session_tables_and_lookup_indexes():
    settings = Settings(**BASE)
    definitions = table_definitions(settings)
    assert len(definitions) == 7
    order = next(d for d in definitions if d["TableName"] == settings.orders_table_name)
    assert order["GlobalSecondaryIndexes"][0]["IndexName"] == "GSI1"
    customer = next(d for d in definitions if d["TableName"] == settings.customers_table_name)
    assert customer["GlobalSecondaryIndexes"][0]["IndexName"] == "GSI1"
    assert any(d["TableName"] == settings.agent_sessions_table_name for d in definitions)


def test_aws_creation_requires_explicit_authorization(monkeypatch):
    settings = Settings(**BASE, allow_aws_resource_creation=False)
    monkeypatch.setattr(create_dynamodb_tables, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="ALLOW_AWS_RESOURCE_CREATION"):
        create_dynamodb_tables.create_tables()


def test_agent_session_ttl_is_enabled_when_missing():
    class FakeClient:
        def __init__(self):
            self.updated = None

        def describe_time_to_live(self, TableName):
            assert TableName == "sessions"
            return {"TimeToLiveDescription": {"TimeToLiveStatus": "DISABLED"}}

        def update_time_to_live(self, **kwargs):
            self.updated = kwargs

    client = FakeClient()

    create_dynamodb_tables._ensure_agent_session_ttl(client, "sessions")

    assert client.updated == {
        "TableName": "sessions",
        "TimeToLiveSpecification": {"Enabled": True, "AttributeName": "expires_at"},
    }
