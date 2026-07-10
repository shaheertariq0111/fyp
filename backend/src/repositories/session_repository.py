from datetime import datetime, timezone

from .base import from_dynamodb, to_dynamodb


class MenuSessionRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def create(self, session: dict) -> None:
        self.table.put_item(
            Item=to_dynamodb(session),
            ConditionExpression="attribute_not_exists(PK)",
        )

    def get_by_token_hash(self, token_hash: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": f"MENU_SESSION#{token_hash}", "SK": "METADATA"},
            ConsistentRead=True,
        )
        item = from_dynamodb(response.get("Item"))
        if not item or item.get("status") != "active":
            return None
        if item.get("expires_at", 0) <= int(datetime.now(timezone.utc).timestamp()):
            return None
        return item
