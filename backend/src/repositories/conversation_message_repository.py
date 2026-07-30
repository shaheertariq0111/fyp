from __future__ import annotations

from boto3.dynamodb.conditions import Key

from .base import from_dynamodb
from .base import to_dynamodb


class ConversationMessageRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def save(self, message: dict) -> None:
        self.table.put_item(Item=to_dynamodb(message))

    def list_recent_whatsapp_messages(self, *, limit: int) -> list[dict]:
        response = self.table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq("CHANNEL#whatsapp"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return from_dynamodb(response.get("Items", []))

    def list_for_conversation(self, conversation_id: str) -> list[dict]:
        response = self.table.query(
            KeyConditionExpression=Key("PK").eq(f"CONVERSATION#{conversation_id}"),
            ScanIndexForward=True,
        )
        return from_dynamodb(response.get("Items", []))
