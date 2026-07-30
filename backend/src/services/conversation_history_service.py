from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


CONVERSATION_HISTORY_TTL_DAYS = 90


class ConversationHistoryService:
    def __init__(self, repository, *, ttl_days: int = CONVERSATION_HISTORY_TTL_DAYS):
        self.repository = repository
        self.ttl_days = ttl_days

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def store_inbound_whatsapp_message(
        self,
        *,
        conversation_id: str,
        customer_id: str,
        message_text: str,
        inbound_message_id: str | None,
        request_id: str | None = None,
        customer_number: str | None = None,
        duplicate: bool = False,
    ) -> dict[str, Any]:
        now = self._now()
        item = self._base_message(
            conversation_id=conversation_id,
            customer_id=customer_id,
            direction="inbound",
            sender_type="customer",
            message_text=message_text,
            timestamp_utc=now.isoformat(),
            message_identifier=inbound_message_id or f"inbound-{uuid.uuid4()}",
            request_id=request_id,
        )
        item["inbound_whatsapp_message_id"] = inbound_message_id
        item["duplicate"] = duplicate
        masked_phone = self._mask_phone(customer_number)
        if masked_phone is not None:
            item["masked_customer_phone"] = masked_phone
        item["expires_at"] = self._expires_at(now)
        self.repository.save(item)
        return item

    def store_outbound_whatsapp_message(
        self,
        *,
        conversation_id: str,
        customer_id: str,
        message_text: str,
        request_id: str | None,
        inbound_message_id: str | None = None,
        outbound: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        outbound_data = outbound or {}
        provider_message_id = self._string_value(
            outbound_data.get("providerMessageId")
        )
        outbound_status = self._outbound_status(outbound_data)
        item = self._base_message(
            conversation_id=conversation_id,
            customer_id=customer_id,
            direction="outbound",
            sender_type="agent",
            message_text=message_text,
            timestamp_utc=now.isoformat(),
            message_identifier=(
                provider_message_id or request_id or f"outbound-{uuid.uuid4()}"
            ),
            request_id=request_id,
        )
        item["inbound_whatsapp_message_id"] = inbound_message_id
        item["outbound_provider_message_id"] = provider_message_id
        item["outbound_status"] = outbound_status
        item["delivery_status"] = self._delivery_status(outbound_data)
        item["duplicate"] = False
        item["expires_at"] = self._expires_at(now)
        self.repository.save(item)
        return item

    def _base_message(
        self,
        *,
        conversation_id: str,
        customer_id: str,
        direction: str,
        sender_type: str,
        message_text: str,
        timestamp_utc: str,
        message_identifier: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        safe_identifier = self._safe_identifier(message_identifier)
        return {
            "PK": f"CONVERSATION#{conversation_id}",
            "SK": f"MSG#{timestamp_utc}#{direction}#{safe_identifier}",
            "GSI1PK": "CHANNEL#whatsapp",
            "GSI1SK": f"{timestamp_utc}#{conversation_id}",
            "record_type": "conversation_message",
            "conversation_id": conversation_id,
            "session_id": conversation_id,
            "customer_id": customer_id,
            "channel": "whatsapp",
            "direction": direction,
            "sender_type": sender_type,
            "message_text": message_text,
            "timestamp_utc": timestamp_utc,
            "request_id": request_id,
        }

    def _expires_at(self, now: datetime) -> int:
        return int((now + timedelta(days=self.ttl_days)).timestamp())

    @staticmethod
    def _string_value(value: Any) -> str | None:
        return value if isinstance(value, str) and value.strip() else None

    @classmethod
    def _outbound_status(cls, outbound: dict[str, Any]) -> str:
        status = cls._string_value(outbound.get("status"))
        if status is not None:
            return status
        if outbound.get("sent") is True:
            return "sent"
        if outbound.get("skipped") is True:
            return "skipped"
        return "failed"

    @classmethod
    def _delivery_status(cls, outbound: dict[str, Any]) -> str | None:
        delivery_status = cls._string_value(outbound.get("delivery_status"))
        return delivery_status or cls._string_value(outbound.get("status"))

    @staticmethod
    def _safe_identifier(identifier: str) -> str:
        value = identifier.strip() if isinstance(identifier, str) else ""
        if not value:
            value = f"message-{uuid.uuid4()}"
        return re.sub(r"[^A-Za-z0-9_.:-]", "_", value)[:160]

    @staticmethod
    def _mask_phone(phone: str | None) -> str | None:
        if not isinstance(phone, str):
            return None
        digits = re.sub(r"\D", "", phone)
        if not digits:
            return None
        return f"****{digits[-4:]}"
