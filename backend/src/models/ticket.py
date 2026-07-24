from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Literal

from boto3.dynamodb.types import Binary, TypeSerializer
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError
from pydantic_core import PydanticCustomError


TicketType = Literal["human_assistance", "order_complaint"]
TicketStatus = Literal[
    "open",
    "in_review",
    "waiting_for_customer",
    "resolved",
    "closed",
]
TicketPriority = Literal["normal", "high", "urgent"]

TICKET_TYPES = {"human_assistance", "order_complaint"}
TICKET_STATUSES = {
    "open",
    "in_review",
    "waiting_for_customer",
    "resolved",
    "closed",
}
NON_TERMINAL_TICKET_STATUSES = {
    "open",
    "in_review",
    "waiting_for_customer",
}
TICKET_PRIORITIES = {"normal", "high", "urgent"}
DEFAULT_TICKET_PRIORITY = "normal"
DEFAULT_TICKET_STATUS = "open"
IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60
MAX_DESCRIPTION_LENGTH = 4_000
MAX_CATEGORY_LENGTH = 100
MAX_CUSTOMER_NAME_LENGTH = 200
MAX_CUSTOMER_PHONE_LENGTH = 64
MAX_SOURCE_LENGTH = 64
MAX_ACTOR_LENGTH = 200
MAX_ADMIN_NOTE_LENGTH = 2_000
MAX_NOTE_ID_LENGTH = 19
MAX_STATUS_HISTORY = 100
MAX_PRIORITY_HISTORY = 100
MAX_ADMIN_NOTES = 100
MAX_TICKET_ITEM_BYTES = 350 * 1024
TICKET_ITEM_TOO_LARGE = "TICKET_ITEM_TOO_LARGE"
INVALID_TICKET_ID = "INVALID_TICKET_ID"
INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
INVALID_TICKET_RECORD = "INVALID_TICKET_RECORD"
TICKET_ID_PATTERN = re.compile(r"^TKT-([0-9]{8})-([A-F0-9]{6})$")
NOTE_ID_PATTERN = re.compile(r"^NTE-([0-9]{8})-([A-F0-9]{6})$")
TICKET_STATUS_LABELS = {
    "open": "Open",
    "in_review": "In review",
    "waiting_for_customer": "Waiting for customer",
    "resolved": "Resolved",
    "closed": "Closed",
}


def generate_ticket_id(now: datetime) -> str:
    return f"TKT-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


def generate_note_id(now: datetime) -> str:
    normalized = now.astimezone(timezone.utc)
    return f"NTE-{normalized:%Y%m%d}-{secrets.token_hex(3).upper()}"


class TicketDomainValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_ticket_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PydanticCustomError(
            INVALID_TIMESTAMP,
            "timestamp must be a timezone-aware ISO-8601 string",
        )
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
        offset = parsed.utcoffset()
    except (TypeError, ValueError):
        parsed = None
        offset = None
    if parsed is None or parsed.tzinfo is None or offset is None:
        raise PydanticCustomError(
            INVALID_TIMESTAMP,
            "timestamp must be a timezone-aware ISO-8601 string",
        )
    return parsed.astimezone(timezone.utc).isoformat()


def human_session_guard_key(user_id: str, session_id: str) -> str:
    identity = f"{user_id}\0{session_id}\0human_assistance".encode("utf-8")
    return f"HUMAN_SESSION#{hashlib.sha256(identity).hexdigest()}"


