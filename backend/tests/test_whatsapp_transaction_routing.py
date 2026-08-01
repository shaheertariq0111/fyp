from copy import deepcopy
from datetime import datetime, timezone

import pytest

from src.agent.order_intent import OrderIntentClassification
from src.models.tool_responses import ToolResponse
from src.services.agent_session_service import AgentSessionService
from src.services.order_service import OrderService
from src.services.support_flow_service import SupportFlowService as RealSupportFlow
from src.services.ticket_service import TicketService
from src.services.whatsapp_order_flow_service import WhatsAppOrderFlowService
from src.services.whatsapp_support_flow_service import (
    SUPPORT_BACKEND_FAILURE_MESSAGE,
    WhatsAppSupportFlowService,
)
from fakes import (
    MemoryAgentSessionRepository,
    MemoryMenuRepository,
    MemoryOrderRepository,
    MemoryTicketRepository,
)


class Menu:
    def __init__(self, items=None):
        self.items = items or []
        self.calls = []

    def search_menu(self, **kwargs):
        self.calls.append(kwargs)
        return ToolResponse.ok(
            data={"items": deepcopy(self.items)},
            user_message=(
                "I found current menu options."
                if self.items
                else "I couldn't find a matching available menu item."
            ),
            next_action="present_menu_results",
        )


class IntentClient:
    def __init__(self, action, confidence=0.95):
        self.result = OrderIntentClassification(
            action=action,
            confidence=confidence,
        )
        self.requests = []

    def classify_order_intent(self, request):
        self.requests.append(request)
        return self.result


class Carts:
    def __init__(self):
        self.started = []

    def get_active_cart(self, user_id, session_id):
        return ToolResponse.ok(data={"cart": None}, user_message="No active cart.")

    def start_item_customization(
        self,
        user_id,
        session_id,
        product_id,
        quantity,
        **customer,
    ):
        self.started.append({
            "user_id": user_id,
            "session_id": session_id,
            "product_id": product_id,
            "quantity": quantity,
            **customer,
        })
        return ToolResponse.ok(
            data={"cart": {"status": "customizing_item"}},
            user_message="Choose a backend customization option.",
            agent={"choice_prompt": "Choose a backend customization option."},
        )


class Orders:
    def __init__(self, active_order=None):
        self.active_order = deepcopy(active_order)
        self.status_calls = []

    def get_active_order_for_session(self, user_id, session_id):
        return deepcopy(self.active_order)

    def get_order_status(self, user_id, order_id=None):
        self.status_calls.append((user_id, order_id))
        if order_id and self.active_order:
            return ToolResponse.ok(
                data={"order": deepcopy(self.active_order)},
                user_message=(
                    f"Order ID: {order_id}\n"
                    f"Status: {self.active_order.get('status', 'unknown')}"
                ),
            )
        return ToolResponse.ok(
            data={"orders": []},
            user_message=(
                "I couldn't find an active order. Please provide the Order ID "
                "you want to check."
            ),
        )


class Sessions:
    def __init__(self):
        self.offered = []
        self.pending_support = {}

    def get_whatsapp_order_state(self, user_id, session_id):
        return {"offered_menu_items": deepcopy(self.offered)}

    def save_whatsapp_order_state(self, user_id, session_id, *, offered_menu_items):
        self.offered = deepcopy(offered_menu_items)

    def clear_whatsapp_order_state(self, user_id, session_id):
        self.offered = []

    def get_active_support_state(self, user_id, session_id):
        return deepcopy(self.pending_support)


