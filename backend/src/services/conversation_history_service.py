from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


CONVERSATION_HISTORY_TTL_DAYS = 90
DEFAULT_ADMIN_CONVERSATION_LIMIT = 50
ADMIN_CONVERSATION_SCAN_LIMIT = 1000
MAX_ADMIN_MESSAGE_PREVIEW_LENGTH = 120


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
        idempotency_identifier: str | None = None,
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
            idempotency_identifier=idempotency_identifier,
        )
        item["inbound_whatsapp_message_id"] = inbound_message_id
        item["duplicate"] = duplicate
        masked_phone = self._mask_phone(customer_number)
        if masked_phone is not None:
            item["masked_customer_phone"] = masked_phone
        item["expires_at"] = self._expires_at(now)
        if idempotency_identifier is not None:
            self.repository.save_if_absent(item)
        else:
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
        idempotency_identifier: str | None = None,
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
            idempotency_identifier=idempotency_identifier,
        )
        item["inbound_whatsapp_message_id"] = inbound_message_id
        item["outbound_provider_message_id"] = provider_message_id
        item["outbound_status"] = outbound_status
        item["delivery_status"] = self._delivery_status(outbound_data)
        item["duplicate"] = False
        item["expires_at"] = self._expires_at(now)
        if idempotency_identifier is not None:
            self.repository.save_if_absent(item)
        else:
            self.repository.save(item)
        return item

    def admin_list_conversations(
        self,
        *,
        limit: int = DEFAULT_ADMIN_CONVERSATION_LIMIT,
    ) -> dict[str, Any]:
        recent_messages = self.repository.list_recent_whatsapp_messages(
            limit=ADMIN_CONVERSATION_SCAN_LIMIT,
        )
        conversation_ids: list[str] = []
        seen: set[str] = set()
        for message in recent_messages:
            conversation_id = self._string_value(message.get("conversation_id"))
            if conversation_id is None or conversation_id in seen:
                continue
            seen.add(conversation_id)
            conversation_ids.append(conversation_id)
            if len(conversation_ids) >= limit:
                break

        conversations = []
        for conversation_id in conversation_ids:
            messages = self.repository.list_for_conversation(conversation_id)
            if not messages:
                continue
            conversations.append(self._conversation_summary(messages))
        conversations.sort(
            key=lambda item: item["latest_timestamp_utc"],
            reverse=True,
        )
        return {"conversations": conversations[:limit]}

    def admin_list_messages(
        self,
        conversation_id: str,
    ) -> dict[str, Any]:
        messages = self.repository.list_for_conversation(conversation_id)
        projected = [self._project_admin_message(message) for message in messages]
        projected.sort(key=lambda item: item["timestamp_utc"])
        return {
            "conversation_id": conversation_id,
            "channel": "whatsapp",
            "messages": projected,
        }

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
        idempotency_identifier: str | None = None,
    ) -> dict[str, Any]:
        safe_identifier = self._safe_identifier(message_identifier)
        return {
            "PK": f"CONVERSATION#{conversation_id}",
            "SK": (
                f"MSG#IDEMPOTENT#{direction}#{self._safe_identifier(idempotency_identifier)}"
                if idempotency_identifier is not None
                else f"MSG#{timestamp_utc}#{direction}#{safe_identifier}"
            ),
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

    def _conversation_summary(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(messages, key=lambda item: self._timestamp(item))
        latest = ordered[-1]
        customer_messages = [
            message
            for message in ordered
            if message.get("sender_type") == "customer"
            or message.get("direction") == "inbound"
        ]
        agent_messages = [
            message
            for message in ordered
            if message.get("sender_type") == "agent"
            or message.get("direction") == "outbound"
        ]
        delivery_status = None
        masked_phone = None
        for message in reversed(ordered):
            if delivery_status is None:
                delivery_status = self._string_value(message.get("delivery_status"))
            if masked_phone is None:
                masked_phone = self._string_value(
                    message.get("masked_customer_phone")
                )
            if delivery_status is not None and masked_phone is not None:
                break
        return {
            "conversation_id": latest.get("conversation_id"),
            "channel": latest.get("channel") or "whatsapp",
            "latest_message_preview": self._preview(latest.get("message_text")),
            "latest_timestamp_utc": self._timestamp(latest),
            "message_count": len(ordered),
            "customer_message_count": len(customer_messages),
            "agent_message_count": len(agent_messages),
            "masked_customer_phone": masked_phone,
            "latest_delivery_status": delivery_status,
        }

    def _project_admin_message(self, message: dict[str, Any]) -> dict[str, Any]:
        projected = {
            "timestamp_utc": self._timestamp(message),
            "direction": message.get("direction"),
            "sender_type": message.get("sender_type"),
            "message_text": self._redact_message_text(message.get("message_text")),
            "outbound_status": self._string_value(message.get("outbound_status")),
            "delivery_status": self._string_value(message.get("delivery_status")),
        }
        masked_phone = self._string_value(message.get("masked_customer_phone"))
        if masked_phone is not None:
            projected["masked_customer_phone"] = masked_phone
        return projected

    def _preview(self, message_text: Any) -> str:
        redacted = self._redact_message_text(message_text)
        if len(redacted) <= MAX_ADMIN_MESSAGE_PREVIEW_LENGTH:
            return redacted
        return f"{redacted[:MAX_ADMIN_MESSAGE_PREVIEW_LENGTH - 1].rstrip()}..."

    @classmethod
    def _redact_message_text(cls, value: Any) -> str:
        text = value if isinstance(value, str) else ""
        text = re.sub(
            r"(?i)(session_token\s*=\s*)[^\s&]+",
            r"\1[REDACTED]",
            text,
        )
        text = re.sub(
            r"(?i)([\"']session_token[\"']\s*:\s*[\"'])[^\"']+([\"'])",
            r"\1[REDACTED]\2",
            text,
        )
        return re.sub(
            r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)",
            cls._redact_phone_match,
            text,
        )

    @staticmethod
    def _redact_phone_match(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 8:
            return match.group(0)
        return "[REDACTED_PHONE]"

    @staticmethod
    def _timestamp(message: dict[str, Any]) -> str:
        timestamp = message.get("timestamp_utc")
        return timestamp if isinstance(timestamp, str) else ""

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
