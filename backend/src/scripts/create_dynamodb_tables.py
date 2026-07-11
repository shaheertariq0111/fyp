from __future__ import annotations

from botocore.exceptions import ClientError

from src.infrastructure.config import get_settings
from src.infrastructure.dynamodb import get_dynamodb_resource


def table_definitions(settings):
    standard = {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
    }
    definitions = [
        {"TableName": name, **standard}
        for name in (
            settings.menu_table_name,
            settings.menu_sessions_table_name,
            settings.carts_table_name,
            settings.agent_sessions_table_name,
            settings.audit_table_name,
        )
    ]
    definitions.append(
        {
            "TableName": settings.customers_table_name,
            "KeySchema": standard["KeySchema"],
            "AttributeDefinitions": standard["AttributeDefinitions"]
            + [
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            "BillingMode": "PAY_PER_REQUEST",
        }
    )
    definitions.append(
        {
            "TableName": settings.orders_table_name,
            "KeySchema": standard["KeySchema"],
            "AttributeDefinitions": standard["AttributeDefinitions"]
            + [
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            "BillingMode": "PAY_PER_REQUEST",
        }
    )
    return definitions


def create_tables() -> list[str]:
    settings = get_settings()
    if not settings.allow_aws_resource_creation:
        raise RuntimeError(
            "Set ALLOW_AWS_RESOURCE_CREATION=true to authorize AWS table creation"
        )
    dynamodb = get_dynamodb_resource(settings)
    client = dynamodb.meta.client
    tags = settings.parsed_dynamodb_tags()
    created = []
    ttl_table_name = settings.agent_sessions_table_name
    for definition in table_definitions(settings):
        name = definition["TableName"]
        try:
            client.describe_table(TableName=name)
        except client.exceptions.ResourceNotFoundException:
            pass
        else:
            continue
        request = dict(definition)
        request["DeletionProtectionEnabled"] = settings.dynamodb_deletion_protection
        if tags:
            request["Tags"] = tags
        table = dynamodb.create_table(**request)
        table.wait_until_exists()
        if settings.dynamodb_point_in_time_recovery:
            client.update_continuous_backups(
                TableName=name,
                PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
            )
        created.append(name)
    _ensure_agent_session_ttl(client, ttl_table_name)
    return created


def _ensure_agent_session_ttl(client, table_name: str) -> None:
    try:
        ttl = client.describe_time_to_live(TableName=table_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return
        raise
    status = ttl.get("TimeToLiveDescription", {}).get("TimeToLiveStatus")
    attribute = ttl.get("TimeToLiveDescription", {}).get("AttributeName")
    if status in {"ENABLED", "ENABLING"} and attribute == "expires_at":
        return
    client.update_time_to_live(
        TableName=table_name,
        TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
    )


if __name__ == "__main__":
    for table_name in create_tables():
        print(f"Created {table_name}")
