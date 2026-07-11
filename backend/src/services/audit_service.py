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

    def admin_list_errors(self, limit: int = 50) -> dict:
        events = [
            event for event in self.repository.list_all()
            if "error" in str(event.get("event_type", "")).casefold()
            or "failed" in str(event.get("event_type", "")).casefold()
            or str(event.get("details_redacted", {}).get("error_code", "")).strip()
        ]
        events.sort(key=lambda event: event.get("created_at", ""), reverse=True)
        return {"events": events[:limit], "next_cursor": None}
