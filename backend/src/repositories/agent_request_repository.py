from __future__ import annotations

from .base import from_dynamodb, to_dynamodb


class AgentRequestRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def create(self, request: dict) -> None:
        self.table.put_item(
            Item=to_dynamodb(request),
            ConditionExpression="attribute_not_exists(PK)",
        )

    def get(self, request_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": f"REQUEST#{request_id}", "SK": "METADATA"},
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def save(self, request: dict) -> None:
        self.table.put_item(Item=to_dynamodb(request))
