from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.api import main
from src.models.tool_responses import ToolResponse


def client():
    return TestClient(main.app)


def test_health_route():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_route_invokes_agent_and_sanitizes(monkeypatch):
    monkeypatch.setattr(
        main,
        "invoke_restaurant_agent",
        lambda *args, **kwargs: SimpleNamespace(
            message={"content": [{"text": "<thinking>hidden</thinking>Hello!"}]}
        ),
    )

    response = client().post(
        "/api/chat",
        json={"message": "hi", "session_id": "session", "user_id": "user"},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "Hello!"
    assert response.json()["tool_calls"] == []
    assert response.json()["write_succeeded"] is False


def test_chat_route_delegates_cart_and_order_language_to_agent(monkeypatch):
    captured = {}

    def invoke_restaurant_agent(message, **kwargs):
        captured.update({"message": message, **kwargs})
        return SimpleNamespace(message={"content": [{"text": "Agent handled it."}]})

    monkeypatch.setattr(main, "invoke_restaurant_agent", invoke_restaurant_agent)

    response = client().post(
        "/api/chat",
        json={
            "message": "whats in my cart and can I place order",
            "session_id": "session",
            "user_id": "user",
            "branch_id": "branch",
        },
    )

    assert response.status_code == 200
    assert response.json()["text"] == "Agent handled it."
    assert captured == {
        "message": "whats in my cart and can I place order",
        "user_id": "user",
        "agent_session_id": "session",
        "branch_id": "branch",
    }


def test_chat_false_success_without_write_tool_keeps_text_but_reports_no_write(monkeypatch):
    monkeypatch.setattr(
        main,
        "invoke_restaurant_agent",
        lambda *args, **kwargs: SimpleNamespace(
            message={"content": [{"text": "I added Supreme Pizza to your order."}]},
            tool_calls=[],
        ),
    )

    response = client().post(
        "/api/chat",
        json={"message": "add supreme", "session_id": "session", "user_id": "user"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["write_succeeded"] is False
    assert payload["text"] == "I added Supreme Pizza to your order."
    assert payload["tool_calls"] == []


def test_chat_successful_write_tool_sets_metadata_and_state(monkeypatch):
    tool_result = {
        "success": True,
        "data": {"cart_id": "CART-1", "items": [{"name": "Supreme Pizza", "quantity": 1}]},
        "user_message": "The item was added to your cart.",
        "agent": {
            "entity": "cart",
            "cart_id": "CART-1",
            "cart_status": "item_ready",
            "cart_summary": {"items": [{"name": "Supreme Pizza", "quantity": 1}], "subtotal": 1000},
        },
    }
    monkeypatch.setattr(
        main,
        "invoke_restaurant_agent",
        lambda *args, **kwargs: SimpleNamespace(
            message={"content": [{"text": "I added Supreme Pizza to your order."}]},
            tool_calls=[{
                "tool_name": "start_cart_item_customization",
                "success": True,
                "is_write": True,
                "result": tool_result,
                "error_code": None,
            }],
        ),
    )
    services = SimpleNamespace(
        carts=SimpleNamespace(
            get_active_cart=lambda user_id, session_id: ToolResponse.ok(
                data={"cart": {
                    "cart_id": "CART-FROM-DB",
                    "status": "item_ready",
                    "items": [{"name": "Supreme Pizza", "quantity": 1}],
                }},
                user_message="cart",
            )
        ),
        orders=SimpleNamespace(
            get_order_status=lambda user_id: ToolResponse.ok(data={"orders": []}, user_message="orders")
        ),
    )
    monkeypatch.setattr(main, "get_services", lambda: services)

    response = client().post(
        "/api/chat",
        json={"message": "add supreme", "session_id": "session", "user_id": "user"},
    )

    payload = response.json()
    assert payload["text"] == "I added Supreme Pizza to your order."
    assert payload["write_succeeded"] is True
    assert payload["tool_calls"][0]["tool_name"] == "start_cart_item_customization"
    assert payload["tool_calls"][0]["success"] is True
    assert payload["tool_calls"][0]["is_write"] is True
    assert payload["state"]["cart"]["cart_id"] == "CART-FROM-DB"


def test_chat_failed_write_tool_reports_structured_error(monkeypatch):
    tool_result = {
        "success": False,
        "error_code": "INVALID_OPTION",
        "user_message": "Please choose one of the available options.",
    }
    monkeypatch.setattr(
        main,
        "invoke_restaurant_agent",
        lambda *args, **kwargs: SimpleNamespace(
            message={"content": [{"text": "I updated your order."}]},
            tool_calls=[{
                "tool_name": "save_customization_choice",
                "success": False,
                "is_write": True,
                "result": tool_result,
                "error_code": "INVALID_OPTION",
            }],
        ),
    )

    response = client().post(
        "/api/chat",
        json={"message": "wrong option", "session_id": "session", "user_id": "user"},
    )

    payload = response.json()
    assert payload["write_succeeded"] is False
    assert payload["text"] == "I updated your order."
    assert payload["tool_calls"][0]["error_code"] == "INVALID_OPTION"
    assert payload["tool_calls"][0]["result"]["user_message"] == "Please choose one of the available options."


def test_chat_informational_response_without_write_is_not_replaced(monkeypatch):
    monkeypatch.setattr(
        main,
        "invoke_restaurant_agent",
        lambda *args, **kwargs: SimpleNamespace(
            message={"content": [{"text": "Here are some spicy options."}]},
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {"success": True, "data": {"items": []}, "user_message": "ok"},
                "error_code": None,
            }],
        ),
    )

    response = client().post(
        "/api/chat",
        json={"message": "spicy", "session_id": "session", "user_id": "user"},
    )

    payload = response.json()
    assert payload["write_succeeded"] is False
    assert payload["text"] == "Here are some spicy options."


def test_chat_order_start_help_text_is_not_false_success(monkeypatch):
    text = "I can help you place an order. What would you like?"
    monkeypatch.setattr(
        main,
        "invoke_restaurant_agent",
        lambda *args, **kwargs: SimpleNamespace(
            message={"content": [{"text": text}]},
            tool_calls=[{
                "tool_name": "get_order_status",
                "success": True,
                "is_write": False,
                "result": {"success": True, "data": {"orders": []}, "user_message": "ok"},
                "error_code": None,
            }],
        ),
    )

    response = client().post(
        "/api/chat",
        json={"message": "i want to order", "session_id": "session", "user_id": "user"},
    )

    payload = response.json()
    assert payload["write_succeeded"] is False
    assert payload["text"] == text


def test_menu_route_uses_menu_service(monkeypatch):
    services = SimpleNamespace(
        menu=SimpleNamespace(
            search_menu=lambda **kwargs: ToolResponse.ok(
                data={"items": [{"product_id": "item"}]},
                user_message="ok",
            )
        )
    )
    monkeypatch.setattr(main, "get_services", lambda: services)

    response = client().get("/api/menu?query=chicken")

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["product_id"] == "item"


def test_action_route_injects_context_and_dispatches(monkeypatch):
    def fake_action(item_id):
        from src.agent.context import get_request_context

        context = get_request_context()
        return {
            "success": True,
            "data": {
                "item_id": item_id,
                "user_id": context.user_id,
                "session_id": context.agent_session_id,
            },
            "user_message": "ok",
        }

    monkeypatch.setitem(main.ACTION_HANDLERS, "fake_action", fake_action)

    response = client().post(
        "/api/actions",
        json={
            "action": "fake_action",
            "metadata": {"item_id": "item"},
            "session_id": "session",
            "user_id": "user",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "item_id": "item",
        "user_id": "user",
        "session_id": "session",
    }


def test_menu_orders_requires_session_identity():
    response = client().post(
        "/api/menu-orders",
        json={"items": [{"item_id": "item", "quantity": 1}]},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "SESSION_REQUIRED"


def test_menu_orders_uses_backend_cart_service(monkeypatch):
    services = SimpleNamespace(
        carts=SimpleNamespace(
            create_pending_from_menu_order=lambda **kwargs: ToolResponse.ok(
                data={"order_id": "ORD-1", "items": kwargs["items"]},
                user_message="pending",
            )
        )
    )
    monkeypatch.setattr(main, "get_services", lambda: services)

    response = client().post(
        "/api/menu-orders",
        json={
            "session_id": "session",
            "user_id": "user",
            "items": [{"item_id": "item", "quantity": 2}],
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["order_id"] == "ORD-1"
    assert response.json()["data"]["items"][0]["quantity"] == 2
