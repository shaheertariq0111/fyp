from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.api.whatsapp import WhatsAppInboundAudioMessage
from src.models.whatsapp_voice_job import (
    TERMINAL_VOICE_JOB_STATES,
    VoiceJobState,
    validate_voice_job_record,
    voice_job_id,
)
from src.repositories.whatsapp_voice_job_repository import VoiceJobConditionFailed


LEGAL_TRANSITIONS = {
    VoiceJobState.PENDING_ENQUEUE.value: {VoiceJobState.QUEUED.value, VoiceJobState.RETRYABLE_FAILURE.value},
    VoiceJobState.QUEUED.value: {VoiceJobState.PROCESSING.value, VoiceJobState.RETRYABLE_FAILURE.value, VoiceJobState.PERMANENT_FAILURE.value},
    VoiceJobState.PROCESSING.value: {VoiceJobState.TRANSCRIBED.value, VoiceJobState.RETRYABLE_FAILURE.value, VoiceJobState.PERMANENT_FAILURE.value},
    VoiceJobState.TRANSCRIBED.value: {VoiceJobState.AGENT_INVOKING.value, VoiceJobState.RETRYABLE_FAILURE.value},
    VoiceJobState.AGENT_INVOKING.value: {VoiceJobState.RESPONSE_READY.value, VoiceJobState.MANUAL_REVIEW.value},
    VoiceJobState.RESPONSE_READY.value: {VoiceJobState.OUTBOUND_SENDING.value},
    VoiceJobState.OUTBOUND_SENDING.value: {VoiceJobState.COMPLETED.value, VoiceJobState.RESPONSE_READY.value, VoiceJobState.PERMANENT_FAILURE.value, VoiceJobState.MANUAL_REVIEW.value},
    VoiceJobState.RETRYABLE_FAILURE.value: {VoiceJobState.QUEUED.value, VoiceJobState.PROCESSING.value, VoiceJobState.PERMANENT_FAILURE.value},
    VoiceJobState.PERMANENT_FAILURE.value: {VoiceJobState.RESPONSE_READY.value},
}


@dataclass(frozen=True, slots=True)
class VoiceJobSubmission:
    job_id: str
    accepted: bool
    queued: bool
    duplicate: bool


