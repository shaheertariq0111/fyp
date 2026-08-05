from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


VOICE_QUEUE_KIND = "agentflo_whatsapp_voice"
VOICE_QUEUE_VERSION = 1
VOICE_QUEUE_MAX_BYTES = 512
VOICE_JOB_ID_PATTERN = re.compile(r"^wv1_[0-9a-f]{64}$")
VOICE_AUDIO_ID_MAX_LENGTH = 512


class VoiceJobState(str, Enum):
    PENDING_ENQUEUE = "pending_enqueue"
    QUEUED = "queued"
    PROCESSING = "processing"
    TRANSCRIBED = "transcribed"
    AGENT_INVOKING = "agent_invoking"
    RESPONSE_READY = "response_ready"
    OUTBOUND_SENDING = "outbound_sending"
    COMPLETED = "completed"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    MANUAL_REVIEW = "manual_review"


TERMINAL_VOICE_JOB_STATES = frozenset({
    VoiceJobState.COMPLETED.value,
    VoiceJobState.MANUAL_REVIEW.value,
})


def voice_job_id(provider_message_id: str) -> str:
    value = provider_message_id.strip()
    if not value:
        raise ValueError("VOICE_PROVIDER_MESSAGE_ID_REQUIRED")
    return "wv1_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_voice_job_id(value: str) -> str:
    if not VOICE_JOB_ID_PATTERN.fullmatch(value):
        raise ValueError("VOICE_JOB_ID_INVALID")
    return value


def validate_voice_audio_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("VOICE_AUDIO_ID_INVALID")
    normalized = value.strip()
    if not normalized or len(normalized) > VOICE_AUDIO_ID_MAX_LENGTH:
        raise ValueError("VOICE_AUDIO_ID_INVALID")
    return normalized


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("VOICE_QUEUE_DUPLICATE_KEY")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class VoiceQueueMessage:
    job_id: str
    v: int = VOICE_QUEUE_VERSION
    kind: str = VOICE_QUEUE_KIND

    def __post_init__(self) -> None:
        if self.v != VOICE_QUEUE_VERSION or self.kind != VOICE_QUEUE_KIND:
            raise ValueError("VOICE_QUEUE_SCHEMA_INVALID")
        validate_voice_job_id(self.job_id)

    def serialize(self) -> str:
        body = json.dumps(
            {"v": self.v, "kind": self.kind, "job_id": self.job_id},
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(body.encode("utf-8")) > VOICE_QUEUE_MAX_BYTES:
            raise ValueError("VOICE_QUEUE_BODY_TOO_LARGE")
        return body

    @classmethod
    def parse(cls, body: str | bytes) -> "VoiceQueueMessage":
        raw = body.encode("utf-8") if isinstance(body, str) else body
        if len(raw) > VOICE_QUEUE_MAX_BYTES:
            raise ValueError("VOICE_QUEUE_BODY_TOO_LARGE")
        try:
            decoded = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("VOICE_QUEUE_JSON_INVALID") from exc
        if not isinstance(decoded, dict) or set(decoded) != {"v", "kind", "job_id"}:
            raise ValueError("VOICE_QUEUE_SCHEMA_INVALID")
        if type(decoded["v"]) is not int or not isinstance(decoded["kind"], str) or not isinstance(decoded["job_id"], str):
            raise ValueError("VOICE_QUEUE_SCHEMA_INVALID")
        return cls(v=decoded["v"], kind=decoded["kind"], job_id=decoded["job_id"])


def validate_voice_job_record(
    record: dict[str, Any],
    *,
    require_audio_id: bool = True,
) -> None:
    required = {
        "PK", "SK", "job_id", "state", "version", "media_url",
        "customer_number", "sender_id", "conversation_identity_hash",
        "attempt_count", "enqueue_attempt_count", "created_at", "updated_at",
        "expires_at",
    }
    if require_audio_id:
        required.add("audio_id")
    if not required.issubset(record):
        raise ValueError("VOICE_JOB_RECORD_INVALID")
    job_id = validate_voice_job_id(str(record["job_id"]))
    if record["PK"] != f"JOB#{job_id}" or record["SK"] != "METADATA":
        raise ValueError("VOICE_JOB_RECORD_INVALID")
    if record["state"] not in {state.value for state in VoiceJobState}:
        raise ValueError("VOICE_JOB_STATE_INVALID")
    if type(record["version"]) is not int or record["version"] < 1:
        raise ValueError("VOICE_JOB_VERSION_INVALID")
    if require_audio_id:
        validate_voice_audio_id(record["audio_id"])
    if not isinstance(record["media_url"], str):
        raise ValueError("VOICE_MEDIA_URL_INVALID")
