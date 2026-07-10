from boto3.dynamodb.conditions import Attr

from .base import from_dynamodb, to_dynamodb


class CartRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def create(self, cart: dict) -> None:
        self.table.put_item(Item=to_dynamodb(cart), ConditionExpression="attribute_not_exists(PK)")

    def get(self, user_id: str, cart_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": user_id, "SK": f"CART#{cart_id}"}, ConsistentRead=True
        )
        return from_dynamodb(response.get("Item"))

    def find_by_cart_id(self, cart_id: str) -> dict | None:
        response = self.table.scan(FilterExpression=Attr("cart_id").eq(cart_id), Limit=1)
        items = response.get("Items", [])
        return from_dynamodb(items[0]) if items else None

    def find_by_cart_item_id(self, cart_item_id: str) -> dict | None:
        response = self.table.scan(FilterExpression=Attr("cart_item_ids").contains(cart_item_id), Limit=1)
        items = response.get("Items", [])
        return from_dynamodb(items[0]) if items else None

    def save(self, cart: dict, expected_version: int) -> None:
        updated = dict(cart)
        updated["version"] = expected_version + 1
        self.table.put_item(
            Item=to_dynamodb(updated),
            ConditionExpression="#version = :expected",
            ExpressionAttributeNames={"#version": "version"},
            ExpressionAttributeValues={":expected": expected_version},
        )