class WhatsAppVoiceJobService:
    def __init__(self, repository, queue_service, settings, *, logger: logging.Logger | None = None):
        self.repository = repository
        self.queue_service = queue_service
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _identity_hash(customer_number: str, sender_id: str) -> str:
        normalized = f"{customer_number.strip()}\x00{sender_id.strip()}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def submit_audio(self, message: WhatsAppInboundAudioMessage) -> VoiceJobSubmission:
        if not message.message_id or not message.media_url or not message.customer_number or not message.sender_id:
            raise ValueError("VOICE_AUDIO_FIELDS_REQUIRED")
        job_id = voice_job_id(message.message_id)
        now = self._now()
        record = {
            "PK": f"JOB#{job_id}", "SK": "METADATA", "job_id": job_id,
            "state": VoiceJobState.PENDING_ENQUEUE.value, "version": 1,
            "media_url": message.media_url, "customer_number": message.customer_number,
            "sender_id": message.sender_id,
            "conversation_identity_hash": self._identity_hash(message.customer_number, message.sender_id),
            "attempt_count": 0, "enqueue_attempt_count": 0,
            "created_at": now.isoformat(), "updated_at": now.isoformat(),
            "expires_at": int((now + timedelta(hours=self.settings.voice_job_ttl_hours)).timestamp()),
            "GSI1PK": "VOICE_OUTBOX", "GSI1SK": int(now.timestamp()),
        }
        validate_voice_job_record(record)
        created = self.repository.create_if_absent(record)
        if not created:
            existing = self.repository.get(job_id)
            return VoiceJobSubmission(
                job_id=job_id, accepted=True,
                queued=bool(existing and existing.get("state") not in {
                    VoiceJobState.PENDING_ENQUEUE.value, VoiceJobState.RETRYABLE_FAILURE.value,
                }), duplicate=True,
            )
        self.logger.info("Agentflo audio accepted", extra={"event": "agentflo_whatsapp_audio_accepted", "voice_job_id": job_id})
        return self._enqueue(record, duplicate=False)

    def _enqueue(self, record: dict, *, duplicate: bool) -> VoiceJobSubmission:
        job_id = record["job_id"]
        try:
            self.queue_service.send(job_id)
            updated = self.transition(
                record, VoiceJobState.QUEUED.value,
                values={"enqueue_attempt_count": int(record.get("enqueue_attempt_count", 0)) + 1},
                remove=("next_retry_at", "generic_failure_code", "GSI1PK", "GSI1SK"),
            )
            self.logger.info("Voice queue submitted", extra={"event": "voice_queue_submitted", "voice_job_id": job_id, "voice_job_state": updated["state"]})
            return VoiceJobSubmission(job_id, True, True, duplicate)
        except VoiceJobConditionFailed:
            latest = self.repository.get(job_id)
            queued = bool(latest and latest.get("state") not in {VoiceJobState.PENDING_ENQUEUE.value, VoiceJobState.RETRYABLE_FAILURE.value})
            return VoiceJobSubmission(job_id, True, queued, True)
        except Exception:
            retry_at = int((self._now() + timedelta(seconds=30)).timestamp())
            try:
                self.transition(
                    record, VoiceJobState.RETRYABLE_FAILURE.value,
                    values={
                        "generic_failure_code": "VOICE_QUEUE_OPERATION_FAILED",
                        "next_retry_at": retry_at, "GSI1PK": "VOICE_OUTBOX", "GSI1SK": retry_at,
                        "enqueue_attempt_count": int(record.get("enqueue_attempt_count", 0)) + 1,
                    },
                )
            except VoiceJobConditionFailed:
                pass
            self.logger.warning("Voice enqueue deferred", extra={"event": "voice_retryable_failure", "voice_job_id": job_id, "failure_stage": "enqueue"})
            return VoiceJobSubmission(job_id, True, False, duplicate)

    def recover_outbox(self, *, limit: int = 25) -> int:
        now_epoch = int(self._now().timestamp())
        recovered = 0
        for record in self.repository.query_due(due_partition="VOICE_OUTBOX", now_epoch=now_epoch, limit=limit):
            if record.get("state") not in {VoiceJobState.PENDING_ENQUEUE.value, VoiceJobState.RETRYABLE_FAILURE.value}:
                continue
            if self._enqueue(record, duplicate=True).queued:
                recovered += 1
        return recovered

    def transition(self, record: dict, next_state: str, *, values: dict | None = None, remove: tuple[str, ...] = ()) -> dict:
        current = str(record["state"])
        if next_state not in LEGAL_TRANSITIONS.get(current, set()):
            raise ValueError("VOICE_JOB_TRANSITION_INVALID")
        return self.repository.transition(
            record["job_id"], expected_states={current}, expected_version=int(record["version"]),
            next_state=next_state, updated_at=self._now().isoformat(), values=values, remove=remove,
        )

    def acquire_lease(self, job_id: str, owner: str) -> dict:
        now = self._now()
        return self.repository.acquire_lease(
            job_id, owner=owner, now_epoch=int(now.timestamp()),
            lease_expires_at=int((now + timedelta(seconds=self.settings.voice_job_lease_seconds)).timestamp()),
            updated_at=now.isoformat(),
        )

    def extend_lease(self, job_id: str, owner: str) -> bool:
        now = self._now()
        return self.repository.extend_lease(
            job_id, owner=owner,
            lease_expires_at=int((now + timedelta(seconds=self.settings.voice_job_lease_seconds)).timestamp()),
            updated_at=now.isoformat(),
        )

    def release_lease(self, job_id: str, owner: str) -> bool:
        return self.repository.release_lease(job_id, owner=owner, updated_at=self._now().isoformat())

    @staticmethod
    def is_terminal(record: dict) -> bool:
        return record.get("state") in TERMINAL_VOICE_JOB_STATES
