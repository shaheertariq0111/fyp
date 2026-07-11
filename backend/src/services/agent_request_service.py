from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


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
    ) -> dict[str, Any]:
        now = self._now()
        request_id = f"req-{uuid.uuid4()}"
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
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "expires_at": self._expires_at(now),
        }
        self.repository.create(request)
        return request

    def complete(self, request_id: str, response: dict[str, Any]) -> dict[str, Any]:
        request = self._get_required(request_id)
        now = self._now()
        request.update({
            "status": "completed",
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

    def get(self, request_id: str) -> dict[str, Any] | None:
        return self.repository.get(request_id)

    def _get_required(self, request_id: str) -> dict[str, Any]:
        request = self.repository.get(request_id)
        if not request:
            raise ValueError("AGENT_REQUEST_NOT_FOUND")
        return request

    def _expires_at(self, now: datetime) -> int:
        return int((now + timedelta(hours=self.settings.agent_request_ttl_hours)).timestamp())
