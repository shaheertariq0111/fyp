from copy import deepcopy

import pytest

from src.agent.order_intent import OrderIntentClassification
from src.models.tool_responses import ToolResponse
from src.services.order_service import OrderService
from src.services.whatsapp_order_flow_service import WhatsAppOrderFlowService


class IntentClient:
    def __init__(self, action, confidence=0.95, selected_option=None):
        self.result = OrderIntentClassification(
            action=action,
            confidence=confidence,
            selected_option=selected_option,
        )
        self.requests = []

    def classify_order_intent(self, request):
        self.requests.append(request)
        return self.result


class FakeMenu:
    def search_menu(self, **kwargs):
        raise AssertionError("menu search is not expected")


class FakeSessions:
    def get_whatsapp_order_state(self, user_id, session_id):
        return {}


class FakeOrders:
    def __init__(self, order=None):
        self.order = deepcopy(order)
        self.updates = []
        self.saved_addresses = []

    def get_active_order_for_session(self, user_id, session_id):
        return deepcopy(self.order)

    def update_order_flow(self, order_id, action, value=None, idempotency_key=None):
        self.updates.append((order_id, action, value, idempotency_key))
        if action == "save_address" and not OrderService.is_valid_delivery_address(value):
            return ToolResponse.error(
                error_code="INVALID_DELIVERY_ADDRESS",
                user_message=(
                    "I still need a valid delivery address for delivery. Please "
                    "send your full address, or reply takeaway to switch to pickup."
                ),
            )
        status = {
            "confirm": "submitted_to_restaurant",
            "set_delivery": "awaiting_delivery_address",
            "set_takeaway": "pending_confirmation",
            "save_address": "pending_confirmation",
            "cancel": "cancelled",
        }[action]
        if action == "save_address":
            self.saved_addresses.append(value)
        data = {
            **(self.order or {}),
            "order_id": order_id,
            "status": status,
            "total": 850,
            "currency": "PKR",
            "delivery_address": value if action == "save_address" else None,
        }
        return ToolResponse.ok(
            data=data,
            user_message="Backend order state updated.",
            next_action={
                "submitted_to_restaurant": "await_restaurant_update",
                "awaiting_delivery_address": "ask_delivery_address",
                "pending_confirmation": "confirm_or_cancel",
                "cancelled": "none",
            }[status],
            agent={
                "order_summary": {
                    "items": [],
                    "total": 850,
                    "currency": "PKR",
                },
                "submission_confirmation": "Backend confirmation.",
                "confirmation_summary": "Backend confirmation summary.",
            },
        )

    def get_order_status(self, user_id, order_id=None):
        return ToolResponse.ok(
            data={"order": deepcopy(self.order)} if order_id else {"orders": []},
            user_message="Choose a valid order action.",
            agent={"confirmation_summary": "Please confirm or cancel."},
        )


class FakeCarts:
    def __init__(self, status):
        self.cart = {"cart_id": "CART-1", "status": status}
        self.checkout_calls = []
        self.upsell_calls = []

    def get_active_cart(self, user_id, session_id):
        return ToolResponse.ok(
            data={"cart": deepcopy(self.cart)},
            user_message="Current cart.",
            agent={},
        )

    def create_pending_order(self, cart_id):
        self.checkout_calls.append(cart_id)
        return ToolResponse.ok(
            data={
                "order_id": "ORD-REAL",
                "status": "awaiting_fulfillment_method",
                "total": 850,
                "currency": "PKR",
            },
            user_message="The order is ready for fulfilment details.",
            next_action="ask_fulfillment_method",
            agent={
                "order_summary": {
                    "items": [{"name": "Pepperoni Passion", "quantity": 1}],
                    "total": 850,
                    "currency": "PKR",
                }
            },
        )

    def handle_upsell(self, cart_id, action, item_id=None, quantity=1):
        self.upsell_calls.append((cart_id, action, item_id, quantity))
        if action == "get_options":
            self.cart["status"] = "awaiting_upsell_decision"
            return ToolResponse.ok(
                data={
                    "cart_id": cart_id,
                    "upsell_items": [{"product_id": "cola", "name": "Cola"}],
                },
                user_message="Would you like a drink?",
                next_action="choose_upsell",
                agent={"upsell_prompt": "Would you like a drink?"},
            )
        self.cart["status"] = "cart_ready"
        return ToolResponse.ok(
            data={"cart_id": cart_id, "status": "cart_ready"},
            user_message="Your cart is ready.",
            next_action="create_pending_order",
        )


def service(*, cart_status="cart_ready", order=None, intent=None):
    carts = FakeCarts(cart_status)
    orders = FakeOrders(order)
    flow = WhatsAppOrderFlowService(
        FakeMenu(),
        carts,
        orders,
        FakeSessions(),
        intent_client=intent,
    )
    return flow, carts, orders


def handle(flow, message):
    return flow.handle(
        user_id="user-1",
        session_id="session-1",
        message=message,
        request_id="req-1",
    )


@pytest.mark.parametrize("message", ["checkouttt", "continuee"])
def test_messy_checkout_language_uses_interpreter_then_real_cart_service(message):
    intent = IntentClient("checkout")
    flow, carts, _ = service(intent=intent)

    result = handle(flow, message)

    assert carts.checkout_calls == ["CART-1"]
    assert result.tool_calls[0]["tool_name"] == "create_pending_order_from_cart"
    assert result.tool_calls[0]["result"]["data"]["order_id"] == "ORD-REAL"
    assert result.tool_calls[0]["result"]["data"]["total"] == 850
    assert intent.requests[0].state == "cart_ready"
    assert intent.requests[0].allowed_actions == ["checkout"]


