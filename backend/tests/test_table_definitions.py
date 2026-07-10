from src.infrastructure.config import Settings
import pytest

from src.scripts import create_dynamodb_tables
from src.scripts.create_dynamodb_tables import table_definitions

from test_config import BASE


def test_creates_five_mvp_tables_and_order_lookup_index():
    settings = Settings(**BASE)
    definitions = table_definitions(settings)
    assert len(definitions) == 5
    order = next(d for d in definitions if d["TableName"] == settings.orders_table_name)
    assert order["GlobalSecondaryIndexes"][0]["IndexName"] == "GSI1"


def test_aws_creation_requires_explicit_authorization(monkeypatch):
    settings = Settings(**BASE, allow_aws_resource_creation=False)
    monkeypatch.setattr(create_dynamodb_tables, "get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="ALLOW_AWS_RESOURCE_CREATION"):
        create_dynamodb_tables.create_tables()
