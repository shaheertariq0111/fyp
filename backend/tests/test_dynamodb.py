from unittest.mock import Mock, patch

from src.infrastructure.dynamodb import get_dynamodb_resource, verify_dynamodb_tables
from test_config import make_test_settings


def test_aws_resource_uses_region_and_default_credential_chain():
    settings = make_test_settings()
    with patch("src.infrastructure.dynamodb.boto3.resource") as resource:
        get_dynamodb_resource(settings)
    kwargs = resource.call_args.kwargs
    assert kwargs["region_name"] == settings.aws_region
    assert "endpoint_url" not in kwargs
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs


def test_verify_tables_loads_every_configured_table():
    settings = make_test_settings()
    tables = {}

    def make_table(name):
        table = Mock(table_status="ACTIVE")
        tables[name] = table
        return table

    dynamodb = Mock()
    dynamodb.Table.side_effect = make_table
    statuses = verify_dynamodb_tables(settings, dynamodb)
    assert len(statuses) == 8
    assert set(statuses.values()) == {"ACTIVE"}
    assert all(table.load.called for table in tables.values())
