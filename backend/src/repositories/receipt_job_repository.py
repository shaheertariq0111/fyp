from __future__ import annotations

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from src.models.receipt_job import (
    RECEIPT_JOB_REMOVABLE_CHECKPOINT_FIELDS,
    ReceiptJobState,
    validate_receipt_job_checkpoint_values,
    validate_receipt_job_id,
    validate_receipt_job_record,
)

from .base import from_dynamodb, to_dynamodb


class ReceiptJobConditionFailed(RuntimeError):
    pass


class ReceiptJobRepository:
    """Conditional persistence primitives for durable receipt jobs."""

    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    @staticmethod
    def _key(job_id: str) -> dict[str, str]:
        validated = validate_receipt_job_id(job_id)
        return {"PK": f"JOB#{validated}", "SK": "METADATA"}

    @staticmethod
    def _conditional(exc: ClientError) -> bool:
        return (
            exc.response.get("Error", {}).get("Code")
            == "ConditionalCheckFailedException"
        )

    def create_if_absent(self, record: dict) -> bool:
        validate_receipt_job_record(record)
        try:
            self.table.put_item(
                Item=to_dynamodb(record),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as exc:
            if self._conditional(exc):
                return False
            raise
        return True

    def get(self, job_id: str) -> dict | None:
        response = self.table.get_item(
            Key=self._key(job_id),
            ConsistentRead=True,
        )
        record = from_dynamodb(response.get("Item"))
        if record is not None:
            validate_receipt_job_record(record)
        return record

    def transition(
        self,
        job_id: str,
        *,
        expected_states: set[str],
        expected_version: int,
        next_state: str,
        updated_at: str,
        values: dict | None = None,
        remove: tuple[str, ...] = (),
    ) -> dict:
        valid_states = {state.value for state in ReceiptJobState}
        if not expected_states or not expected_states.issubset(valid_states):
            raise ValueError("RECEIPT_JOB_EXPECTED_STATE_INVALID")
        if next_state not in valid_states:
            raise ValueError("RECEIPT_JOB_STATE_INVALID")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("RECEIPT_JOB_VERSION_INVALID")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise ValueError("RECEIPT_JOB_TIMESTAMP_INVALID")

        checkpoint_values = {} if values is None else values
        validate_receipt_job_checkpoint_values(checkpoint_values)
        if (
            not isinstance(remove, tuple)
            or not set(remove).issubset(RECEIPT_JOB_REMOVABLE_CHECKPOINT_FIELDS)
            or len(set(remove)) != len(remove)
            or set(checkpoint_values).intersection(remove)
        ):
            raise ValueError("RECEIPT_JOB_UPDATE_INVALID")
        outbox_fields = {"GSI1PK", "GSI1SK"}
        if (
            bool(set(remove).intersection(outbox_fields))
            and not outbox_fields.issubset(remove)
        ):
            raise ValueError("RECEIPT_JOB_UPDATE_INVALID")

        names = {
            "#state": "state",
            "#version": "version",
            "#updated": "updated_at",
        }
        expression_values = {
            ":next": next_state,
            ":version": expected_version,
            ":one": 1,
            ":updated": updated_at,
        }
        state_tokens = []
        for index, state in enumerate(sorted(expected_states)):
            token = f":state{index}"
            state_tokens.append(token)
            expression_values[token] = state

        assignments = [
            "#state = :next",
            "#version = #version + :one",
            "#updated = :updated",
        ]
        for index, (field, value) in enumerate(checkpoint_values.items()):
            name_token = f"#field{index}"
            value_token = f":value{index}"
            names[name_token] = field
            expression_values[value_token] = value
            assignments.append(f"{name_token} = {value_token}")

        update_expression = "SET " + ", ".join(assignments)
        if remove:
            remove_tokens = []
            for index, field in enumerate(remove):
                token = f"#remove{index}"
                names[token] = field
                remove_tokens.append(token)
            update_expression += " REMOVE " + ", ".join(remove_tokens)

        try:
            response = self.table.update_item(
                Key=self._key(job_id),
                UpdateExpression=update_expression,
                ConditionExpression=(
                    f"#version = :version AND #state IN ({', '.join(state_tokens)})"
                ),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=to_dynamodb(expression_values),
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if self._conditional(exc):
                raise ReceiptJobConditionFailed(
                    "RECEIPT_JOB_CONDITION_FAILED"
                ) from exc
            raise
        record = from_dynamodb(response["Attributes"])
        validate_receipt_job_record(record)
        return record

    def mark_retryable_failure(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="retryable_failure", **kwargs)

    def mark_permanent_failure(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="permanent_failure", **kwargs)

    def mark_manual_review(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="manual_review", **kwargs)

    def mark_sent(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="sent", **kwargs)

    def acquire_lease(
        self,
        job_id: str,
        *,
        owner: str,
        now_epoch: int,
        lease_expires_at: int,
        updated_at: str,
    ) -> dict:
        self._validate_lease_arguments(
            owner=owner,
            updated_at=updated_at,
            epoch=now_epoch,
            minimum=0,
        )
        self._validate_lease_arguments(
            owner=owner,
            updated_at=updated_at,
            epoch=lease_expires_at,
            minimum=1,
        )
        if lease_expires_at <= now_epoch:
            raise ValueError("RECEIPT_JOB_LEASE_INVALID")
        try:
            response = self.table.update_item(
                Key=self._key(job_id),
                UpdateExpression=(
                    "SET lease_owner = :owner, lease_expires_at = :expires, "
                    "updated_at = :updated, "
                    "attempt_count = if_not_exists(attempt_count, :zero) + :one"
                ),
                ConditionExpression=(
                    "attribute_exists(PK) AND (attribute_not_exists(lease_owner) "
                    "OR lease_expires_at <= :now OR lease_owner = :owner)"
                ),
                ExpressionAttributeValues=to_dynamodb({
                    ":owner": owner,
                    ":expires": lease_expires_at,
                    ":updated": updated_at,
                    ":now": now_epoch,
                    ":zero": 0,
                    ":one": 1,
                }),
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if self._conditional(exc):
                raise ReceiptJobConditionFailed("RECEIPT_JOB_LEASE_HELD") from exc
            raise
        record = from_dynamodb(response["Attributes"])
        validate_receipt_job_record(record)
        return record

    def extend_lease(
        self,
        job_id: str,
        *,
        owner: str,
        lease_expires_at: int,
        updated_at: str,
    ) -> bool:
        self._validate_lease_arguments(
            owner=owner,
            updated_at=updated_at,
            epoch=lease_expires_at,
            minimum=1,
        )
        return self._lease_update(
            job_id,
            owner=owner,
            update="SET lease_expires_at = :expires, updated_at = :updated",
            values={":expires": lease_expires_at, ":updated": updated_at},
        )

    def release_lease(
        self,
        job_id: str,
        *,
        owner: str,
        updated_at: str,
    ) -> bool:
        self._validate_lease_arguments(
            owner=owner,
            updated_at=updated_at,
            epoch=1,
            minimum=1,
        )
        return self._lease_update(
            job_id,
            owner=owner,
            update="SET updated_at = :updated REMOVE lease_owner, lease_expires_at",
            values={":updated": updated_at},
        )

    def _lease_update(
        self,
        job_id: str,
        *,
        owner: str,
        update: str,
        values: dict,
    ) -> bool:
        expression_values = dict(values)
        expression_values[":owner"] = owner
        try:
            self.table.update_item(
                Key=self._key(job_id),
                UpdateExpression=update,
                ConditionExpression="lease_owner = :owner",
                ExpressionAttributeValues=to_dynamodb(expression_values),
            )
        except ClientError as exc:
            if self._conditional(exc):
                return False
            raise
        return True

    @staticmethod
    def _validate_lease_arguments(
        *,
        owner: str,
        updated_at: str,
        epoch: int,
        minimum: int,
    ) -> None:
        if (
            not isinstance(owner, str)
            or not owner.strip()
            or not isinstance(updated_at, str)
            or not updated_at.strip()
            or type(epoch) is not int
            or epoch < minimum
        ):
            raise ValueError("RECEIPT_JOB_LEASE_INVALID")

    def query_due(
        self,
        *,
        now_epoch: int,
        limit: int = 25,
        index_name: str = "DueJobsIndex",
    ) -> list[dict]:
        if type(now_epoch) is not int or now_epoch < 0:
            raise ValueError("RECEIPT_JOB_DUE_TIME_INVALID")
        if type(limit) is not int or limit < 1:
            raise ValueError("RECEIPT_JOB_QUERY_LIMIT_INVALID")
        response = self.table.query(
            IndexName=index_name,
            KeyConditionExpression=(
                Key("GSI1PK").eq("RECEIPT_OUTBOX")
                & Key("GSI1SK").lte(now_epoch)
            ),
            Limit=limit,
        )
        records = [
            from_dynamodb(item)
            for item in response.get("Items", [])
        ]
        for record in records:
            validate_receipt_job_record(record)
        return records
