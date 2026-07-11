from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


class AgentSessionService:
    def __init__(self, repository, customer_service, settings):
        self.repository = repository
        self.customers = customer_service
        self.settings = settings

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

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
