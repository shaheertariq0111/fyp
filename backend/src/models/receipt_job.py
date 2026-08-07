from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


RECEIPT_QUEUE_KIND = "whatsapp_pdf_receipt"
RECEIPT_QUEUE_VERSION = 1
RECEIPT_QUEUE_MAX_BYTES = 512
RECEIPT_JOB_ID_PATTERN = re.compile(r"^rj1_[0-9a-f]{64}$")
RECEIPT_JOB_REQUIRED_FIELDS = frozenset({
    "PK",
    "SK",
    "job_id",
    "order_id",
    "receipt_version",
    "state",
    "version",
    "customer_number",
    "sender_id",
    "conversation_id",
    "request_id",
    "attempt_count",
    "enqueue_attempt_count",
    "created_at",
    "updated_at",
    "expires_at",
})
RECEIPT_JOB_OPTIONAL_FIELDS = frozenset({
    "GSI1PK",
    "GSI1SK",
    "lease_owner",
    "lease_expires_at",
    "s3_key",
    "provider_message_id",
    "generic_failure_code",
    "next_retry_at",
})
RECEIPT_JOB_ALLOWED_FIELDS = (
    RECEIPT_JOB_REQUIRED_FIELDS | RECEIPT_JOB_OPTIONAL_FIELDS
)
RECEIPT_JOB_MUTABLE_CHECKPOINT_FIELDS = frozenset({
    "enqueue_attempt_count",
    "GSI1PK",
    "GSI1SK",
    "s3_key",
    "provider_message_id",
    "generic_failure_code",
    "next_retry_at",
})
RECEIPT_JOB_REMOVABLE_CHECKPOINT_FIELDS = frozenset({
    "GSI1PK",
    "GSI1SK",
    "s3_key",
    "provider_message_id",
    "generic_failure_code",
    "next_retry_at",
})


class ReceiptJobState(str, Enum):
    PENDING_ENQUEUE = "pending_enqueue"
    QUEUED = "queued"
    PROCESSING = "processing"
    GENERATED = "generated"
    OUTBOUND_SENDING = "outbound_sending"
    SENT = "sent"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    MANUAL_REVIEW = "manual_review"


TERMINAL_RECEIPT_JOB_STATES = frozenset({
    ReceiptJobState.SENT.value,
    ReceiptJobState.PERMANENT_FAILURE.value,
    ReceiptJobState.MANUAL_REVIEW.value,
})


