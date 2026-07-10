from boto3.dynamodb.conditions import Attr, Key

from .base import from_dynamodb, to_dynamodb


class OrderRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def create(self, order: dict) -> None:
        self.table.put_item(Item=to_dynamodb(order), ConditionExpression="attribute_not_exists(PK)")

    def get(self, user_id: str, order_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": user_id, "SK": f"ORDER#{order_id}"}, ConsistentRead=True
        )
        return from_dynamodb(response.get("Item"))

    def get_by_order_id(self, order_id: str) -> dict | None:
        response = self.table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(f"ORDER#{order_id}"),
            Limit=1,
        )
        items = response.get("Items", [])
        return from_dynamodb(items[0]) if items else None

    def list_active(self, user_id: str, terminal_statuses: set[str]) -> list[dict]:
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(user_id) & Key("SK").begins_with("ORDER#"),
            FilterExpression=~Attr("status").is_in(list(terminal_statuses)),
        )
        return from_dynamodb(response.get("Items", []))

    def save(self, order: dict, expected_version: int) -> None:
        updated = dict(order)
        updated["version"] = expected_version + 1
        self.table.put_item(
            Item=to_dynamodb(updated),
            ConditionExpression="#version = :expected",
            ExpressionAttributeNames={"#version": "version"},
            ExpressionAttributeValues={":expected": expected_version},
        )
