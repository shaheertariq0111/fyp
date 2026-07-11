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


class CartStub:
    def get_active_cart(self, user_id, session_id):
        return ToolResponse.ok(
            data={"cart": {"user_id": user_id, "session_id": session_id}},
            user_message="cart",
        )


def test_mvp_tools_include_active_cart_lookup():
    assert len(tools.MVP_TOOLS) == 12
    assert tools.get_active_cart in tools.MVP_TOOLS


def test_menu_link_injects_trusted_context(monkeypatch):
    container = SimpleNamespace(menu=MenuStub(), menu_sessions=SessionStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.create_menu_session_link(item_id="dynamic-item")
    assert result["data"] == {
        "user_id": "trusted-user", "session_id": "trusted-session", "item_id": "dynamic-item"
    }


def test_search_menu_tool_caps_chat_results_to_five(monkeypatch):
    container = SimpleNamespace(menu=MenuStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    result = tools.search_menu(query="recommend", max_results=12)

    assert result["data"]["limit"] == 5


def test_get_active_cart_uses_trusted_user_and_session(monkeypatch):
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.get_active_cart()
    assert result["data"]["cart"] == {
        "user_id": "trusted-user",
        "session_id": "trusted-session",
    }


def test_tool_converts_service_exception_to_safe_error_and_logs(monkeypatch, caplog):
    class BrokenMenu:
        def get_menu_item(self, _item_id):
            raise RuntimeError("internal table details")

    monkeypatch.setattr(tools, "get_services",
                        lambda: SimpleNamespace(menu=BrokenMenu()))
    with caplog.at_level("ERROR", logger="src.agent.tools"):
        with request_context(AgentRequestContext("trusted-user", "trusted-session")):
            result = tools.get_menu_item(item_id="item")
    assert result["error_code"] == "BACKEND_UNAVAILABLE"
    assert "table" not in result["user_message"]
    assert "get_menu_item" in caplog.text
    assert "RuntimeError" in caplog.text
