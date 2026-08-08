from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic_core import PydanticCustomError

from src.models.ticket import MAX_DESCRIPTION_LENGTH, normalize_ticket_timestamp
from src.repositories.agent_session_repository import SupportStateConflictError


SUPPORT_STATE_TTL = timedelta(minutes=30)
SUPPORT_STATE_MAX_CLOCK_SKEW = timedelta(seconds=5)
VERIFIED_ORDER_TTL = SUPPORT_STATE_TTL
WHATSAPP_ORDER_STATE_TTL = SUPPORT_STATE_TTL


logger = logging.getLogger(__name__)


class AgentSessionService:
    def __init__(
        self,
        repository,
        customer_service,
        settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.customers = customer_service
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def resolve(
        self,
        *,
        requested_session_id: str | None,
        customer_id: str | None,
        channel: str = "web",
        preserve_expired: bool = False,
        force_new: bool = False,
        allow_requested_session_creation: bool = False,
    ) -> dict:
        if force_new or not requested_session_id:
            existing = None
        elif customer_id:
            existing = self.repository.get_owned(customer_id, requested_session_id)
        else:
            # Identity-less legacy callers still need to recover the owning
            # customer from the session record.
            existing = self.repository.get(requested_session_id)
        effective_customer_id = customer_id or (existing or {}).get("customer_id")
        customer = self.customers.ensure_customer(effective_customer_id, channel)
        now = self._now()
        if existing and existing.get("customer_id") == customer["customer_id"]:
            expired = existing.get("expires_at", 0) <= int(now.timestamp())
            if not expired or preserve_expired:
                existing["status"] = "active"
                existing["last_seen_at"] = now.isoformat()
                existing["expires_at"] = self._expires_at(now)
                self.repository.save(existing)
                return {"session": existing, "customer": customer, "rotated": False}
        stable_session_id = (
            requested_session_id
            if allow_requested_session_creation and not force_new
            else None
        )
        session = self._new_session(
            customer["customer_id"],
            channel,
            now,
            session_id=stable_session_id,
        )
        self.repository.create(session)
        return {"session": session, "customer": customer, "rotated": True}

    def _new_session(
        self,
        customer_id: str,
        channel: str,
        now: datetime,
        *,
        session_id: str | None = None,
    ) -> dict:
        session_id = session_id or f"{channel}-{uuid.uuid4()}"
        return {
            "PK": f"CUSTOMER#{customer_id}",
            "SK": f"SESSION#{session_id}",
            "agent_session_id": session_id,
            "customer_id": customer_id,
            "channel": channel,
            "status": "active",
            "created_at": now.isoformat(),
            "last_seen_at": now.isoformat(),
            "expires_at": self._expires_at(now),
        }

    def _expires_at(self, now: datetime) -> int:
        return int((now + timedelta(hours=self.settings.agent_session_ttl_hours)).timestamp())

    def get_support_state(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        return self.repository.get_support_state(customer_id, agent_session_id)

    def save_verified_order_context(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        order_id: str,
        status: str,
    ) -> dict:
        if not isinstance(order_id, str) or not order_id.strip():
            raise ValueError("verified order ID is required")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("verified order status is required")
        verified_at = self._now().isoformat()
        self.repository.update_verified_order_context(
            customer_id,
            agent_session_id,
            order_id=order_id,
            status=status,
            verified_at=verified_at,
        )
        return {
            "verified_order_id": order_id,
            "verified_order_status": status,
            "verified_order_at": verified_at,
        }

    def save_whatsapp_order_state(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        offered_menu_items: list[dict],
        menu_query: str | None = None,
        shown_menu_item_ids: list[str] | None = None,
        menu_has_more: bool = False,
    ) -> dict:
        if not isinstance(offered_menu_items, list) or not offered_menu_items:
            raise ValueError("offered menu items are required")
        updated_at = self._now().isoformat()
        stored_shown_ids = shown_menu_item_ids or [
            str(item["product_id"])
            for item in offered_menu_items
            if item.get("product_id")
        ]
        self.repository.update_whatsapp_order_state(
            customer_id,
            agent_session_id,
            offered_menu_items=offered_menu_items,
            menu_query=menu_query,
            shown_menu_item_ids=stored_shown_ids,
            menu_has_more=menu_has_more,
            updated_at=updated_at,
        )
        return {
            "offered_menu_items": offered_menu_items,
            "whatsapp_menu_query": menu_query or "",
            "shown_menu_item_ids": stored_shown_ids,
            "whatsapp_menu_has_more": menu_has_more,
            "whatsapp_order_state_updated_at": updated_at,
        }

    def get_whatsapp_order_state(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        state = self.repository.get_whatsapp_order_state(
            customer_id,
            agent_session_id,
        )
        updated_at = state.get("whatsapp_order_state_updated_at")
        try:
            normalized = normalize_ticket_timestamp(updated_at)
        except (PydanticCustomError, TypeError, ValueError):
            normalized = None
        if normalized is not None and normalized == updated_at:
            timestamp = datetime.fromisoformat(normalized)
            now = self._now()
            if (
                timestamp <= now + SUPPORT_STATE_MAX_CLOCK_SKEW
                and now - timestamp < WHATSAPP_ORDER_STATE_TTL
                and isinstance(state.get("offered_menu_items"), list)
            ):
                return state
        if state:
            self.repository.clear_whatsapp_order_state(customer_id, agent_session_id)
        return {}

    def clear_whatsapp_order_state(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> None:
        self.repository.clear_whatsapp_order_state(customer_id, agent_session_id)

    def get_active_verified_order_context(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        for attempt in range(2):
            context = self.repository.get_verified_order_context(
                customer_id,
                agent_session_id,
            )
            if not context:
                self._log_verified_order_rejection(
                    customer_id,
                    agent_session_id,
                    "missing",
                )
                return {}
            reason = self._verified_order_rejection_reason(context)
            if reason is None:
                return context
            self._log_verified_order_rejection(
                customer_id,
                agent_session_id,
                reason,
            )
            try:
                self.repository.clear_verified_order_context(
                    customer_id,
                    agent_session_id,
                    expected_verified_at=context.get("verified_order_at"),
                )
                return {}
            except SupportStateConflictError:
                if attempt:
                    raise
        return {}

    def clear_verified_order_context(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        expected_verified_at: str,
    ) -> None:
        self.repository.clear_verified_order_context(
            customer_id,
            agent_session_id,
            expected_verified_at=expected_verified_at,
        )

    def save_support_state(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        expected_updated_at: str | None,
        intent: str,
        order_id: str | None,
        description: str | None,
    ) -> dict:
        updated_at = self._support_updated_at(expected_updated_at)
        self.repository.update_support_state(
            customer_id,
            agent_session_id,
            expected_updated_at,
            intent,
            order_id,
            description,
            updated_at,
        )
        state = {
            "pending_support_intent": intent,
            "pending_support_updated_at": updated_at,
        }
        if order_id is not None:
            state["pending_order_id"] = order_id
        if description is not None:
            state["pending_complaint_description"] = description
        return state

    def clear_support_state(
        self,
        customer_id: str,
        agent_session_id: str,
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        self.repository.clear_support_state(
            customer_id,
            agent_session_id,
            expected_updated_at=expected_updated_at,
        )

    def get_active_support_state(
        self,
        customer_id: str,
        agent_session_id: str,
    ) -> dict:
        for attempt in range(2):
            state = self.get_support_state(customer_id, agent_session_id)
            if not state:
                return {}
            now = self._now()
            if self._valid_support_state(state):
                updated_at = datetime.fromisoformat(
                    state["pending_support_updated_at"]
                )
                if (
                    updated_at <= now + SUPPORT_STATE_MAX_CLOCK_SKEW
                    and now - updated_at < SUPPORT_STATE_TTL
                ):
                    return state
            try:
                self.clear_support_state(
                    customer_id,
                    agent_session_id,
                    expected_updated_at=state.get(
                        "pending_support_updated_at"
                    ),
                )
                return {}
            except SupportStateConflictError:
                if attempt:
                    raise
        return {}

    def _support_updated_at(self, expected_updated_at: str | None) -> str:
        now = self._now()
        if expected_updated_at is not None:
            try:
                previous = datetime.fromisoformat(
                    normalize_ticket_timestamp(expected_updated_at)
                )
            except (PydanticCustomError, TypeError, ValueError):
                previous = None
            if previous is not None and now <= previous:
                now = previous + timedelta(microseconds=1)
        return now.isoformat()

    def _verified_order_rejection_reason(self, context: dict) -> str | None:
        order_id = context.get("verified_order_id")
        status = context.get("verified_order_status")
        verified_at = context.get("verified_order_at")
        if (
            not isinstance(order_id, str)
            or not order_id.strip()
            or not isinstance(status, str)
            or not status.strip()
        ):
            return "malformed"
        try:
            normalized = normalize_ticket_timestamp(verified_at)
        except (PydanticCustomError, TypeError, ValueError):
            return "malformed"
        if normalized != verified_at:
            return "malformed"
        timestamp = datetime.fromisoformat(normalized)
        now = self._now()
        if timestamp > now + SUPPORT_STATE_MAX_CLOCK_SKEW:
            return "future_timestamp"
        if now - timestamp >= VERIFIED_ORDER_TTL:
            return "expired"
        return None

    @staticmethod
    def _log_verified_order_rejection(
        customer_id: str,
        agent_session_id: str,
        reason: str,
    ) -> None:
        logger.info(
            "Verified order context was not used",
            extra={
                "event": "verified_order_context_rejected",
                "actor_id": customer_id,
                "agent_session_id": agent_session_id,
                "verified_order_rejection_reason": reason,
            },
        )

    @staticmethod
    def _valid_support_state(state: dict) -> bool:
        if state.get("pending_support_intent") != "order_complaint":
            return False
        updated_at = state.get("pending_support_updated_at")
        try:
            normalized = normalize_ticket_timestamp(updated_at)
        except (PydanticCustomError, TypeError, ValueError):
            return False
        if normalized != updated_at:
            return False
        order_id = state.get("pending_order_id")
        if order_id is not None and (
            not isinstance(order_id, str) or not order_id.strip()
        ):
            return False
        description = state.get("pending_complaint_description")
        if description is not None and (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > MAX_DESCRIPTION_LENGTH
        ):
            return False
        return True
