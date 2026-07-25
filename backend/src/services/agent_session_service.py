from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic_core import PydanticCustomError

from src.models.ticket import MAX_DESCRIPTION_LENGTH, normalize_ticket_timestamp
from src.repositories.agent_session_repository import SupportStateConflictError


SUPPORT_STATE_TTL = timedelta(minutes=30)
SUPPORT_STATE_MAX_CLOCK_SKEW = timedelta(seconds=5)


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
    ) -> dict:
        existing = None if force_new or not requested_session_id else self.repository.get(requested_session_id)
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
        session = self._new_session(customer["customer_id"], channel, now)
        self.repository.create(session)
        return {"session": session, "customer": customer, "rotated": True}

    def _new_session(self, customer_id: str, channel: str, now: datetime) -> dict:
        session_id = f"{channel}-{uuid.uuid4()}"
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