def _receipt_version(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("RECEIPT_VERSION_INVALID")
    return value


def receipt_job_id(order_id: str, receipt_version: int) -> str:
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("RECEIPT_ORDER_ID_REQUIRED")
    version = _receipt_version(receipt_version)
    identity = json.dumps(
        [order_id.strip(), version],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "rj1_" + hashlib.sha256(identity).hexdigest()


def validate_receipt_job_id(value: str) -> str:
    if not isinstance(value, str) or not RECEIPT_JOB_ID_PATTERN.fullmatch(value):
        raise ValueError("RECEIPT_JOB_ID_INVALID")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("RECEIPT_QUEUE_DUPLICATE_KEY")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ReceiptQueueMessage:
    job_id: str
    v: int = RECEIPT_QUEUE_VERSION
    kind: str = RECEIPT_QUEUE_KIND

    def __post_init__(self) -> None:
        if (
            type(self.v) is not int
            or self.v != RECEIPT_QUEUE_VERSION
            or not isinstance(self.kind, str)
            or self.kind != RECEIPT_QUEUE_KIND
        ):
            raise ValueError("RECEIPT_QUEUE_SCHEMA_INVALID")
        validate_receipt_job_id(self.job_id)

    def serialize(self) -> str:
        body = json.dumps(
            {"v": self.v, "kind": self.kind, "job_id": self.job_id},
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(body.encode("utf-8")) > RECEIPT_QUEUE_MAX_BYTES:
            raise ValueError("RECEIPT_QUEUE_BODY_TOO_LARGE")
        return body

    @classmethod
    def parse(cls, body: str | bytes) -> "ReceiptQueueMessage":
        if isinstance(body, str):
            raw = body.encode("utf-8")
        elif isinstance(body, bytes):
            raw = body
        else:
            raise ValueError("RECEIPT_QUEUE_JSON_INVALID")
        if len(raw) > RECEIPT_QUEUE_MAX_BYTES:
            raise ValueError("RECEIPT_QUEUE_BODY_TOO_LARGE")
        try:
            decoded = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("RECEIPT_QUEUE_JSON_INVALID") from exc
        if not isinstance(decoded, dict) or set(decoded) != {"v", "kind", "job_id"}:
            raise ValueError("RECEIPT_QUEUE_SCHEMA_INVALID")
        if (
            type(decoded["v"]) is not int
            or not isinstance(decoded["kind"], str)
            or not isinstance(decoded["job_id"], str)
        ):
            raise ValueError("RECEIPT_QUEUE_SCHEMA_INVALID")
        return cls(
            v=decoded["v"],
            kind=decoded["kind"],
            job_id=decoded["job_id"],
        )


def _non_empty_string(record: dict[str, Any], field: str) -> bool:
    value = record.get(field)
    return isinstance(value, str) and bool(value.strip())


def _integer_at_least(record: dict[str, Any], field: str, minimum: int) -> bool:
    value = record.get(field)
    return type(value) is int and value >= minimum


def validate_receipt_job_checkpoint_values(values: dict[str, Any]) -> None:
    if (
        not isinstance(values, dict)
        or not set(values).issubset(RECEIPT_JOB_MUTABLE_CHECKPOINT_FIELDS)
    ):
        raise ValueError("RECEIPT_JOB_UPDATE_INVALID")
    if "enqueue_attempt_count" in values and (
        type(values["enqueue_attempt_count"]) is not int
        or values["enqueue_attempt_count"] < 0
    ):
        raise ValueError("RECEIPT_JOB_COUNTER_INVALID")

    outbox_fields = {"GSI1PK", "GSI1SK"}
    if (
        bool(set(values).intersection(outbox_fields))
        and not outbox_fields.issubset(values)
    ):
        raise ValueError("RECEIPT_JOB_OUTBOX_INVALID")
    if outbox_fields.issubset(values) and (
        values["GSI1PK"] != "RECEIPT_OUTBOX"
        or type(values["GSI1SK"]) is not int
        or values["GSI1SK"] < 0
    ):
        raise ValueError("RECEIPT_JOB_OUTBOX_INVALID")

    for field in ("s3_key", "provider_message_id", "generic_failure_code"):
        if field in values and (
            not isinstance(values[field], str) or not values[field].strip()
        ):
            raise ValueError("RECEIPT_JOB_CHECKPOINT_INVALID")
    if "next_retry_at" in values and (
        type(values["next_retry_at"]) is not int
        or values["next_retry_at"] < 0
    ):
        raise ValueError("RECEIPT_JOB_CHECKPOINT_INVALID")


def validate_receipt_job_record(record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise ValueError("RECEIPT_JOB_RECORD_INVALID")
    if (
        not RECEIPT_JOB_REQUIRED_FIELDS.issubset(record)
        or not set(record).issubset(RECEIPT_JOB_ALLOWED_FIELDS)
    ):
        raise ValueError("RECEIPT_JOB_RECORD_INVALID")

    job_id = validate_receipt_job_id(record["job_id"])
    if record["PK"] != f"JOB#{job_id}" or record["SK"] != "METADATA":
        raise ValueError("RECEIPT_JOB_RECORD_INVALID")
    if not _non_empty_string(record, "order_id"):
        raise ValueError("RECEIPT_ORDER_ID_REQUIRED")
    if record["order_id"] != record["order_id"].strip():
        raise ValueError("RECEIPT_ORDER_ID_INVALID")
    _receipt_version(record["receipt_version"])
    if job_id != receipt_job_id(record["order_id"], record["receipt_version"]):
        raise ValueError("RECEIPT_JOB_IDENTITY_INVALID")
    if (
        not isinstance(record["state"], str)
        or record["state"] not in {state.value for state in ReceiptJobState}
    ):
        raise ValueError("RECEIPT_JOB_STATE_INVALID")
    if not _integer_at_least(record, "version", 1):
        raise ValueError("RECEIPT_JOB_VERSION_INVALID")
    for field in ("customer_number", "sender_id", "conversation_id", "request_id"):
        if not _non_empty_string(record, field):
            raise ValueError("RECEIPT_JOB_ROUTING_INVALID")
    for field in ("attempt_count", "enqueue_attempt_count"):
        if not _integer_at_least(record, field, 0):
            raise ValueError("RECEIPT_JOB_COUNTER_INVALID")
    for field in ("created_at", "updated_at"):
        if not _non_empty_string(record, field):
            raise ValueError("RECEIPT_JOB_TIMESTAMP_INVALID")
    if not _integer_at_least(record, "expires_at", 1):
        raise ValueError("RECEIPT_JOB_EXPIRY_INVALID")

    has_outbox_partition = "GSI1PK" in record
    has_outbox_sort = "GSI1SK" in record
    if has_outbox_partition != has_outbox_sort:
        raise ValueError("RECEIPT_JOB_OUTBOX_INVALID")
    if has_outbox_partition and (
        record["GSI1PK"] != "RECEIPT_OUTBOX"
        or not _integer_at_least(record, "GSI1SK", 0)
    ):
        raise ValueError("RECEIPT_JOB_OUTBOX_INVALID")

    if "lease_owner" in record and not _non_empty_string(record, "lease_owner"):
        raise ValueError("RECEIPT_JOB_LEASE_INVALID")
    if "lease_expires_at" in record and not _integer_at_least(
        record,
        "lease_expires_at",
        1,
    ):
        raise ValueError("RECEIPT_JOB_LEASE_INVALID")
    if ("lease_owner" in record) != ("lease_expires_at" in record):
        raise ValueError("RECEIPT_JOB_LEASE_INVALID")

    checkpoint_values = {
        field: record[field]
        for field in RECEIPT_JOB_MUTABLE_CHECKPOINT_FIELDS
        if field in record
    }
    validate_receipt_job_checkpoint_values(checkpoint_values)