@pytest.mark.parametrize("message", ["confirmm", "yeah go ahead"])
def test_messy_confirmation_only_confirms_in_pending_confirmation(message):
    intent = IntentClient("confirm")
    order = {
        "order_id": "ORD-REAL",
        "status": "pending_confirmation",
        "total": 850,
        "currency": "PKR",
    }
    flow, _, orders = service(order=order, intent=intent)

    result = handle(flow, message)

    assert orders.updates == [("ORD-REAL", "confirm", None, "req-1")]
    assert result.tool_calls[0]["result"]["data"]["status"] == "submitted_to_restaurant"
    assert result.tool_calls[0]["result"]["data"]["order_id"] == "ORD-REAL"
    assert result.tool_calls[0]["result"]["data"]["total"] == 850


def test_interpreted_action_is_rejected_outside_current_state_allowlist():
    intent = IntentClient("confirm")
    flow, carts, _ = service(intent=intent)

    result = handle(flow, "yeah go ahead")

    assert carts.checkout_calls == []
    assert result.text == "Your cart is ready. Reply checkout to continue."
    assert result.tool_calls == []


def test_numbered_fulfilment_choice_does_not_call_interpreter():
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_fulfillment_method",
        "total": 850,
        "currency": "PKR",
    }
    intent = IntentClient("confirm")
    flow, _, orders = service(order=order, intent=intent)

    handle(flow, "1")

    assert orders.updates[0][1] == "set_delivery"
    assert intent.requests == []


@pytest.mark.parametrize(
    ("message", "expected_action"),
    [("i want delivery", "set_delivery"), ("take away", "set_takeaway")],
)
def test_fulfilment_language_executes_only_backend_order_action(message, expected_action):
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_fulfillment_method",
        "total": 850,
        "currency": "PKR",
    }
    intent = IntentClient("confirm")
    flow, _, orders = service(order=order, intent=intent)

    handle(flow, message)

    assert orders.updates[0][1] == expected_action
    assert intent.requests == []


def test_no_thanks_skips_upsell_only_in_upsell_state_without_llm():
    intent = IntentClient("checkout")
    flow, carts, _ = service(
        cart_status="awaiting_upsell_decision",
        intent=intent,
    )

    handle(flow, "no thanks")

    assert [call[1] for call in carts.upsell_calls] == ["get_options", "skip"]
    assert intent.requests == []


def test_no_thanks_does_not_become_checkout_outside_upsell_state():
    intent = IntentClient("checkout")
    flow, carts, _ = service(intent=intent)

    result = handle(flow, "no thanks")

    assert carts.checkout_calls == []
    assert intent.requests == []
    assert result.text == "Your cart is ready. Reply checkout to continue."


def test_low_confidence_message_asks_for_state_specific_clarification():
    intent = IntentClient("checkout", confidence=0.4)
    flow, carts, _ = service(intent=intent)

    result = handle(flow, "maybe later or something")

    assert carts.checkout_calls == []
    assert result.text == "Your cart is ready. Reply checkout to continue."


@pytest.mark.parametrize("message", ["I need support", "I have a complaint"])
def test_support_and_complaint_messages_never_reach_order_interpreter(message):
    intent = IntentClient("checkout")
    flow, carts, orders = service(intent=intent)

    result = handle(flow, message)

    assert result is None
    assert carts.checkout_calls == []
    assert orders.updates == []
    assert intent.requests == []


@pytest.mark.parametrize("message", ["No", "nah", "none", "n/a", "skip", "Ok"])
def test_delivery_address_step_rejects_invalid_reply_without_saving(message):
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_delivery_address",
        "fulfillment_method": "delivery",
    }
    flow, _, orders = service(order=order)

    result = handle(flow, message)

    assert orders.saved_addresses == []
    assert result.tool_calls[0]["success"] is False
    assert result.tool_calls[0]["error_code"] == "INVALID_DELIVERY_ADDRESS"
    assert result.text == (
        "I still need a valid delivery address for delivery. Please send your "
        "full address, or reply takeaway to switch to pickup."
    )


def test_delivery_address_step_accepts_realistic_address():
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_delivery_address",
        "fulfillment_method": "delivery",
    }
    flow, _, orders = service(order=order)

    result = handle(flow, "D-07-07, Flexis, One South")

    assert orders.saved_addresses == ["D-07-07, Flexis, One South"]
    assert result.tool_calls[0]["success"] is True
    assert result.tool_calls[0]["result"]["data"]["status"] == (
        "pending_confirmation"
    )


@pytest.mark.parametrize("message", ["takeaway", "pickup", "collect"])
def test_delivery_address_step_can_switch_to_takeaway(message):
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_delivery_address",
        "fulfillment_method": "delivery",
    }
    flow, _, orders = service(order=order)

    result = handle(flow, message)

    assert orders.updates == [("ORD-REAL", "set_takeaway", None, None)]
    assert result.tool_calls[0]["result"]["data"]["status"] == (
        "pending_confirmation"
    )
