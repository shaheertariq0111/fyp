from copy import deepcopy

import pytest

from src.agent.order_intent import OrderIntentClassification
from src.models.tool_responses import ToolResponse
from src.services.order_service import OrderService
from src.services.whatsapp_order_flow_service import WhatsAppOrderFlowService


class IntentClient:
    def __init__(
        self,
        action,
        confidence=0.95,
        selected_option=None,
        extracted_name=None,
    ):
        self.result = OrderIntentClassification(
            action=action,
            confidence=confidence,
            selected_option=selected_option,
            extracted_name=extracted_name,
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
        self.status_calls = []

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
            "save_customer_name": "pending_confirmation",
            "confirm_customer_name": "pending_confirmation",
            "reject_customer_name": "awaiting_customer_name",
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
            "customer_name": (
                value
                if action == "save_customer_name"
                else (self.order or {}).get("suggested_customer_name")
                if action == "confirm_customer_name"
                else (self.order or {}).get("customer_name")
            ),
        }
        if action == "reject_customer_name":
            data["customer_name_suggestion_rejected"] = True
        self.order = deepcopy(data)
        return ToolResponse.ok(
            data=data,
            user_message=(
                "Can I have your name for the order?"
                if action == "reject_customer_name"
                else "Backend order state updated."
            ),
            next_action={
                "submitted_to_restaurant": "await_restaurant_update",
                "awaiting_delivery_address": "ask_delivery_address",
                "pending_confirmation": "confirm_or_cancel",
                "awaiting_customer_name": "ask_customer_name",
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
        self.status_calls.append((user_id, order_id))
        if order_id is None:
            return ToolResponse.ok(
                data={"orders": []},
                user_message=(
                    "I couldn't find an active order. Please provide the Order ID "
                    "you want to check."
                ),
            )
        return ToolResponse.ok(
            data={"order": deepcopy(self.order)},
            user_message=f"Order ID: {order_id}\nStatus: preparing",
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
    assert intent.requests[-1].state == "cart_ready"
    assert intent.requests[-1].allowed_actions == ["checkout"]


def test_latest_order_eta_intent_precedes_menu_routing_and_uses_backend_status():
    intent = IntentClient("latest_order_eta")
    order = {
        "order_id": "ORD-LATEST",
        "status": "preparing",
        "total": 850,
        "currency": "PKR",
    }
    flow, carts, orders = service(order=order, intent=intent)

    result = handle(flow, "when will I receive my order")

    assert intent.requests[0].state == "conversation"
    assert intent.requests[0].allowed_actions == [
        "latest_order_eta",
        "latest_order_status",
    ]
    assert orders.status_calls == [("user-1", "ORD-LATEST")]
    assert carts.checkout_calls == []
    assert "Order ID: ORD-LATEST" in result.text
    assert "Status: preparing" in result.text
    assert "Exact ETA is unavailable" in result.text


def test_latest_order_eta_without_order_asks_for_order_id():
    intent = IntentClient("latest_order_eta")
    flow, carts, orders = service(intent=intent)

    result = handle(flow, "when will I receive my order")

    assert orders.status_calls == [("user-1", None)]
    assert carts.checkout_calls == []
    assert "provide the Order ID" in result.text
    assert "Exact ETA" not in result.text


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


def test_explicit_name_correction_updates_snapshot_without_submitting():
    order = {
        "order_id": "ORD-REAL",
        "status": "pending_confirmation",
        "customer_name": "shaheer",
        "total": 850,
        "currency": "PKR",
    }
    flow, _, orders = service(order=order)

    result = handle(
        flow,
        "you misspelled my name, it's actually Shaheer Tariq",
    )

    assert orders.updates == [
        ("ORD-REAL", "save_customer_name", "Shaheer Tariq", None)
    ]
    assert result.tool_calls[0]["result"]["data"]["status"] == (
        "pending_confirmation"
    )
    assert orders.order["customer_name"] == "Shaheer Tariq"
    assert "Backend confirmation summary" in result.text


def test_classifier_name_correction_uses_only_validated_extracted_name():
    intent = IntentClient(
        "customer_name_correction",
        extracted_name="Shaheer Tariq",
    )
    order = {
        "order_id": "ORD-REAL",
        "status": "pending_confirmation",
        "customer_name": "shaheer",
    }
    flow, _, orders = service(order=order, intent=intent)

    handle(flow, "the correct customer name should be Shaheer Tariq")

    assert intent.requests[-1].allowed_actions == [
        "customer_name_correction",
        "confirm",
        "cancel",
    ]
    assert orders.updates == [
        ("ORD-REAL", "save_customer_name", "Shaheer Tariq", None)
    ]


@pytest.mark.parametrize(
    "message",
    [
        "my name is yes",
        "change my name to no",
        "put it under ok",
        "put it under delivery",
        "my name is Pepperoni Pizza",
        "my name is Shaheer Tariq please",
    ],
)
def test_invalid_name_correction_does_not_update_or_submit(message):
    order = {
        "order_id": "ORD-REAL",
        "status": "pending_confirmation",
        "customer_name": "shaheer",
    }
    flow, _, orders = service(order=order)

    result = handle(flow, message)

    assert orders.updates == []
    assert orders.order["status"] == "pending_confirmation"
    assert result.tool_calls == []
    assert "corrected customer name only" in result.text


def test_classifier_cannot_invent_a_name_missing_from_customer_message():
    intent = IntentClient(
        "customer_name_correction",
        extracted_name="Invented Name",
    )
    order = {
        "order_id": "ORD-REAL",
        "status": "pending_confirmation",
        "customer_name": "shaheer",
    }
    flow, _, orders = service(order=order, intent=intent)

    result = handle(flow, "please correct the customer name")

    assert orders.updates == []
    assert result.tool_calls == []
    assert orders.order["status"] == "pending_confirmation"


def test_later_yes_submits_after_name_correction():
    order = {
        "order_id": "ORD-REAL",
        "status": "pending_confirmation",
        "customer_name": "shaheer",
        "total": 850,
        "currency": "PKR",
    }
    flow, _, orders = service(order=order)

    handle(flow, "my name is Shaheer Tariq")
    result = handle(flow, "yes")

    assert [update[1] for update in orders.updates] == [
        "save_customer_name",
        "confirm",
    ]
    assert result.tool_calls[0]["result"]["data"]["status"] == (
        "submitted_to_restaurant"
    )


def test_yes_alone_still_confirms_pending_order():
    order = {
        "order_id": "ORD-REAL",
        "status": "pending_confirmation",
        "customer_name": "Shaheer Tariq",
    }
    flow, _, orders = service(order=order)

    handle(flow, "yes")

    assert orders.updates == [("ORD-REAL", "confirm", None, "req-1")]


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


def test_customer_name_is_saved_only_while_order_awaits_name():
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_customer_name",
    }
    flow, _, orders = service(order=order)

    result = handle(flow, "Ava Khan")

    assert orders.updates == [
        ("ORD-REAL", "save_customer_name", "Ava Khan", None)
    ]
    assert result.tool_calls[0]["result"]["data"]["status"] == (
        "pending_confirmation"
    )


def test_yes_confirms_only_backend_suggested_whatsapp_name():
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_customer_name",
        "suggested_customer_name": "Profile Alias",
    }
    flow, _, orders = service(order=order)

    result = handle(flow, "yes")

    assert orders.updates == [
        ("ORD-REAL", "confirm_customer_name", None, None)
    ]
    assert result.tool_calls[0]["result"]["data"]["customer_name"] == (
        "Profile Alias"
    )


def test_no_rejects_suggested_name_and_asks_for_customer_name():
    order = {
        "order_id": "ORD-REAL",
        "status": "awaiting_customer_name",
        "suggested_customer_name": "Profile Alias",
    }
    flow, _, orders = service(order=order)

    result = handle(flow, "no")

    assert orders.updates == [
        ("ORD-REAL", "reject_customer_name", None, None)
    ]
    assert result.text == "Can I have your name for the order?"