def _json_safe_attribute_value(value):
    if isinstance(value, (bytes, bytearray, Binary)):
        return {
            "__binary__": base64.b64encode(bytes(value)).decode("ascii")
        }
    if isinstance(value, dict):
        return {
            key: _json_safe_attribute_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        return [_json_safe_attribute_value(item) for item in value]
    return value


def ticket_item_size_bytes(item: dict) -> int:
    serializer = TypeSerializer()
    serialized = {
        key: serializer.serialize(value)
        for key, value in item.items()
    }
    conservative = _json_safe_attribute_value(serialized)
    return len(
        json.dumps(
            conservative,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def canonical_dynamodb_value(value) -> str:
    serialized = TypeSerializer().serialize(value)
    return json.dumps(
        _json_safe_attribute_value(serialized),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _validation_error_code(exc: ValidationError) -> str:
    for error in exc.errors():
        if error["type"] in {INVALID_TICKET_ID, INVALID_TIMESTAMP}:
            return error["type"]
    return INVALID_TICKET_RECORD


class StatusHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_status: TicketStatus
    new_status: TicketStatus
    timestamp: str = Field(min_length=1)
    actor: str | None = Field(default=None, max_length=MAX_ACTOR_LENGTH)
    reason: str | None = Field(
        default=None,
        max_length=MAX_ADMIN_NOTE_LENGTH,
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> str:
        return normalize_ticket_timestamp(value)

    @field_validator("actor", "reason")
    @classmethod
    def validate_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value


class AdminNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: str | None = Field(default=None, max_length=MAX_NOTE_ID_LENGTH)
    text: str = Field(min_length=1, max_length=MAX_ADMIN_NOTE_LENGTH)
    timestamp: str = Field(min_length=1)
    actor: str | None = Field(default=None, max_length=MAX_ACTOR_LENGTH)

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> str:
        return normalize_ticket_timestamp(value)

    @field_validator("note_id")
    @classmethod
    def validate_note_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = NOTE_ID_PATTERN.fullmatch(value)
        if not match:
            raise ValueError("note_id has an invalid format")
        try:
            datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError as exc:
            raise ValueError("note_id contains an invalid date") from exc
        return value

    @field_validator("text", "actor")
    @classmethod
    def validate_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value


class PriorityHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_priority: TicketPriority
    new_priority: TicketPriority
    timestamp: str = Field(min_length=1)
    actor: str = Field(min_length=1, max_length=MAX_ACTOR_LENGTH)
    reason: str | None = Field(
        default=None,
        max_length=MAX_ADMIN_NOTE_LENGTH,
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> str:
        return normalize_ticket_timestamp(value)

    @field_validator("actor", "reason")
    @classmethod
    def validate_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    user_id: str
    customer_id: str | None = None
    customer_name: str | None = Field(
        default=None, max_length=MAX_CUSTOMER_NAME_LENGTH
    )
    customer_phone: str | None = Field(
        default=None, max_length=MAX_CUSTOMER_PHONE_LENGTH
    )
    session_id: str | None = None
    ticket_type: TicketType
    category: str = Field(min_length=1, max_length=MAX_CATEGORY_LENGTH)
    description: str | None = Field(
        default=None, max_length=MAX_DESCRIPTION_LENGTH
    )
    priority: TicketPriority = DEFAULT_TICKET_PRIORITY
    status: TicketStatus = DEFAULT_TICKET_STATUS
    order_id: str | None = None
    order_status_snapshot: str | None = None
    source: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    status_history: list[StatusHistoryEntry] = Field(
        default_factory=list, max_length=MAX_STATUS_HISTORY
    )
    priority_history: list[PriorityHistoryEntry] = Field(
        default_factory=list, max_length=MAX_PRIORITY_HISTORY
    )
    admin_notes: list[AdminNote] = Field(
        default_factory=list, max_length=MAX_ADMIN_NOTES
    )
    version: int = Field(default=1, strict=True, ge=1)
    PK: str
    SK: Literal["METADATA"] = "METADATA"
    GSI1PK: str
    GSI1SK: str
    GSI2PK: str
    GSI2SK: str

    @field_validator(
        "user_id",
        "category",
        "source",
        "created_at",
        "updated_at",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator(
        "customer_id",
        "customer_name",
        "customer_phone",
        "session_id",
        "description",
        "order_id",
        "order_status_snapshot",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("ticket_id")
    @classmethod
    def validate_ticket_id(cls, value: str) -> str:
        match = TICKET_ID_PATTERN.fullmatch(value)
        if not match:
            raise PydanticCustomError(
                INVALID_TICKET_ID, "ticket_id has an invalid format"
            )
        try:
            datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError as exc:
            raise PydanticCustomError(
                INVALID_TICKET_ID, "ticket_id contains an invalid date"
            ) from exc
        return value

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> str:
        return normalize_ticket_timestamp(value)

    @model_validator(mode="after")
    def validate_record(self) -> "Ticket":
        note_ids = [
            note.note_id
            for note in self.admin_notes
            if note.note_id is not None
        ]
        if len(note_ids) != len(set(note_ids)):
            raise ValueError("admin note IDs must be unique")
        if datetime.fromisoformat(self.created_at) > datetime.fromisoformat(
            self.updated_at
        ):
            raise PydanticCustomError(
                INVALID_TIMESTAMP,
                "created_at must not be after updated_at",
            )
        if self.ticket_type == "human_assistance" and not self.session_id:
            raise ValueError("human assistance requires session_id")
        if self.ticket_type == "order_complaint":
            if not self.order_id:
                raise ValueError("order complaint requires order_id")
            if not self.description:
                raise ValueError("order complaint requires description")
            if not self.order_status_snapshot:
                raise ValueError("order complaint requires order_status_snapshot")

        expected = {
            "PK": f"TICKET#{self.ticket_id}",
            "SK": "METADATA",
            "GSI1PK": f"CUSTOMER#{self.user_id}",
            "GSI1SK": f"CREATED#{self.created_at}#{self.ticket_id}",
            "GSI2PK": f"STATUS#{self.status}",
            "GSI2SK": f"UPDATED#{self.updated_at}#{self.ticket_id}",
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(f"{field_name} is inconsistent with ticket data")
        return self


class IdempotencyMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    PK: str
    SK: Literal["METADATA"] = "METADATA"
    ticket_id: str
    user_id: str
    operation: TicketType
    expires_at: int = Field(strict=True, ge=0)

    @field_validator("ticket_id")
    @classmethod
    def validate_ticket_id(cls, value: str) -> str:
        match = TICKET_ID_PATTERN.fullmatch(value)
        if not match:
            raise PydanticCustomError(
                INVALID_TICKET_ID, "ticket_id has an invalid format"
            )
        try:
            datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError as exc:
            raise PydanticCustomError(
                INVALID_TICKET_ID, "ticket_id contains an invalid date"
            ) from exc
        return value

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("user_id must not be blank")
        return value

    @field_validator("PK")
    @classmethod
    def validate_pk(cls, value: str) -> str:
        if not re.fullmatch(r"IDEMPOTENCY#[0-9a-f]{64}", value):
            raise ValueError("idempotency marker PK is invalid")
        return value


class HumanSessionGuard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    PK: str
    SK: Literal["METADATA"] = "METADATA"
    user_id: str
    session_id: str
    operation: Literal["human_assistance"] = "human_assistance"
    active_ticket_id: str
    updated_at: str = Field(min_length=1)
    version: int = Field(strict=True, ge=1)

    @field_validator("user_id", "session_id", "updated_at")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("active_ticket_id")
    @classmethod
    def validate_ticket_id(cls, value: str) -> str:
        match = TICKET_ID_PATTERN.fullmatch(value)
        if not match:
            raise PydanticCustomError(
                INVALID_TICKET_ID,
                "active_ticket_id has an invalid format",
            )
        try:
            datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError as exc:
            raise PydanticCustomError(
                INVALID_TICKET_ID,
                "active_ticket_id contains an invalid date",
            ) from exc
        return value

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> str:
        return normalize_ticket_timestamp(value)


def validate_ticket_for_write(ticket: dict) -> dict:
    try:
        validated = Ticket.model_validate(ticket).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise TicketDomainValidationError(
            _validation_error_code(exc),
            "The ticket record is invalid.",
        ) from exc
    if ticket_item_size_bytes(validated) > MAX_TICKET_ITEM_BYTES:
        raise TicketDomainValidationError(
            TICKET_ITEM_TOO_LARGE,
            "The ticket item exceeds the configured size limit.",
        )
    return validated


def validate_marker_for_ticket(marker: dict, ticket: dict) -> dict:
    try:
        validated = IdempotencyMarker.model_validate(marker).model_dump()
    except ValidationError as exc:
        raise TicketDomainValidationError(
            _validation_error_code(exc),
            "The idempotency marker is invalid.",
        ) from exc
    if validated["ticket_id"] != ticket["ticket_id"]:
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "Marker ticket identity does not match.",
        )
    if validated["user_id"] != ticket["user_id"]:
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "Marker user identity does not match.",
        )
    if validated["operation"] != ticket["ticket_type"]:
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "Marker operation does not match.",
        )
    return validated


def validate_guard_for_ticket(guard: dict, ticket: dict) -> dict:
    try:
        validated = HumanSessionGuard.model_validate(guard).model_dump()
    except ValidationError as exc:
        raise TicketDomainValidationError(
            _validation_error_code(exc),
            "The human-session guard is invalid.",
        ) from exc
    if validated["PK"] != human_session_guard_key(
        validated["user_id"], validated["session_id"]
    ):
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "Guard key is inconsistent with its identity.",
        )
    if validated["user_id"] != ticket["user_id"]:
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "Guard user identity does not match.",
        )
    if validated["session_id"] != ticket["session_id"]:
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "Guard session identity does not match.",
        )
    if validated["active_ticket_id"] != ticket["ticket_id"]:
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "Guard ticket identity does not match.",
        )
    if ticket["ticket_type"] != "human_assistance":
        raise TicketDomainValidationError(
            INVALID_TICKET_RECORD,
            "A human-session guard requires a human-assistance ticket.",
        )
    return validated
