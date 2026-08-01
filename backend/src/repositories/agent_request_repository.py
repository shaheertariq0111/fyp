from __future__ import annotations

from botocore.exceptions import ClientError

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

    def claim_idempotency_key(
        self,
        marker: dict,
        *,
        now_epoch: int,
    ) -> bool:
        try:
            self.table.put_item(
                Item=to_dynamodb(marker),
                ConditionExpression=(
                    "attribute_not_exists(PK) OR expires_at <= :now"
                ),
                ExpressionAttributeValues={
                    ":now": now_epoch,
                },
            )
        except ClientError as exc:
            if (
                exc.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                return False
            raise
        return True

    def get_idempotency_key(self, message_id: str) -> dict | None:
        response = self.table.get_item(
            Key={
                "PK": f"agentflo-whatsapp-message:{message_id}",
                "SK": "IDEMPOTENCY",
            },
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def save_idempotency_key(self, marker: dict) -> None:
        self.table.put_item(Item=to_dynamodb(marker))

    def delete_idempotency_key(self, message_id: str) -> None:
        self.table.delete_item(
            Key={
                "PK": f"agentflo-whatsapp-message:{message_id}",
                "SK": "IDEMPOTENCY",
            }
        )