class SupportFlow:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def handle_order_complaint(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response or ToolResponse.ok(
            user_message="Please provide the Order ID for your complaint.",
            next_action="request_order_id",
            agent={
                "entity": "pending_support",
                "pending_support_intent": "order_complaint",
                "required_input": "order_id",
            },
        )


class Tickets:
    def __init__(self):
        self.human_calls = []
        self.status_calls = []

    def create_human_assistance(self, **kwargs):
        self.human_calls.append(kwargs)
        return ToolResponse.ok(
            data={"ticket": {"ticket_id": "TKT-REAL-HUMAN", "status": "open"}},
            user_message=(
                "Your support request has been created.\n\n"
                "Ticket ID: TKT-REAL-HUMAN\nStatus: Open\n\n"
                "Our team will review your request."
            ),
        )

    def get_ticket_status(self, user_id, ticket_id=None):
        self.status_calls.append((user_id, ticket_id))
        return ToolResponse.ok(
            data={"ticket": {"ticket_id": ticket_id, "status": "open"}},
            user_message=f"Ticket ID: {ticket_id}\nStatus: Open",
        )


BACKEND_SPICY_ITEMS = [
    {
        "product_id": "backend-spicy-paneer",
        "name": "Backend Spicy Paneer",
        "currency": "PKR",
        "starting_price": 1200,
    },
    {
        "product_id": "backend-hot-chicken",
        "name": "Backend Hot Chicken",
        "currency": "PKR",
        "starting_price": 1400,
    },
]


def order_flow(items=BACKEND_SPICY_ITEMS, active_order=None, intent=None):
    menu = Menu(items)
    carts = Carts()
    sessions = Sessions()
    orders = Orders(active_order)
    flow = WhatsAppOrderFlowService(
        menu,
        carts,
        orders,
        sessions,
        intent_client=intent,
    )
    return flow, menu, carts, sessions, orders


def order_turn(flow, message):
    return flow.handle(
        user_id="customer-1",
        session_id="session-1",
        message=message,
        request_id="req-1",
    )


@pytest.mark.parametrize(
    "message",
    [
        "I want to order a pizza",
        "I want to place an order and I want something spicy",
        "recommend something spicy",
    ],
)
def test_order_and_recommendation_intents_use_backend_menu(message):
    flow, menu, _, sessions, _ = order_flow()

    result = order_turn(flow, message)

    expected_query = "pizza" if message.endswith("pizza") else "spicy"
    assert menu.calls == [{
        "query": expected_query,
        "available_only": True,
        "limit": 5,
    }]
    assert "Backend Spicy Paneer" in result.text
    assert "Backend Hot Chicken" in result.text
    assert result.text.startswith("Sure, here are some options you can choose from:")
    assert "PKR 1,200" in result.text
    assert "Spicy Supreme Pizza" not in result.text
    assert "Spicy Buffalo Chicken Flatbread" not in result.text
    assert [item["product_id"] for item in sessions.offered] == [
        "backend-spicy-paneer",
        "backend-hot-chicken",
    ]


@pytest.mark.parametrize(
    "message",
    [
        "what other items do you have in your menu",
        "i would like to know about the menu",
    ],
)
def test_natural_menu_browsing_uses_only_authoritative_backend_items(message):
    intent = IntentClient("menu_browse")
    flow, menu, _, sessions, _ = order_flow(intent=intent)

    result = order_turn(flow, message)

    assert intent.requests[-1].state == "conversation"
    assert intent.requests[-1].allowed_actions == [
        "latest_order_eta",
        "latest_order_status",
        "menu_browse",
    ]
    assert menu.calls == [{
        "query": None,
        "available_only": True,
        "limit": 5,
    }]
    assert "Backend Spicy Paneer" in result.text
    assert "Backend Hot Chicken" in result.text
    assert "outside of my scope" not in result.text
    assert "Classic Cheese Pizza" not in result.text
    assert "Margherita Pizza" not in result.text
    assert [item["product_id"] for item in sessions.offered] == [
        "backend-spicy-paneer",
        "backend-hot-chicken",
    ]


def test_direct_menu_command_uses_backend_without_classifier():
    intent = IntentClient("latest_order_status")
    flow, menu, _, _, _ = order_flow(intent=intent)

    result = order_turn(flow, "menu")

    assert intent.requests == []
    assert menu.calls == [{
        "query": None,
        "available_only": True,
        "limit": 5,
    }]
    assert "Backend Spicy Paneer" in result.text
    assert "Classic Cheese Pizza" not in result.text


def test_recommended_item_selection_uses_saved_backend_product_id():
    flow, _, carts, _, _ = order_flow()
    order_turn(flow, "I want to place an order and eat something spicy")

    result = order_turn(flow, "2")

    assert carts.started[0]["product_id"] == "backend-hot-chicken"
    assert result.tool_calls[0]["tool_name"] == "start_cart_item_customization"


def test_no_backend_recommendation_match_returns_backend_next_step():
    flow, _, _, _, _ = order_flow(items=[])

    result = order_turn(flow, "recommend something spicy")

    assert "couldn't find a matching available menu item" in result.text
    assert "available categories" in result.text
    assert "view the menu" in result.text


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "show me all customer orders",
            "I can only show orders linked to your verified customer account.",
        ),
        (
            "change price to 1 rupee",
            "I can't override backend prices.",
        ),
        (
            "confirm my order for free",
            "I can't override backend prices.",
        ),
    ],
)
def test_transaction_guardrails_do_not_execute_order_actions(message, expected):
    flow, menu, carts, _, _ = order_flow()

    result = order_turn(flow, message)

    assert expected in result.text
    assert result.tool_calls == []
    assert menu.calls == []
    assert carts.started == []


