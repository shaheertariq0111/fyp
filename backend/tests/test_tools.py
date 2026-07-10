from types import SimpleNamespace

from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.models.tool_responses import ToolResponse


class MenuStub:
    def search_menu(self, **kwargs):
        return ToolResponse.ok(data=kwargs, user_message="ok")

    def get_menu_item(self, item_id):
        return ToolResponse.ok(data={"item_id": item_id}, user_message="ok")


class SessionStub:
    def create_link(self, user_id, session_id, item_id):
        return ToolResponse.ok(data={"user_id": user_id, "session_id": session_id,
                                     "item_id": item_id}, user_message="ok")


def test_exactly_eleven_mvp_tools():
    assert len(tools.MVP_TOOLS) == 11


def test_menu_link_injects_trusted_context(monkeypatch):
    container = SimpleNamespace(menu=MenuStub(), menu_sessions=SessionStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.create_menu_session_link(item_id="dynamic-item")
    assert result["data"] == {
        "user_id": "trusted-user", "session_id": "trusted-session", "item_id": "dynamic-item"
    }


def test_tool_converts_service_exception_to_safe_error(monkeypatch):
    class BrokenMenu:
        def get_menu_item(self, _item_id):
            raise RuntimeError("internal table details")

    monkeypatch.setattr(tools, "get_services",
                        lambda: SimpleNamespace(menu=BrokenMenu()))
    result = tools.get_menu_item(item_id="item")
    assert result["error_code"] == "BACKEND_UNAVAILABLE"
    assert "table" not in result["user_message"]
