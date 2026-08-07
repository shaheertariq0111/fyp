from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from src.repositories.whatsapp_voice_job_repository import VoiceJobConditionFailed
from src.services.receipt_job_service import ReceiptJobServiceError


@dataclass(frozen=True, slots=True)
class WhatsAppVoiceReceiptActivationResult:
    status: str
    retryable: bool


class WhatsAppVoiceReceiptActivationService:
    def __init__(
        self,
        *,
        voice_jobs,
        receipt_jobs,
        logger: logging.Logger | None = None,
    ) -> None:
        self.voice_jobs = voice_jobs
        self.receipt_jobs = receipt_jobs
        self.logger = logger or logging.getLogger(__name__)

    def activate_pending(
        self,
        record: dict[str, Any],
    ) -> WhatsAppVoiceReceiptActivationResult:
        if not isinstance(record, dict) or record.get("state") != "outbound_sending":
            return self._result("not_required", retryable=False)
        activation_state = record.get("receipt_activation_state")
        if activation_state == "completed":
            return self._result("already_activated", retryable=False)
        if activation_state == "manual_review":
            return self._result("manual_review", retryable=False)
        if activation_state != "pending":
            return self._result("not_required", retryable=False)

        required = (
            record.get("submitted_order_id"),
            record.get("customer_number"),
            record.get("sender_id"),
            record.get("session_id"),
            record.get("request_id"),
        )
        if not all(self._canonical_string(value) for value in required):
            self.voice_jobs.mark_receipt_manual_review(record)
            return self._result("manual_review", retryable=False)

        try:
            submission = self.receipt_jobs.submit(
                order_id=record["submitted_order_id"],
                receipt_version=1,
                customer_number=record["customer_number"],
                sender_id=record["sender_id"],
                conversation_id=record["session_id"],
                request_id=record["request_id"],
            )
        except ReceiptJobServiceError as exc:
            if exc.retryable:
                return self._result("retryable_failure", retryable=True)
            self.voice_jobs.mark_receipt_manual_review(record)
            return self._result("manual_review", retryable=False)
        except (ClientError, BotoCoreError):
            return self._result("retryable_failure", retryable=True)
        except ValueError:
            self.voice_jobs.mark_receipt_manual_review(record)
            return self._result("manual_review", retryable=False)

        if submission.accepted is not True:
            return self._result("retryable_failure", retryable=True)
        try:
            self.voice_jobs.complete_receipt_activation(record)
        except (ClientError, BotoCoreError, VoiceJobConditionFailed):
            return self._result("retryable_failure", retryable=True)
        return self._result(
            "activated",
            retryable=False,
            duplicate=bool(submission.duplicate),
        )

    @staticmethod
    def _canonical_string(value: Any) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and value == value.strip()
        )

    def _result(
        self,
        status: str,
        *,
        retryable: bool,
        duplicate: bool = False,
    ) -> WhatsAppVoiceReceiptActivationResult:
        self.logger.info(
            "WhatsApp voice receipt activation result",
            extra={
                "event": "whatsapp_voice_receipt_activation",
                "voice_receipt_activation_status": status,
                "retryable": retryable,
                "duplicate": duplicate,
            },
        )
        return WhatsAppVoiceReceiptActivationResult(status, retryable)
