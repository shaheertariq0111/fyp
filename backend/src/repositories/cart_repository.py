from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from .base import from_dynamodb, to_dynamodb


class CartVersionConflictError(RuntimeError):
    pass


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

    def find_by_cart_id(self, user_id: str, cart_id: str) -> dict | None:
        return self.get(user_id, cart_id)

    def find_by_cart_item_id(self, user_id: str, cart_item_id: str) -> dict | None:
        kwargs = {
            "KeyConditionExpression": (
                Key("PK").eq(user_id) & Key("SK").begins_with("CART#")
            ),
            "FilterExpression": Attr("cart_item_ids").contains(cart_item_id),
        }
        while True:
            response = self.table.query(**kwargs)
            items = response.get("Items", [])
            if items:
                return from_dynamodb(items[0])
            if "LastEvaluatedKey" not in response:
                return None
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def find_active_by_session(
        self,
        user_id: str,
        agent_session_id: str,
        terminal_statuses: set[str],
    ) -> dict | None:
        kwargs = {
            "KeyConditionExpression": (
                Key("PK").eq(user_id) & Key("SK").begins_with("CART#")
            ),
            "FilterExpression": (
                Attr("agent_session_id").eq(agent_session_id)
                & ~Attr("status").is_in(list(terminal_statuses))
            )
        }
        matches: list[dict] = []
        while True:
            response = self.table.query(**kwargs)
            matches.extend(from_dynamodb(item) for item in response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                if not matches:
                    return None
                return max(matches, key=lambda item: item.get("updated_at", ""))
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    def save(self, cart: dict, expected_version: int) -> None:
        updated = dict(cart)
        updated["version"] = expected_version + 1
        try:
            self.table.put_item(
                Item=to_dynamodb(updated),
                ConditionExpression="#version = :expected",
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues={":expected": expected_version},
            )
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                raise CartVersionConflictError("cart version changed") from exc
            raise
