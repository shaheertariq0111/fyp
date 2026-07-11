from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api import main
from src.infrastructure.config import Settings
from src.models.tool_responses import ToolResponse
from test_config import BASE


class IdentityServices:
    def __init__(self, *, session_id="session", customer_id="user", rotated=False):
        self.session_id = session_id
        self.customer_id = customer_id
        self.rotated = rotated
        self.carts = SimpleNamespace(
            get_active_cart=lambda user_id, session_id: ToolResponse.ok(
                data={"cart": None}, user_message="cart"
            )
        )
        self.orders = SimpleNamespace(
            get_order_status=lambda user_id: ToolResponse.ok(data={"orders": []}, user_message="orders")
        )
        self.agent_sessions = SimpleNamespace(resolve=self.resolve)

    def resolve(self, **kwargs):
        return {
            "session": {
                "agent_session_id": self.session_id,
                "customer_id": self.customer_id,
                "channel": kwargs.get("channel", "web"),
                "expires_at": 123,
            },
            "customer": {
                "customer_id": self.customer_id,
                "display_name": None,
                "phone_e164": None,
                "phone_verified": False,
            },
            "rotated": self.rotated,
        }


def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def default_identity_services(monkeypatch):
    monkeypatch.setattr(main, "get_services", lambda: IdentityServices())


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
        "customer_id": "user",
        "customer_name": None,
        "customer_phone": None,
        "channel": "web",
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
    services = IdentityServices()
    services.carts = SimpleNamespace(
        get_active_cart=lambda user_id, session_id: ToolResponse.ok(
            data={"cart": {
                "cart_id": "CART-FROM-DB",
                "status": "item_ready",
                "items": [{"name": "Supreme Pizza", "quantity": 1}],
            }},
            user_message="cart",
        )
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
    services = IdentityServices()
    services.menu = SimpleNamespace(
        search_menu=lambda **kwargs: ToolResponse.ok(
            data={"items": [{"product_id": "item"}]},
            user_message="ok",
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
    assert response.json()["data"]["item_id"] == "item"
    assert response.json()["data"]["user_id"] == "user"
    assert response.json()["data"]["session_id"] == "session"
    assert response.json()["data"]["customer"]["customer_id"] == "user"


def test_chat_response_returns_canonical_customer_and_session(monkeypatch):
    monkeypatch.setattr(main, "get_services", lambda: IdentityServices(
        session_id="web-new", customer_id="cust-new", rotated=True
    ))
    monkeypatch.setattr(
        main,
        "invoke_restaurant_agent",
        lambda *args, **kwargs: SimpleNamespace(message={"content": [{"text": "hi"}]}),
    )

    response = client().post(
        "/api/chat",
        json={"message": "hi", "session_id": "old", "customer_id": "cust-new"},
    )

    payload = response.json()
    assert payload["session_id"] == "web-new"
    assert payload["customer_id"] == "cust-new"
    assert payload["state"]["session"]["rotated"] is True


def test_menu_orders_uses_backend_cart_service(monkeypatch):
    services = IdentityServices()
    services.carts = SimpleNamespace(
        get_active_cart=lambda user_id, session_id: ToolResponse.ok(
            data={"cart": None}, user_message="cart"
        ),
        create_pending_from_menu_order=lambda **kwargs: ToolResponse.ok(
            data={"order_id": "ORD-1", "items": kwargs["items"], "customer_id": kwargs["customer_id"]},
            user_message="pending",
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
    assert response.json()["data"]["customer_id"] == "user"


def admin_settings():
    return Settings(
        **BASE,
        admin_username="admin",
        admin_password="secret",
        admin_session_secret="admin-secret-at-least-sixteen",
    )


def login_admin(client, monkeypatch):
    monkeypatch.setattr(main, "get_settings", admin_settings)
    response = client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    assert response.status_code == 200


def test_admin_login_logout_and_auth_guard(monkeypatch):
    test_client = client()
    monkeypatch.setattr(main, "get_settings", admin_settings)

    guarded = test_client.get("/api/admin/me")
    login = test_client.post("/api/admin/login", json={"username": "admin", "password": "secret"})
    me = test_client.get("/api/admin/me")
    logout = test_client.post("/api/admin/logout")

    assert guarded.status_code == 401
    assert login.status_code == 200
    assert me.status_code == 200
    assert me.json()["admin"]["username"] == "admin"
    assert logout.status_code == 200


def test_admin_order_routes_use_admin_services(monkeypatch):
    test_client = client()
    login_admin(test_client, monkeypatch)
    services = IdentityServices()
    services.orders = SimpleNamespace(
        admin_analytics=lambda: {"today_orders": 1, "active_orders": 1, "revenue": 10,
                                 "failed_orders": 0, "by_status": {}, "recent_orders": []},
        admin_list_orders=lambda status=None, limit=50: {"orders": [{"order_id": "ORD-1", "status": status or "accepted"}]},
        admin_get_order=lambda order_id: {"order": {"order_id": order_id, "allowed_actions": ["start_preparing"]}},
        admin_update_status=lambda order_id, action, reason=None: {"order": {
            "order_id": order_id, "status": "preparing",
            "status_history": [{"action": action, "reason": reason}],
        }},
    )
    monkeypatch.setattr(main, "get_services", lambda: services)

    listed = test_client.get("/api/admin/orders?status=accepted")
    updated = test_client.patch(
        "/api/admin/orders/ORD-1/status",
        json={"action": "start_preparing", "reason": "Started"},
    )

    assert listed.status_code == 200
    assert listed.json()["orders"][0]["status"] == "accepted"
    assert updated.status_code == 200
    assert updated.json()["order"]["status_history"][0]["reason"] == "Started"


def test_admin_menu_customer_and_monitoring_routes(monkeypatch):
    test_client = client()
    login_admin(test_client, monkeypatch)
    services = IdentityServices()
    services.menu = SimpleNamespace(
        admin_list_entities=lambda entity_type: {"items": [{"entity_type": entity_type}]},
        admin_get_entity=lambda entity_type, entity_id: {"item": {"id": entity_id}},
        admin_save_menu_item=lambda payload, existing_id=None: {"item": payload},
        admin_set_item_availability=lambda item_id, available: {"item": {"product_id": item_id, "available": available}},
        admin_archive_item=lambda item_id: {"item": {"product_id": item_id, "available": False, "archived": True}},
    )
    services.customers = SimpleNamespace(
        admin_search=lambda query, limit: {"customers": [{"customer_id": "cust-1"}]},
        admin_get=lambda customer_id, order_service: {"customer": {"customer_id": customer_id}, "orders": []},
    )
    services.audit = SimpleNamespace(admin_list_errors=lambda limit: {"events": [{"event_type": "tool_error"}]})
    services.orders.admin_failed_orders = lambda limit: {"orders": [{"status": "failed"}]}
    monkeypatch.setattr(main, "get_services", lambda: services)

    created = test_client.post("/api/admin/menu/items", json={
        "product_id": "item",
        "name": "Item",
        "category": "pizza",
        "currency": "CUR",
        "starting_price": 10,
    })
    archived = test_client.patch("/api/admin/menu/items/item/archive")
    customers = test_client.get("/api/admin/customers?query=ava")
    errors = test_client.get("/api/admin/monitoring/errors")

    assert created.status_code == 200
    assert archived.json()["item"]["archived"] is True
    assert customers.json()["customers"][0]["customer_id"] == "cust-1"
    assert errors.json()["events"][0]["event_type"] == "tool_error"
