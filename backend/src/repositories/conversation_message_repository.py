from __future__ import annotations

from .base import to_dynamodb


class ConversationMessageRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    def save(self, message: dict) -> None:
        self.table.put_item(Item=to_dynamodb(message))
