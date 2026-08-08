from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


AGENTFLO_WHATSAPP_IDEMPOTENCY_TTL_HOURS = 24


class AgentRequestService:
    def __init__(self, repository, settings):
        self.repository = repository
        self.settings = settings

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def start_processing(
        self,
        *,
        actor_id: str,
        session_id: str,
        message: str,
        channel: str,
        request_payload: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        request_id = request_id or f"req-{uuid.uuid4()}"
        request = {
            "PK": f"REQUEST#{request_id}",
            "SK": "METADATA",
            "request_id": request_id,
            "actor_id": actor_id,
            "session_id": session_id,
            "status": "processing",
            "message": message,
            "channel": channel,
            "request": request_payload,
            "invocation_state": "not_started",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": self._expires_at(now),
        }
        self.repository.create(request)
        return request

    def start_or_resume_processing(self, **kwargs) -> tuple[dict[str, Any], bool]:
        request_id = kwargs.get("request_id")
        if not request_id:
            return self.start_processing(**kwargs), True
        existing = self.get(request_id)
        if existing is not None:
            return existing, False
        try:
            return self.start_processing(**kwargs), True
        except Exception as exc:
            response = getattr(exc, "response", {})
            if response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            existing = self.get(request_id)
            if existing is None:
                raise
            return existing, False

    def claim_invocation(self, request_id: str) -> bool:
        return self.repository.transition_invocation_state(
            request_id, expected_state="not_started", next_state="invoking",
            updated_at=self._now().isoformat(),
        )

    def mark_invocation_ambiguous(self, request_id: str) -> bool:
        return self.repository.transition_invocation_state(
            request_id, expected_state="invoking", next_state="ambiguous",
            updated_at=self._now().isoformat(),
        )

    def complete(self, request_id: str, response: dict[str, Any]) -> dict[str, Any]:
        request = self._get_required(request_id)
        now = self._now()
        request.update({
            "status": "completed",
            "invocation_state": "completed",
            "response": response,
            "updated_at": now.isoformat(),
        })
        self.repository.save(request)
        return request

    def fail(self, request_id: str, *, error_code: str, message: str) -> dict[str, Any]:
        request = self._get_required(request_id)
        now = self._now()
        request.update({
            "status": "failed",
            "error_code": error_code,
            "failure_message": message,
            "updated_at": now.isoformat(),
        })
        self.repository.save(request)
        return request

    def fail_before_invocation(
        self,
        request_id: str,
        *,
        error_code: str,
        message: str,
    ) -> dict[str, Any]:
        request = self._get_required(request_id)
        if (
            request.get("status") != "processing"
            or request.get("invocation_state") != "invoking"
        ):
            raise ValueError("AGENT_REQUEST_PRE_INVOCATION_STATE_INVALID")
        now = self._now()
        request.update({
            "status": "failed",
            "invocation_state": "failed",
            "error_code": error_code,
            "failure_message": message,
            "updated_at": now.isoformat(),
        })
        self.repository.save(request)
        return request

    def get(self, request_id: str) -> dict[str, Any] | None:
        return self.repository.get(request_id)

    def claim_agentflo_whatsapp_message(self, message_id: str) -> bool:
        now = self._now()
        now_epoch = int(now.timestamp())
        marker = {
            "PK": f"agentflo-whatsapp-message:{message_id}",
            "SK": "IDEMPOTENCY",
            "record_type": "agentflo_whatsapp_message_idempotency",
            "delivery_state": "processing",
            "created_at": now.isoformat(),
            "expires_at": now_epoch
            + AGENTFLO_WHATSAPP_IDEMPOTENCY_TTL_HOURS * 60 * 60,
        }
        return self.repository.claim_idempotency_key(
            marker,
            now_epoch=now_epoch,
        )

    def get_agentflo_whatsapp_message(self, message_id: str) -> dict | None:
        return self.repository.get_idempotency_key(message_id)

    def cache_agentflo_whatsapp_response(
        self,
        message_id: str,
        *,
        request_id: str,
        session_id: str,
        customer_id: str,
        reply: str,
        submitted_order_id: str | None = None,
    ) -> None:
        if submitted_order_id is not None and (
            not isinstance(submitted_order_id, str)
            or not submitted_order_id
            or submitted_order_id != submitted_order_id.strip()
        ):
            raise ValueError("AGENTFLO_SUBMITTED_ORDER_ID_INVALID")
        marker = self.repository.get_idempotency_key(message_id)
        if marker is None:
            raise ValueError("AGENTFLO_WHATSAPP_MARKER_NOT_FOUND")
        marker.update({
            "delivery_state": "response_ready",
            "request_id": request_id,
            "session_id": session_id,
            "customer_id": customer_id,
            "reply": reply,
            "updated_at": self._now().isoformat(),
        })
        if submitted_order_id is not None:
            marker["submitted_order_id"] = submitted_order_id
        self.repository.save_idempotency_key(marker)

    def complete_agentflo_whatsapp_message(self, message_id: str) -> bool:
        return self.repository.transition_idempotency_delivery_state(
            message_id,
            expected_state="outbound_sending",
            next_state="completed",
            updated_at=self._now().isoformat(),
        )

    def complete_agentflo_whatsapp_with_receipt_pending(
        self,
        message_id: str,
    ) -> bool:
        return self.repository.complete_delivery_with_receipt_pending(
            message_id,
            updated_at=self._now().isoformat(),
        )

    def complete_agentflo_whatsapp_receipt_activation(
        self,
        message_id: str,
    ) -> bool:
        return self.repository.transition_receipt_activation_state(
            message_id,
            expected_state="pending",
            next_state="completed",
            updated_at=self._now().isoformat(),
        )

    def mark_agentflo_whatsapp_receipt_manual_review(
        self,
        message_id: str,
    ) -> bool:
        return self.repository.transition_receipt_activation_state(
            message_id,
            expected_state="pending",
            next_state="manual_review",
            updated_at=self._now().isoformat(),
        )

    def claim_agentflo_whatsapp_outbound(self, message_id: str) -> bool:
        return self.repository.transition_idempotency_delivery_state(
            message_id,
            expected_state="response_ready",
            next_state="outbound_sending",
            updated_at=self._now().isoformat(),
        )

    def retry_agentflo_whatsapp_outbound(self, message_id: str) -> bool:
        return self.repository.transition_idempotency_delivery_state(
            message_id,
            expected_state="outbound_sending",
            next_state="response_ready",
            updated_at=self._now().isoformat(),
        )

    def release_agentflo_whatsapp_message(self, message_id: str) -> None:
        self.repository.delete_idempotency_key(message_id)

    def _get_required(self, request_id: str) -> dict[str, Any]:
        request = self.repository.get(request_id)
        if not request:
            raise ValueError("AGENT_REQUEST_NOT_FOUND")
        return request

    def _expires_at(self, now: datetime) -> int:
        return int((now + timedelta(hours=self.settings.agent_request_ttl_hours)).timestamp())