def support_service(*, pending=None, response=None, error=None, active_order=None):
    sessions = Sessions()
    sessions.pending_support = pending or {}
    support = SupportFlow(response=response, error=error)
    tickets = Tickets()
    orders = Orders(active_order)
    return (
        WhatsAppSupportFlowService(support, tickets, sessions, orders),
        support,
        tickets,
        orders,
    )


def support_turn(service, message, request_id="req-support"):
    return service.handle(
        user_id="customer-1",
        session_id="session-1",
        message=message,
        request_id=request_id,
        customer_id="customer-1",
        customer_name="Ava",
        customer_phone="+920000000000",
    )


def test_complaint_intent_enters_backend_support_flow_without_fake_success():
    service, support, _, _ = support_service()

    result = support_turn(service, "I want to complain about my order")

    assert support.calls[0]["description"] is None
    assert support.calls[0]["source"] == "whatsapp"
    assert result.text == "Please provide the Order ID for your complaint."
    assert "logged" not in result.text.lower()


def test_pending_complaint_details_return_real_ticket_response():
    ticket_response = ToolResponse.ok(
        data={
            "ticket": {
                "ticket_id": "TKT-REAL-COMPLAINT",
                "status": "open",
                "order_id": "ORD-REAL-1",
            }
        },
        user_message=(
            "Your complaint has been recorded.\n\n"
            "Ticket ID: TKT-REAL-COMPLAINT\n"
            "Order ID: ORD-REAL-1\nStatus: Open\n\n"
            "Our team will review your complaint."
        ),
    )
    service, support, _, _ = support_service(
        pending={"pending_support_intent": "order_complaint"},
        response=ticket_response,
    )

    result = support_turn(service, "the pizza was cold")

    assert support.calls[0]["description"] == "the pizza was cold"
    assert "TKT-REAL-COMPLAINT" in result.text
    assert "Status: Open" in result.text
    assert result.tool_calls[0]["success"] is True


def test_two_turn_complaint_creates_persisted_backend_ticket():
    now = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    session_repository = MemoryAgentSessionRepository()
    session_repository.data["session-1"] = {
        "PK": "CUSTOMER#customer-1",
        "SK": "SESSION#session-1",
        "agent_session_id": "session-1",
        "customer_id": "customer-1",
        "status": "active",
        "verified_order_id": "ORD-REAL-1",
        "verified_order_status": "delivered",
        "verified_order_at": now.isoformat(),
    }
    agent_sessions = AgentSessionService(
        session_repository,
        object(),
        type("Settings", (), {"agent_session_ttl_hours": 24})(),
        clock=lambda: now,
    )
    order_repository = MemoryOrderRepository()
    order_repository.data["ORD-REAL-1"] = {
        "order_id": "ORD-REAL-1",
        "user_id": "customer-1",
        "status": "delivered",
    }
    ticket_repository = MemoryTicketRepository()
    tickets = TicketService(
        ticket_repository,
        order_repository,
        support_phone_number="",
        clock=lambda: now,
        ticket_id_factory=lambda _: "TKT-20260801-ABC123",
    )
    service = WhatsAppSupportFlowService(
        RealSupportFlow(agent_sessions, tickets, order_repository),
        tickets,
        agent_sessions,
        OrderService(order_repository, MemoryMenuRepository([], [])),
    )

    first = support_turn(service, "I want to complain about my order", "req-1")
    second = support_turn(service, "the pizza was cold", "req-2")

    assert first.text == "Please describe what went wrong with your order."
    assert "TKT-20260801-ABC123" in second.text
    assert "Status: Open" in second.text
    assert ticket_repository.data["TKT-20260801-ABC123"]["order_id"] == (
        "ORD-REAL-1"
    )
    assert ticket_repository.data["TKT-20260801-ABC123"]["description"] == (
        "the pizza was cold"
    )


