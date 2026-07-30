import pytest

from src.scripts import create_dynamodb_tables
from src.scripts.create_dynamodb_tables import table_definitions

from test_config import make_test_settings


def test_creates_customer_session_tables_and_lookup_indexes():
    settings = make_test_settings()
    definitions = table_definitions(settings)
    assert len(definitions) == 10
    order = next(d for d in definitions if d["TableName"] == settings.orders_table_name)
    assert order["GlobalSecondaryIndexes"][0]["IndexName"] == "GSI1"
    customer = next(d for d in definitions if d["TableName"] == settings.customers_table_name)
    assert customer["GlobalSecondaryIndexes"][0]["IndexName"] == "GSI1"
    assert any(d["TableName"] == settings.agent_sessions_table_name for d in definitions)
    assert any(d["TableName"] == settings.agent_requests_table_name for d in definitions)
    assert any(
        d["TableName"] == settings.conversation_messages_table_name
        for d in definitions
    )


def test_conversation_messages_table_has_channel_listing_index():
    settings = make_test_settings()
    conversation = next(
        definition
        for definition in table_definitions(settings)
        if definition["TableName"] == settings.conversation_messages_table_name
    )

    assert conversation["KeySchema"] == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert conversation["AttributeDefinitions"] == [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
        {"AttributeName": "GSI1PK", "AttributeType": "S"},
        {"AttributeName": "GSI1SK", "AttributeType": "S"},
    ]
    assert conversation["GlobalSecondaryIndexes"] == [
        {
            "IndexName": "GSI1",
            "KeySchema": [
                {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ]


def test_ticket_table_has_customer_and_status_indexes():
    settings = make_test_settings()
    ticket = next(
        definition
        for definition in table_definitions(settings)
        if definition["TableName"] == settings.tickets_table_name
    )

    assert ticket["KeySchema"] == [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ]
    assert ticket["AttributeDefinitions"] == [
        {"AttributeName": "PK", "AttributeType": "S"},
        {"AttributeName": "SK", "AttributeType": "S"},
        {"AttributeName": "GSI1PK", "AttributeType": "S"},
        {"AttributeName": "GSI1SK", "AttributeType": "S"},
        {"AttributeName": "GSI2PK", "AttributeType": "S"},
        {"AttributeName": "GSI2SK", "AttributeType": "S"},
    ]
    assert [index["IndexName"] for index in ticket["GlobalSecondaryIndexes"]] == [
        "GSI1",
        "GSI2",
    ]
    assert ticket["GlobalSecondaryIndexes"][0]["KeySchema"] == [
        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
    ]
    assert ticket["GlobalSecondaryIndexes"][1]["KeySchema"] == [
        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
    ]


def test_aws_creation_requires_explicit_authorization(monkeypatch):
    settings = make_test_settings(allow_aws_resource_creation=False)
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


def test_ticket_ttl_uses_expires_at(monkeypatch):
    settings = make_test_settings(allow_aws_resource_creation=True)
    monkeypatch.setattr(create_dynamodb_tables, "get_settings", lambda: settings)

    class FakeClient:
        class exceptions:
            class ResourceNotFoundException(Exception):
                pass

        def __init__(self):
            self.ttl_updates = []

        def describe_table(self, TableName):
            return {"Table": {"TableName": TableName}}

        def describe_time_to_live(self, TableName):
            return {"TimeToLiveDescription": {"TimeToLiveStatus": "DISABLED"}}

        def update_time_to_live(self, **kwargs):
            self.ttl_updates.append(kwargs)

    class FakeDynamoResource:
        def __init__(self):
            self.meta = type("Meta", (), {"client": FakeClient()})()

    resource = FakeDynamoResource()
    monkeypatch.setattr(
        create_dynamodb_tables,
        "get_dynamodb_resource",
        lambda _settings: resource,
    )

    assert create_dynamodb_tables.create_tables() == []
    assert {
        "TableName": settings.tickets_table_name,
        "TimeToLiveSpecification": {
            "Enabled": True,
            "AttributeName": "expires_at",
        },
    } in resource.meta.client.ttl_updates
    assert {
        "TableName": settings.conversation_messages_table_name,
        "TimeToLiveSpecification": {
            "Enabled": True,
            "AttributeName": "expires_at",
        },
    } in resource.meta.client.ttl_updates
