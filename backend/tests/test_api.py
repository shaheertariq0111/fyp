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

