from __future__ import annotations

from copy import deepcopy

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from src.models.ticket import (
    canonical_dynamodb_value,
    human_session_guard_key,
    validate_guard_for_ticket,
    validate_marker_for_ticket,
    validate_ticket_for_write,
)

from .base import from_dynamodb, to_dynamodb


class TicketIdCollisionError(Exception):
    pass


class IdempotencyConflictError(Exception):
    pass


class HumanSessionGuardConflictError(Exception):
    pass


class ReusableTicketChangedError(Exception):
    pass


class TicketVersionConflictError(Exception):
    pass


TICKET_PAGINATION_STALLED = "TICKET_PAGINATION_STALLED"
MAX_INTERNAL_QUERY_PAGES = 1000


class TicketPaginationStalledError(Exception):
    code = TICKET_PAGINATION_STALLED

    def __init__(self):
        super().__init__("Ticket pagination did not make progress.")


class TicketRepository:
    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)
        self.client = dynamodb.meta.client
        self.table_name = table_name
        self.serializer = TypeSerializer()

    def create_with_idempotency(
        self,
        ticket: dict,
        marker: dict,
        now_epoch: int,
        *,
        guard: dict | None = None,
        expected_guard: dict | None = None,
    ) -> None:
        validated_ticket = self._validate_ticket(ticket)
        validated_marker = self._validate_marker(marker, validated_ticket)
        transact_items = [
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": self._serialize_item(validated_ticket),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
            self._marker_put(validated_marker, now_epoch),
        ]
        if guard is not None:
            validated_guard = self._validate_guard(guard, validated_ticket)
            transact_items.append(
                {
                    "Put": self._guard_put(
                        validated_guard,
                        expected_guard=expected_guard,
                    )
                }
            )
        try:
            self.client.transact_write_items(TransactItems=transact_items)
        except ClientError as exc:
            if not self._is_transaction_cancelled(exc):
                raise
            reasons = exc.response.get("CancellationReasons", [])
            if self._conditional_failed(reasons, 0):
                raise TicketIdCollisionError from exc
            if self._conditional_failed(reasons, 1):
                raise IdempotencyConflictError from exc
            if guard is not None and self._conditional_failed(reasons, 2):
                raise HumanSessionGuardConflictError from exc
            raise

    def bind_human_reuse(
        self,
        ticket: dict,
        marker: dict,
        guard: dict,
        now_epoch: int,
        *,
        expected_guard: dict | None = None,
    ) -> None:
        validated_ticket = self._validate_ticket(ticket)
        if validated_ticket["ticket_type"] != "human_assistance":
            raise ValueError("only human assistance tickets can be reused")
        validated_marker = self._validate_marker(marker, validated_ticket)
        validated_guard = self._validate_guard(guard, validated_ticket)
        values = {
            ":user_id": validated_ticket["user_id"],
            ":session_id": validated_ticket["session_id"],
            ":ticket_type": "human_assistance",
            ":open": "open",
            ":in_review": "in_review",
            ":waiting": "waiting_for_customer",
        }
        transact_items = [
            {
                "ConditionCheck": {
                    "TableName": self.table_name,
                    "Key": self._serialize_item(
                        {
                            "PK": validated_ticket["PK"],
                            "SK": validated_ticket["SK"],
                        }
                    ),
                    "ConditionExpression": (
                        "user_id = :user_id AND session_id = :session_id "
                        "AND #ticket_type = :ticket_type "
                        "AND #status IN (:open, :in_review, :waiting)"
                    ),
                    "ExpressionAttributeNames": {
                        "#ticket_type": "ticket_type",
                        "#status": "status",
                    },
                    "ExpressionAttributeValues": {
                        key: self.serializer.serialize(value)
                        for key, value in values.items()
                    },
                }
            },
            self._marker_put(validated_marker, now_epoch),
            {
                "Put": self._guard_put(
                    validated_guard,
                    expected_guard=expected_guard,
                )
            },
        ]
        try:
            self.client.transact_write_items(TransactItems=transact_items)
        except ClientError as exc:
            if not self._is_transaction_cancelled(exc):
                raise
            reasons = exc.response.get("CancellationReasons", [])
            if self._conditional_failed(reasons, 0):
                raise ReusableTicketChangedError from exc
            if self._conditional_failed(reasons, 1):
                raise IdempotencyConflictError from exc
            if self._conditional_failed(reasons, 2):
                raise HumanSessionGuardConflictError from exc
            raise

    def get(self, ticket_id: str) -> dict | None:
        response = self.table.get_item(
            Key={"PK": f"TICKET#{ticket_id}", "SK": "METADATA"},
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def get_idempotency_marker(self, idempotency_hash: str) -> dict | None:
        response = self.table.get_item(
            Key={
                "PK": f"IDEMPOTENCY#{idempotency_hash}",
                "SK": "METADATA",
            },
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def get_human_session_guard(
        self,
        user_id: str,
        session_id: str,
    ) -> dict | None:
        response = self.table.get_item(
            Key={
                "PK": human_session_guard_key(user_id, session_id),
                "SK": "METADATA",
            },
            ConsistentRead=True,
        )
        return from_dynamodb(response.get("Item"))

    def list_for_customer(self, user_id: str) -> list[dict]:
        kwargs = {
            "IndexName": "GSI1",
            "KeyConditionExpression": Key("GSI1PK").eq(f"CUSTOMER#{user_id}"),
        }
        items: list[dict] = []
        seen_cursors: set[str] = set()
        page_count = 0
        while True:
            page_count += 1
            if page_count > MAX_INTERNAL_QUERY_PAGES:
                raise TicketPaginationStalledError
            response = self.table.query(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                return from_dynamodb(items)
            cursor = response["LastEvaluatedKey"]
            fingerprint = canonical_dynamodb_value(cursor)
            if fingerprint in seen_cursors:
                raise TicketPaginationStalledError
            seen_cursors.add(fingerprint)
            kwargs["ExclusiveStartKey"] = cursor

    def query_status_page(
        self,
        status: str,
        *,
        limit: int,
        exclusive_start_key: dict | None = None,
    ) -> dict:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("query limit must be between 1 and 100")
        # DynamoDB GSI queries are eventually consistent.
        kwargs = {
            "IndexName": "GSI2",
            "KeyConditionExpression": Key("GSI2PK").eq(f"STATUS#{status}"),
            "Limit": limit,
            "ScanIndexForward": False,
        }
        if exclusive_start_key is not None:
            kwargs["ExclusiveStartKey"] = deepcopy(exclusive_start_key)
        response = self.table.query(**kwargs)
        return {
            "items": deepcopy(from_dynamodb(response.get("Items", []))),
            "last_evaluated_key": deepcopy(
                from_dynamodb(response.get("LastEvaluatedKey"))
            ),
        }

    def save(self, ticket: dict, expected_version: int) -> None:
        validated = self._validate_ticket(ticket)
        if validated["version"] != expected_version:
            raise ValueError("ticket version does not match expected_version")
        updated = {**validated, "version": expected_version + 1}
        updated = self._validate_ticket(updated)
        try:
            self.table.put_item(
                Item=to_dynamodb(updated),
                ConditionExpression="#version = :expected",
                ExpressionAttributeNames={"#version": "version"},
                ExpressionAttributeValues={":expected": expected_version},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == (
                "ConditionalCheckFailedException"
            ):
                raise TicketVersionConflictError from exc
            raise

    def _marker_put(self, marker: dict, now_epoch: int) -> dict:
        return {
            "Put": {
                "TableName": self.table_name,
                "Item": self._serialize_item(marker),
                "ConditionExpression": (
                    "attribute_not_exists(PK) OR expires_at <= :now"
                ),
                "ExpressionAttributeValues": {
                    ":now": self.serializer.serialize(now_epoch)
                },
            }
        }

    def _guard_put(
        self,
        guard: dict,
        *,
        expected_guard: dict | None,
    ) -> dict:
        put = {
            "TableName": self.table_name,
            "Item": self._serialize_item(guard),
        }
        if expected_guard is None:
            put["ConditionExpression"] = "attribute_not_exists(PK)"
            return put
        put.update(
            {
                "ConditionExpression": (
                    "#version = :expected_version "
                    "AND active_ticket_id = :expected_ticket_id"
                ),
                "ExpressionAttributeNames": {"#version": "version"},
                "ExpressionAttributeValues": {
                    ":expected_version": self.serializer.serialize(
                        expected_guard["version"]
                    ),
                    ":expected_ticket_id": self.serializer.serialize(
                        expected_guard["active_ticket_id"]
                    ),
                },
            }
        )
        return put

    @staticmethod
    def _validate_ticket(ticket: dict) -> dict:
        return validate_ticket_for_write(ticket)

    @staticmethod
    def _validate_marker(marker: dict, ticket: dict) -> dict:
        return validate_marker_for_ticket(marker, ticket)

    @staticmethod
    def _validate_guard(guard: dict, ticket: dict) -> dict:
        return validate_guard_for_ticket(guard, ticket)

    def _serialize_item(self, item: dict) -> dict:
        converted = to_dynamodb(item)
        return {
            key: self.serializer.serialize(value)
            for key, value in converted.items()
        }

    @staticmethod
    def _is_transaction_cancelled(exc: ClientError) -> bool:
        return exc.response.get("Error", {}).get("Code") == (
            "TransactionCanceledException"
        )

    @staticmethod
    def _conditional_failed(reasons: list[dict], index: int) -> bool:
        return (
            len(reasons) > index
            and reasons[index].get("Code") == "ConditionalCheckFailed"
        )
