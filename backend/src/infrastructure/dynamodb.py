import boto3
from botocore.config import Config

from .config import Settings


def get_dynamodb_resource(settings: Settings):
    return boto3.resource(
        "dynamodb",
        region_name=settings.aws_region,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


def verify_dynamodb_tables(settings: Settings, dynamodb=None) -> dict[str, str]:
    resource = dynamodb or get_dynamodb_resource(settings)
    statuses = {}
    for table_name in (
        settings.menu_table_name,
        settings.menu_sessions_table_name,
        settings.carts_table_name,
        settings.orders_table_name,
        settings.audit_table_name,
    ):
        table = resource.Table(table_name)
        table.load()
        statuses[table_name] = table.table_status
    return statuses
