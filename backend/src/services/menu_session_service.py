import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from src.models.tool_responses import ToolResponse


class MenuSessionService:
    def __init__(self, repository, settings):
        self.repository = repository
        self.settings = settings

    @staticmethod
    def _hash(token: str, secret: str) -> str:
        return hashlib.sha256(f"{secret}:{token}".encode()).hexdigest()

    def create_link(self, user_id: str, agent_session_id: str,
                    item_id: str | None = None) -> ToolResponse:
        token = secrets.token_urlsafe(32)
        token_hash = self._hash(token, self.settings.session_token_secret)
        now = datetime.now(timezone.utc)
        session = {
            "PK": f"MENU_SESSION#{token_hash}", "SK": "METADATA",
            "session_token_hash": token_hash, "user_id": user_id,
            "agent_session_id": agent_session_id,
            "restaurant_id": self.settings.restaurant_id,
            "branch_id": self.settings.branch_id,
            "preselected_item_id": item_id, "status": "active",
            "expires_at": int((now + timedelta(minutes=self.settings.session_token_ttl_minutes)).timestamp()),
            "created_at": now.isoformat(),
        }
        self.repository.create(session)
        query = {"session_token": token}
        if item_id:
            query["item_id"] = item_id
        url = f"{str(self.settings.menu_site_base_url)}?{urlencode(query)}"
        return ToolResponse.ok(data={"url": url, "expires_at": session["expires_at"]},
                               user_message="Your secure menu link is ready.", next_action="open_menu")

    def resolve_token(self, session_token: str) -> ToolResponse:
        token_hash = self._hash(session_token, self.settings.session_token_secret)
        session = self.repository.get_by_token_hash(token_hash)
        if not session:
            return ToolResponse.error(error_code="MENU_SESSION_INVALID",
                                      user_message="This menu link is invalid or has expired.")
        public = {key: session.get(key) for key in
                  ("restaurant_id", "branch_id", "preselected_item_id", "agent_session_id", "user_id")}
        return ToolResponse.ok(data=public, user_message="Menu session resolved.")
