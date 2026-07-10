import uuid
from datetime import datetime, timezone


class AuditService:
    REDACTED_FIELDS = {"value", "delivery_address", "session_token", "secret"}

    def __init__(self, repository):
        self.repository = repository

    def record(self, session_id: str, user_id: str, event_type: str,
               details: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        redacted = {key: ("[REDACTED]" if key in self.REDACTED_FIELDS else value)
                    for key, value in details.items()}
        self.repository.append({
            "PK": session_id,
            "SK": f"AUDIT#{now}#{uuid.uuid4()}",
            "session_id": session_id,
            "user_id": user_id,
            "event_type": event_type,
            "details_redacted": redacted,
            "created_at": now,
        })