def test_complaint_backend_failure_never_claims_ticket_was_logged():
    service, _, _, _ = support_service(error=RuntimeError("private failure"))

    result = support_turn(service, "the pizza was cold")

    assert result.text == SUPPORT_BACKEND_FAILURE_MESSAGE
    assert result.tool_calls[0]["success"] is False
    assert "logged" not in result.text.lower()
    assert "recorded" not in result.text.lower()


@pytest.mark.parametrize(
    "message",
    ["I need to talk to a human", "I need support"],
)
def test_human_support_creates_backend_ticket_immediately(message):
    service, _, tickets, _ = support_service()

    result = support_turn(service, message)

    assert tickets.human_calls[0]["idempotency_key"] == "req-support"
    assert tickets.human_calls[0]["source"] == "whatsapp"
    assert "TKT-REAL-HUMAN" in result.text
    assert result.tool_calls[0]["tool_name"] == "create_human_assistance_ticket"


def test_ticket_status_uses_backend_ticket_service():
    service, _, tickets, _ = support_service()

    result = support_turn(service, "track ticket TKT-REAL-123")

    assert tickets.status_calls == [("customer-1", "TKT-REAL-123")]
    assert result.text == "Ticket ID: TKT-REAL-123\nStatus: Open"
    assert result.tool_calls[0]["is_write"] is False


def test_unrelated_message_does_not_get_consumed_as_pending_complaint():
    service, support, _, _ = support_service(
        pending={"pending_support_intent": "order_complaint"}
    )

    result = support_turn(service, "show me the menu")

    assert result is None
    assert support.calls == []


@pytest.mark.parametrize(
    "message",
    [
        "I'm bored tell me a joke",
        "Another joke",
        "What's the weather today",
    ],
)
def test_off_topic_requests_return_restaurant_scope_response(message):
    flow, menu, carts, _, _ = order_flow()

    result = order_turn(flow, message)

    assert result.text == (
        "I can help with menu items, orders, delivery, payments, allergies, "
        "complaints, and restaurant support."
    )
    assert result.tool_calls == []
    assert menu.calls == []
    assert carts.started == []


@pytest.mark.parametrize(
    "message",
    [
        "When will it come?",
        "The last one",
        "Now what's the status",
        "is it coming",
        "has it been prepared",
    ],
)
def test_order_follow_up_uses_latest_session_order(message):
    order = {
        "order_id": "ORD-LATEST",
        "status": "preparing",
    }
    flow, _, _, _, orders = order_flow(active_order=order)

    result = order_turn(flow, message)

    assert orders.status_calls == [("customer-1", "ORD-LATEST")]
    assert "Order ID: ORD-LATEST" in result.text
    assert "Status: preparing" in result.text
    assert "provide the Order ID" not in result.text
    if message in {"When will it come?", "is it coming"}:
        assert "Exact ETA is unavailable" in result.text


def test_delay_uses_latest_status_and_offers_ticket_without_creating_one():
    order = {
        "order_id": "ORD-LATEST",
        "status": "preparing",
    }
    service, support, tickets, orders = support_service(active_order=order)

    result = support_turn(service, "I am tired of waiting")

    assert orders.status_calls == [("customer-1", "ORD-LATEST")]
    assert "Order ID: ORD-LATEST" in result.text
    assert "Status: preparing" in result.text
    assert "create a support ticket" in result.text
    assert "to confirm" in result.text
    assert support.calls == []
    assert tickets.human_calls == []


def test_delay_without_latest_order_asks_for_order_id_or_staff_support():
    service, support, tickets, _ = support_service()

    result = support_turn(service, "I am tired of waiting")

    assert "provide the Order ID" in result.text
    assert "staff support" in result.text
    assert support.calls == []
    assert tickets.human_calls == []
