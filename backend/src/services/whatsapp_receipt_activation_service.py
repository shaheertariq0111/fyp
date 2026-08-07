from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from src.services.receipt_job_service import ReceiptJobServiceError


@dataclass(frozen=True, slots=True)
class WhatsAppReceiptActivationResult:
    status: str
    retryable: bool


class WhatsAppReceiptActivationService:
    def __init__(
        self,
        *,
        agent_requests,
        receipt_jobs,
        logger: logging.Logger | None = None,
    ) -> None:
        self.agent_requests = agent_requests
        self.receipt_jobs = receipt_jobs
        self.logger = logger or logging.getLogger(__name__)

    def activate_pending(
        self,
        *,
        message_id: str,
        marker: dict[str, Any],
        customer_number: str,
        sender_id: str,
    ) -> WhatsAppReceiptActivationResult:
        if not isinstance(marker, dict):
            return self._result("not_required", retryable=False)
        delivery_state = marker.get("delivery_state")
        activation_state = marker.get("receipt_activation_state")

        if delivery_state == "completed" and activation_state == "completed":
            return self._result("already_activated", retryable=False)
        if delivery_state == "completed" and activation_state == "manual_review":
            return self._result("manual_review", retryable=False)
        if delivery_state != "completed" or activation_state != "pending":
            return self._result("not_required", retryable=False)

        required = (
            marker.get("submitted_order_id"),
            marker.get("request_id"),
            marker.get("session_id"),
            customer_number,
            sender_id,
        )
        if not all(self._canonical_string(value) for value in required):
            self.agent_requests.mark_agentflo_whatsapp_receipt_manual_review(
                message_id
            )
            return self._result("manual_review", retryable=False)

        try:
            submission = self.receipt_jobs.submit(
                order_id=marker["submitted_order_id"],
                receipt_version=1,
                customer_number=customer_number,
                sender_id=sender_id,
                conversation_id=marker["session_id"],
                request_id=marker["request_id"],
            )
        except ReceiptJobServiceError as exc:
            if exc.retryable:
                return self._result("retryable_failure", retryable=True)
            self.agent_requests.mark_agentflo_whatsapp_receipt_manual_review(
                message_id
            )
            return self._result("manual_review", retryable=False)
        except (ClientError, BotoCoreError):
            return self._result("retryable_failure", retryable=True)
        except ValueError:
            self.agent_requests.mark_agentflo_whatsapp_receipt_manual_review(
                message_id
            )
            return self._result("manual_review", retryable=False)

        if submission.accepted is not True:
            return self._result("retryable_failure", retryable=True)
        try:
            completed = (
                self.agent_requests.complete_agentflo_whatsapp_receipt_activation(
                    message_id
                )
            )
        except (ClientError, BotoCoreError):
            return self._result("retryable_failure", retryable=True)
        if not completed:
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
    ) -> WhatsAppReceiptActivationResult:
        self.logger.info(
            "WhatsApp receipt activation result",
            extra={
                "event": "whatsapp_receipt_activation",
                "receipt_activation_status": status,
                "retryable": retryable,
                "duplicate": duplicate,
            },
        )
        return WhatsAppReceiptActivationResult(status, retryable)
