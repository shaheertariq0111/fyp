from __future__ import annotations

from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from src.models.whatsapp_voice_job import validate_voice_job_record

from .base import from_dynamodb, to_dynamodb


class VoiceJobConditionFailed(RuntimeError):
    pass


class WhatsAppVoiceJobRepository:
    """Conditional persistence for the future dedicated voice-job table."""

    def __init__(self, dynamodb, table_name: str):
        self.table = dynamodb.Table(table_name)

    @staticmethod
    def _key(job_id: str) -> dict[str, str]:
        return {"PK": f"JOB#{job_id}", "SK": "METADATA"}

    @staticmethod
    def _conditional(exc: ClientError) -> bool:
        return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"

    def create_if_absent(self, record: dict) -> bool:
        validate_voice_job_record(record, require_audio_id=True)
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
        response = self.table.get_item(Key=self._key(job_id), ConsistentRead=True)
        record = from_dynamodb(response.get("Item"))
        if record is not None:
            validate_voice_job_record(record, require_audio_id=False)
        return record

    def transition(self, job_id: str, *, expected_states: set[str], expected_version: int, next_state: str, updated_at: str, values: dict | None = None, remove: tuple[str, ...] = ()) -> dict:
        names = {"#state": "state", "#version": "version", "#updated": "updated_at"}
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
        assignments = ["#state = :next", "#version = #version + :one", "#updated = :updated"]
        for index, (field, value) in enumerate((values or {}).items()):
            name_token, value_token = f"#field{index}", f":value{index}"
            names[name_token] = field
            expression_values[value_token] = value
            assignments.append(f"{name_token} = {value_token}")
        update = "SET " + ", ".join(assignments)
        if remove:
            remove_tokens = []
            for index, field in enumerate(remove):
                token = f"#remove{index}"
                names[token] = field
                remove_tokens.append(token)
            update += " REMOVE " + ", ".join(remove_tokens)
        try:
            response = self.table.update_item(
                Key=self._key(job_id),
                UpdateExpression=update,
                ConditionExpression=f"#version = :version AND #state IN ({', '.join(state_tokens)})",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=to_dynamodb(expression_values),
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if self._conditional(exc):
                raise VoiceJobConditionFailed("VOICE_JOB_CONDITION_FAILED") from exc
            raise
        return from_dynamodb(response["Attributes"])

    def checkpoint_identifiers(self, job_id: str, *, expected_state: str, expected_version: int, next_state: str, updated_at: str, request_id: str, customer_id: str, session_id: str) -> dict:
        return self.transition(
            job_id, expected_states={expected_state}, expected_version=expected_version,
            next_state=next_state, updated_at=updated_at,
            values={"request_id": request_id, "customer_id": customer_id, "session_id": session_id},
        )

    def mark_retryable_failure(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="retryable_failure", **kwargs)

    def mark_permanent_failure(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="permanent_failure", **kwargs)

    def mark_manual_review(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="manual_review", **kwargs)

    def mark_terminal(self, job_id: str, **kwargs) -> dict:
        return self.transition(job_id, next_state="completed", **kwargs)

    def acquire_lease(self, job_id: str, *, owner: str, now_epoch: int, lease_expires_at: int, updated_at: str) -> dict:
        try:
            response = self.table.update_item(
                Key=self._key(job_id),
                UpdateExpression=(
                    "SET lease_owner = :owner, lease_expires_at = :expires, "
                    "updated_at = :updated, attempt_count = if_not_exists(attempt_count, :zero) + :one"
                ),
                ConditionExpression=(
                    "attribute_exists(PK) AND (attribute_not_exists(lease_owner) "
                    "OR lease_expires_at <= :now OR lease_owner = :owner)"
                ),
                ExpressionAttributeValues=to_dynamodb({
                    ":owner": owner, ":expires": lease_expires_at, ":updated": updated_at,
                    ":now": now_epoch, ":zero": 0, ":one": 1,
                }),
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if self._conditional(exc):
                raise VoiceJobConditionFailed("VOICE_JOB_LEASE_HELD") from exc
            raise
        return from_dynamodb(response["Attributes"])

    def extend_lease(self, job_id: str, *, owner: str, lease_expires_at: int, updated_at: str) -> bool:
        return self._lease_update(job_id, owner=owner, update=(
            "SET lease_expires_at = :expires, updated_at = :updated"
        ), values={":expires": lease_expires_at, ":updated": updated_at})

    def release_lease(self, job_id: str, *, owner: str, updated_at: str) -> bool:
        return self._lease_update(
            job_id, owner=owner,
            update="SET updated_at = :updated REMOVE lease_owner, lease_expires_at",
            values={":updated": updated_at},
        )

    def _lease_update(self, job_id: str, *, owner: str, update: str, values: dict) -> bool:
        values[":owner"] = owner
        try:
            self.table.update_item(
                Key=self._key(job_id), UpdateExpression=update,
                ConditionExpression="lease_owner = :owner",
                ExpressionAttributeValues=to_dynamodb(values),
            )
        except ClientError as exc:
            if self._conditional(exc):
                return False
            raise
        return True

    def query_due(self, *, due_partition: str, now_epoch: int, limit: int = 25, index_name: str = "DueJobsIndex") -> list[dict]:
        response = self.table.query(
            IndexName=index_name,
            KeyConditionExpression=Key("GSI1PK").eq(due_partition) & Key("GSI1SK").lte(now_epoch),
            Limit=limit,
        )
        records = [from_dynamodb(item) for item in response.get("Items", [])]
        for record in records:
            validate_voice_job_record(record, require_audio_id=False)
        return records
