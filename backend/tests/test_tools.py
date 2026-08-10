import inspect
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from fakes import MemoryAgentSessionRepository, MemoryOrderRepository
from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.models.tool_responses import GroundingEvidence, GroundingOption, ToolResponse
from src.models.conversation_contracts import OptionContract
from src.services.agent_session_service import AgentSessionService
from src.services.support_flow_service import SupportFlowService


class MenuStub:
    customer_result_limit = 5

    def search_menu(self, **kwargs):
        return ToolResponse.ok(data=kwargs, user_message="ok")

    def get_menu_item(self, item_id):
        return ToolResponse.ok(data={"item_id": item_id}, user_message="ok")


class SessionStub:
    def create_link(self, user_id, session_id, item_id, customer_id=None):
        return ToolResponse.ok(data={"user_id": user_id, "session_id": session_id,
                                     "item_id": item_id, "customer_id": customer_id}, user_message="ok")


class CartStub:
    def __init__(self):
        self.checkout_calls = []
        self.start_calls = []

    def start_item_customization(self, *args, **kwargs):
        self.start_calls.append((args, kwargs))
        return ToolResponse.ok(
            data={"cart_id": "CART-1"},
            user_message="started",
            grounding=GroundingEvidence(transactional_effects=["item_selected"]),
        )

    def get_active_cart(self, user_id, session_id):
        return ToolResponse.ok(
            data={"cart": {"user_id": user_id, "session_id": session_id}},
            user_message="cart",
        )

    def create_pending_order(self, user_id, cart_id):
        self.checkout_calls.append((user_id, cart_id))
        return ToolResponse.ok(
            data={"order": {"order_id": "ORD-CHECKOUT"}},
            user_message="checkout",
        )


class CustomerStub:
    def __init__(self):
        self.address_calls = []

    def get_profile(self, customer_id):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id}}, user_message="ok")

    def update_profile(self, customer_id, **kwargs):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id, **kwargs}},
                               user_message="ok")

    def save_address(self, customer_id, **kwargs):
        self.address_calls.append((customer_id, kwargs))
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id},
                                     "address": kwargs},
                               user_message="ok")


class OrderMutationStub:
    def __init__(self):
        self.calls = []

    def update_order_flow(
        self,
        user_id,
        order_id,
        action,
        value=None,
        idempotency_key=None,
    ):
        self.calls.append((user_id, order_id, action, value, idempotency_key))
        return ToolResponse.ok(
            data={"order": {"order_id": order_id, "status": action}},
            user_message=f"{action} ok",
        )


def test_mvp_tools_include_active_cart_lookup():
    assert len(tools.MVP_TOOLS) == 28
    assert tools.get_active_cart in tools.MVP_TOOLS
    assert tools.get_customer_profile in tools.MVP_TOOLS
    assert tools.update_customer_profile in tools.MVP_TOOLS
    assert tools.save_customer_address in tools.MVP_TOOLS
    assert tools.begin_checkout in tools.MVP_TOOLS
    assert tools.choose_delivery in tools.MVP_TOOLS
    assert tools.choose_takeaway in tools.MVP_TOOLS
    assert tools.save_order_address in tools.MVP_TOOLS
    assert tools.confirm_order in tools.MVP_TOOLS
    assert tools.cancel_order in tools.MVP_TOOLS
    assert tools.create_human_assistance_ticket in tools.MVP_TOOLS
    assert tools.handle_order_complaint in tools.MVP_TOOLS
    assert tools.get_support_ticket_status in tools.MVP_TOOLS
    assert tools.request_human_support in tools.MVP_TOOLS
    assert tools.create_order_complaint in tools.MVP_TOOLS
    assert tools.cancel_support_request in tools.MVP_TOOLS
    assert tools.get_support_ticket in tools.MVP_TOOLS
    assert "begin_checkout" in tools.WRITE_TOOLS
    assert "choose_delivery" in tools.WRITE_TOOLS
    assert "choose_takeaway" in tools.WRITE_TOOLS
    assert "save_order_address" in tools.WRITE_TOOLS
    assert "confirm_order" in tools.WRITE_TOOLS
    assert "cancel_order" in tools.WRITE_TOOLS
    assert "create_human_assistance_ticket" in tools.WRITE_TOOLS
    assert "handle_order_complaint" in tools.WRITE_TOOLS
    assert "request_human_support" in tools.WRITE_TOOLS
    assert "create_order_complaint" in tools.WRITE_TOOLS
    assert "cancel_support_request" in tools.WRITE_TOOLS
    assert "get_support_ticket_status" not in tools.WRITE_TOOLS
    assert "get_support_ticket" not in tools.WRITE_TOOLS


def test_channel_scoped_capabilities_keep_whatsapp_chat_native_without_menu_link():
    whatsapp = tools.tools_for_channel("whatsapp")
    web = tools.tools_for_channel("web")
    assert tools.create_menu_session_link not in whatsapp
    assert tools.create_menu_session_link in web
    assert tools.whatsapp_start_cart_item_customization in whatsapp
    assert tools.start_cart_item_customization not in whatsapp
    for capability in (
        tools.search_menu,
        tools.save_customization_choice,
        tools.begin_checkout,
        tools.confirm_order,
    ):
        assert capability in whatsapp


def test_option_capabilities_expose_only_structured_contract_arguments():
    role = tools.search_menu.tool_spec["inputSchema"]["json"]["properties"][
        "presentation_role"
    ]
    assert role["enum"] == ["selection_offer", "informational_reference"]
    start_parameters = inspect.signature(
        tools.start_cart_item_customization
    ).parameters
    assert {"contract_id", "contract_version", "selected_option_id"}.issubset(
        start_parameters
    )
    assert not {
        "creation_idempotency_key",
        "customer_message",
        "current_message",
        "ordinal",
    }.intersection(start_parameters)
    whatsapp_schema = tools.whatsapp_start_cart_item_customization.tool_spec[
        "inputSchema"
    ]["json"]
    assert tools.whatsapp_start_cart_item_customization.tool_spec["name"] == (
        "start_cart_item_customization"
    )
    assert set(whatsapp_schema["required"]) == {
        "item_id", "contract_id", "contract_version", "selected_option_id"
    }
    web_schema = tools.start_cart_item_customization.tool_spec["inputSchema"]["json"]
    assert web_schema["required"] == ["item_id"]
    order_status_role = tools.get_order_status.tool_spec["inputSchema"]["json"][
        "properties"
    ]["presentation_role"]
    assert order_status_role["enum"] == [
        "selection_offer", "informational_reference"
    ]


def test_web_start_cart_tool_remains_contract_optional(monkeypatch):
    carts = CartStub()
    monkeypatch.setattr(tools, "get_services", lambda: SimpleNamespace(carts=carts))

    with request_context(AgentRequestContext("user-1", "session-1", channel="web")):
        result = tools.start_cart_item_customization("item-1")

    assert result["success"] is True
    assert carts.start_calls[0][1]["creation_idempotency_key"] is None


@pytest.mark.parametrize(
    ("chosen_id", "customer_message"),
    [
        ("item-5", "order the 5th one for me"),
        ("chicken-fajita", "Chicken Fajita"),
    ],
)
def test_whatsapp_semantic_selection_executes_only_with_llm_supplied_opaque_id(
    monkeypatch, chosen_id, customer_message,
):
    now = datetime.now(timezone.utc)
    active = OptionContract(
        contract_id="contract-1",
        contract_version=1,
        required_effect="item_selected",
        consumer_capability="start_cart_item_customization",
        source_capability="search_menu",
        source_request_id="request-1",
        options=[
            {"id": option_id, "label": f"Option {index}"}
            for index, option_id in enumerate(
                ["item-1", "item-2", "item-3", "item-4", chosen_id], start=1
            )
        ],
        created_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=30)).isoformat(),
    )

    class Sessions:
        def validate_option_contract_consumption(self, *_args, **kwargs):
            assert kwargs["contract_id"] == active.contract_id
            assert kwargs["contract_version"] == active.contract_version
            assert kwargs["selected_option_id"] == chosen_id
            assert kwargs["bound_option_id"] == chosen_id
            return active

    carts = CartStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(carts=carts, agent_sessions=Sessions()),
    )
    context = AgentRequestContext(
        "customer-1", "session-1", customer_id="customer-1",
        channel="whatsapp", current_message=customer_message,
    )

    with request_context(context):
        result = tools.whatsapp_start_cart_item_customization(
            item_id=chosen_id,
            selected_option_id=chosen_id,
            contract_id=active.contract_id,
            contract_version=active.contract_version,
        )

    assert result["success"] is True
    assert carts.start_calls[0][0][2] == chosen_id


def test_whatsapp_selection_binding_contains_no_language_routing():
    source = inspect.getsource(tools._start_cart_item_customization)
    assert "current_message" not in source
    assert "customer_message" not in source
    assert "casefold" not in source
    assert "regex" not in source


def test_start_cart_uses_only_backend_validated_contract_for_creation_key(monkeypatch):
    now = datetime.now(timezone.utc)
    validated = OptionContract(
        contract_id="validated-contract",
        contract_version=4,
        required_effect="item_selected",
        consumer_capability="start_cart_item_customization",
        source_capability="search_menu",
        source_request_id="request-1",
        options=[{"id": "item-1", "label": "Item"}],
        created_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=30)).isoformat(),
    )

    class Sessions:
        def validate_option_contract_consumption(self, *_args, **_kwargs):
            return validated

    carts = CartStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(carts=carts, agent_sessions=Sessions()),
    )
    context = AgentRequestContext(
        "customer-1", "session-1", customer_id="customer-1",
        channel="whatsapp", request_id="request-1",
    )

    with request_context(context):
        result = tools.start_cart_item_customization(
            "item-1",
            contract_id="untrusted-llm-value",
            contract_version=999,
            selected_option_id="item-1",
        )

    assert result["success"] is True
    assert carts.start_calls[0][1]["creation_idempotency_key"] == (
        "validated-contract:4"
    )


def test_invalid_contract_never_reaches_initial_cart_creation(monkeypatch):
    class Sessions:
        def validate_option_contract_consumption(self, *_args, **_kwargs):
            return ToolResponse.error(
                error_code="INVALID_OPTION_CONTRACT",
                user_message="That choice is no longer active.",
            )

    carts = CartStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(carts=carts, agent_sessions=Sessions()),
    )
    context = AgentRequestContext(
        "customer-1", "session-1", customer_id="customer-1",
        channel="whatsapp", request_id="request-1",
    )

    with request_context(context):
        result = tools.start_cart_item_customization(
            "item-1", contract_id="stale", contract_version=1,
        )

    assert result["error_code"] == "INVALID_OPTION_CONTRACT"
    assert carts.start_calls == []


def test_menu_presentation_role_controls_contract_proposal(monkeypatch):
    class Menu:
        customer_result_limit = 5

        def search_menu(self, *, presentation_role, **_kwargs):
            actionable = presentation_role == "selection_offer"
            return ToolResponse.ok(
                data={"items": [{"product_id": "item-1", "name": "First"}]},
                user_message="Current menu information.",
                grounding=GroundingEvidence(
                    authoritative_domains=["menu"],
                    required_next_effect="item_selected" if actionable else None,
                    offered_options=(
                        [GroundingOption(id="item-1", label="First")]
                        if actionable else []
                    ),
                ),
            )

    monkeypatch.setattr(tools, "get_services", lambda: SimpleNamespace(menu=Menu()))
    context = AgentRequestContext(
        "customer-1", "session-1", request_id="request-1", channel="whatsapp"
    )
    with request_context(context):
        offer = tools.search_menu(presentation_role="selection_offer")
        reference = tools.search_menu(presentation_role="informational_reference")

    assert offer["grounding"]["option_contract_proposal"]["required_effect"] == (
        "item_selected"
    )
    assert "option_contract_proposal" not in reference["grounding"]


class TicketStub:
    def __init__(self, response=None):
        self.response = response or ToolResponse.ok(
            data={"ticket": {"ticket_id": "TKT-20260724-A1B2C3", "status": "open"}},
            user_message="Authoritative ticket message.",
            agent={"entity": "ticket", "tracking_state": "specific_ticket"},
        )
        self.human_calls = []
        self.complaint_calls = []
        self.status_calls = []

    def create_human_assistance(self, **kwargs):
        self.human_calls.append(kwargs)
        return self.response

    def get_ticket_status(self, user_id, ticket_id=None):
        self.status_calls.append((user_id, ticket_id))
        return self.response

    def create_order_complaint(self, **kwargs):
        self.complaint_calls.append(kwargs)
        return self.response


class SupportFlowStub:
    def __init__(self, response=None):
        self.response = response or ToolResponse.ok(
            user_message="Please provide the Order ID.",
            next_action="request_order_id",
            agent={
                "entity": "pending_support",
                "pending_support_intent": "order_complaint",
                "required_input": "order_id",
            },
        )
        self.calls = []

    def handle_order_complaint(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class VerifiedSessionStub:
    def __init__(self, error=None):
        self.saved = []
        self.error = error

    def save_verified_order_context(
        self,
        customer_id,
        agent_session_id,
        *,
        order_id,
        status,
    ):
        if self.error:
            raise self.error
        self.saved.append(
            {
                "customer_id": customer_id,
                "agent_session_id": agent_session_id,
                "order_id": order_id,
                "status": status,
            }
        )


class OrderStatusStub:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get_order_status(self, user_id, order_id=None):
        self.calls.append((user_id, order_id))
        return self.response


def test_support_tool_signatures_expose_only_customer_inputs():
    assert list(inspect.signature(tools.create_human_assistance_ticket).parameters) == [
        "description"
    ]
    assert list(inspect.signature(tools.handle_order_complaint).parameters) == [
        "order_id",
        "description",
        "action",
    ]
    assert list(inspect.signature(tools.get_support_ticket_status).parameters) == [
        "ticket_id"
    ]
    assert list(inspect.signature(tools.request_human_support).parameters) == [
        "description"
    ]
    assert list(inspect.signature(tools.create_order_complaint).parameters) == [
        "order_id",
        "description",
    ]
    assert list(inspect.signature(tools.cancel_support_request).parameters) == []
    assert list(inspect.signature(tools.get_support_ticket).parameters) == [
        "ticket_id"
    ]
    action_schema = tools.handle_order_complaint.tool_spec["inputSchema"]["json"][
        "properties"
    ]["action"]
    assert action_schema["enum"] == ["continue", "cancel"]
    assert "current_message" not in repr(
        tools.create_human_assistance_ticket.tool_spec
    )
    assert "current_message" not in repr(tools.handle_order_complaint.tool_spec)


def test_semantic_order_tools_call_validated_backend_actions(monkeypatch):
    carts = CartStub()
    orders = OrderMutationStub()
    customers = CustomerStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(carts=carts, orders=orders, customers=customers),
    )
    context = AgentRequestContext(
        "user-1",
        "session-1",
        customer_id="customer-1",
        request_id="request-1",
        channel="whatsapp",
    )

    with request_context(context):
        checkout = tools.begin_checkout("CART-1")
        delivery = tools.choose_delivery("ORD-1")
        takeaway = tools.choose_takeaway("ORD-2")
        address = tools.save_order_address("ORD-3", "House 1, Street 2")
        confirmed = tools.confirm_order("ORD-4")
        cancelled = tools.cancel_order("ORD-5")

    assert checkout["data"]["order"]["order_id"] == "ORD-CHECKOUT"
    assert delivery["user_message"] == "set_delivery ok"
    assert takeaway["user_message"] == "set_takeaway ok"
    assert address["user_message"] == "save_address ok"
    assert confirmed["user_message"] == "confirm ok"
    assert cancelled["user_message"] == "cancel ok"
    assert carts.checkout_calls == [("user-1", "CART-1")]
    assert customers.address_calls == [
        (
            "customer-1",
            {
                "address_text": "House 1, Street 2",
                "label": None,
                "make_default": True,
                "channel": "whatsapp",
            },
        )
    ]
    assert orders.calls == [
        ("user-1", "ORD-1", "set_delivery", None, None),
        ("user-1", "ORD-2", "set_takeaway", None, None),
        ("user-1", "ORD-3", "save_address", "House 1, Street 2", None),
        ("user-1", "ORD-4", "confirm", None, "request-1"),
        ("user-1", "ORD-5", "cancel", None, None),
    ]


def test_semantic_support_tools_call_requested_backend_paths(monkeypatch):
    tickets = TicketStub()
    support_flow = SupportFlowStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets, support_flow=support_flow),
    )
    context = AgentRequestContext(
        "user-1",
        "session-1",
        request_id="request-1",
        current_message="I need help",
        channel="whatsapp",
    )

    with request_context(context):
        human = tools.request_human_support("Please call me")
        complaint = tools.create_order_complaint(
            order_id="ORD-1",
            description="The food was cold.",
        )
        cancelled = tools.cancel_support_request()
        status = tools.get_support_ticket("TKT-1")

    assert human["user_message"] == "Authoritative ticket message."
    assert tickets.human_calls == [{
        "user_id": "user-1",
        "session_id": "session-1",
        "description": "Please call me",
        "customer_id": None,
        "customer_name": None,
        "customer_phone": None,
        "source": "whatsapp",
        "idempotency_key": "request-1",
    }]
    assert support_flow.calls[0]["order_id"] == "ORD-1"
    assert support_flow.calls[0]["description"] == "The food was cold."
    assert support_flow.calls[0]["action"] == "continue"
    assert support_flow.calls[1]["action"] == "cancel"
    assert cancelled["next_action"] == "request_order_id"
    assert tickets.status_calls == [("user-1", "TKT-1")]
    assert status["success"] is True


@pytest.mark.parametrize(
    ("data", "agent", "expected"),
    [
        (
            {"order": {"order_id": "ORD-1", "status": "delivered"}},
            {"selected_order_id": "ORD-1"},
            ("ORD-1", "delivered"),
        ),
        (
            {"orders": [{"order_id": "ORD-2", "status": "confirmed"}]},
            {"selected_order_id": "ORD-2"},
            ("ORD-2", "confirmed"),
        ),
        (
            {
                "orders": [
                    {"order_id": "ORD-1", "status": "confirmed"},
                    {"order_id": "ORD-2", "status": "preparing"},
                ]
            },
            {"selected_order_id": None},
            None,
        ),
    ],
)
def test_order_status_persists_only_one_deterministically_selected_order(
    monkeypatch,
    data,
    agent,
    expected,
):
    response = ToolResponse.ok(
        data=data,
        user_message="Order status.",
        agent=agent,
    )
    sessions = VerifiedSessionStub()
    orders = OrderStatusStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(orders=orders, agent_sessions=sessions),
    )

    with request_context(AgentRequestContext("user-1", "session-1")):
        result = tools.get_order_status()

    assert result["success"] is True
    if expected is None:
        assert sessions.saved == []
    else:
        assert sessions.saved[0]["order_id"] == expected[0]
        assert sessions.saved[0]["status"] == expected[1]


def test_failed_order_status_does_not_persist_verified_context(monkeypatch):
    sessions = VerifiedSessionStub()
    orders = OrderStatusStub(
        ToolResponse.error(
            error_code="ORDER_NOT_FOUND",
            user_message="I couldn't find that order.",
        )
    )
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(orders=orders, agent_sessions=sessions),
    )

    with request_context(AgentRequestContext("user-1", "session-1")):
        result = tools.get_order_status("ORD-UNOWNED")

    assert result["error_code"] == "ORDER_NOT_FOUND"
    assert sessions.saved == []


def test_informational_order_status_has_no_fulfillment_contract_or_invitation(
    monkeypatch,
):
    response = ToolResponse.ok(
        data={"order": {
            "order_id": "ORD-1", "status": "awaiting_fulfillment_method",
        }},
        user_message="Order ORD-1 is awaiting fulfillment method.",
        agent={"selected_order_id": "ORD-1"},
        grounding=GroundingEvidence(authoritative_domains=["order"]),
    )
    sessions = VerifiedSessionStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(
            orders=OrderStatusStub(response), agent_sessions=sessions,
        ),
    )

    with request_context(AgentRequestContext(
        "user-1", "session-1", request_id="request-1", channel="whatsapp",
    )):
        result = tools.get_order_status(
            presentation_role="informational_reference"
        )

    grounding = result["grounding"]
    assert grounding["presentation"]["role"] == "informational_reference"
    assert grounding["offered_options"] == []
    assert "required_next_effect" not in grounding
    assert "option_contract_proposal" not in grounding
    assert "delivery or takeaway" not in result["user_message"].casefold()
    assert "delivery or takeaway" not in grounding.get(
        "exact_customer_text", ""
    ).casefold()


def test_order_status_selection_offer_creates_scoped_fulfillment_contract(
    monkeypatch,
):
    response = ToolResponse.ok(
        data={"order": {
            "order_id": "ORD-AUTHORITATIVE",
            "status": "awaiting_fulfillment_method",
        }},
        user_message="Order status is awaiting fulfillment method.",
        agent={"selected_order_id": "ORD-AUTHORITATIVE"},
        grounding=GroundingEvidence(authoritative_domains=["order"]),
    )
    sessions = VerifiedSessionStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(
            orders=OrderStatusStub(response), agent_sessions=sessions,
        ),
    )

    with request_context(AgentRequestContext(
        "user-1", "session-1", request_id="request-1", channel="whatsapp",
    )):
        result = tools.get_order_status(presentation_role="selection_offer")

    grounding = result["grounding"]
    proposal = grounding["option_contract_proposal"]
    assert grounding["required_next_effect"] == "fulfillment_saved"
    assert [option["id"] for option in grounding["offered_options"]] == [
        "set_delivery", "set_takeaway"
    ]
    assert proposal["consumer_capability"] == "update_order_flow"
    assert proposal["scope"] == {"order_id": "ORD-AUTHORITATIVE"}
    assert "delivery or takeaway" in grounding["exact_customer_text"].casefold()


@pytest.mark.parametrize(
    ("data", "agent"),
    [
        (
            {"orders": [
                {"order_id": "ORD-1", "status": "awaiting_fulfillment_method"},
                {"order_id": "ORD-2", "status": "awaiting_fulfillment_method"},
            ]},
            {"selected_order_id": None},
        ),
        (
            {"order": {"order_id": "ORD-1", "status": "preparing"}},
            {"selected_order_id": "ORD-1"},
        ),
    ],
)
def test_ineligible_order_status_selection_offer_has_no_actionable_contract(
    monkeypatch, data, agent,
):
    response = ToolResponse.ok(
        data=data,
        user_message="Verified order status.",
        agent=agent,
        grounding=GroundingEvidence(authoritative_domains=["order"]),
    )
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(
            orders=OrderStatusStub(response),
            agent_sessions=VerifiedSessionStub(),
        ),
    )

    with request_context(AgentRequestContext(
        "user-1", "session-1", request_id="request-1", channel="whatsapp",
    )):
        result = tools.get_order_status(presentation_role="selection_offer")

    grounding = result["grounding"]
    assert "option_contract_proposal" not in grounding
    assert "required_next_effect" not in grounding
    assert grounding["offered_options"] == []
    assert "delivery or takeaway" not in grounding.get(
        "exact_customer_text", ""
    ).casefold()


def test_web_order_status_preserves_prechange_response_without_typed_contract(
    monkeypatch,
):
    response = ToolResponse.ok(
        data={"order": {
            "order_id": "ORD-1", "status": "awaiting_fulfillment_method",
        }},
        user_message="Order ORD-1 is awaiting fulfillment method.",
        next_action="present_order_status",
        agent={
            "selected_order_id": "ORD-1",
            "instruction": "Continue from the returned next_action.",
            "required_input": "fulfillment_method",
        },
        grounding=GroundingEvidence(authoritative_domains=["order"]),
    )
    expected = response.model_dump(exclude_none=True)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(
            orders=OrderStatusStub(response),
            agent_sessions=VerifiedSessionStub(),
        ),
    )

    with request_context(AgentRequestContext(
        "user-1", "session-1", request_id="request-1", channel="web",
    )):
        result = tools.get_order_status()

    assert result == expected
    assert result["next_action"] == "present_order_status"
    assert result["agent"]["instruction"] == (
        "Continue from the returned next_action."
    )
    assert "option_contract_proposal" not in result["grounding"]


def test_verified_context_persistence_failure_keeps_successful_order_status(
    monkeypatch,
    caplog,
):
    response = ToolResponse.ok(
        data={"order": {"order_id": "ORD-1", "status": "delivered"}},
        user_message="Your order has been delivered.",
        agent={"selected_order_id": "ORD-1"},
    )
    sessions = VerifiedSessionStub(RuntimeError("private persistence detail"))
    orders = OrderStatusStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(orders=orders, agent_sessions=sessions),
    )
    context = AgentRequestContext(
        "user-1",
        "session-1",
        current_message="Where is my order?",
        customer_phone="+10000000000",
    )

    with caplog.at_level(logging.ERROR), request_context(context):
        result = tools.get_order_status("ORD-1")

    assert result == response.model_dump(exclude_none=True)
    failure = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "verified_order_context_persistence_failed"
    )
    assert failure.exception_type == "RuntimeError"
    assert failure.actor_id == "user-1"
    assert failure.agent_session_id == "session-1"
    assert not hasattr(failure, "customer_message")
    assert "private persistence detail" not in failure.getMessage()
    assert "Where is my order?" not in caplog.text
    assert "+10000000000" not in caplog.text


def test_verified_status_context_links_explicit_order_complaint(
    monkeypatch,
):
    now = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)
    session_repository = MemoryAgentSessionRepository()
    session_repository.data["session-1"] = {
        "PK": "CUSTOMER#user-1",
        "SK": "SESSION#session-1",
        "agent_session_id": "session-1",
        "customer_id": "user-1",
    }
    sessions = AgentSessionService(
        session_repository,
        SimpleNamespace(),
        SimpleNamespace(agent_session_ttl_hours=24),
        clock=lambda: now,
    )
    status = OrderStatusStub(ToolResponse.ok(
        data={"order": {"order_id": "ORD-1", "status": "delivered"}},
        user_message="Order status.",
        agent={"selected_order_id": "ORD-1"},
    ))
    orders = MemoryOrderRepository()
    orders.data["ORD-1"] = {
        "order_id": "ORD-1",
        "user_id": "user-1",
        "status": "delivered",
    }
    tickets = TicketStub(ToolResponse.ok(
        data={
            "ticket": {
                "ticket_id": "TKT-20260724-C0FFEE",
                "ticket_type": "order_complaint",
                "order_id": "ORD-1",
                "order_status_snapshot": "delivered",
            }
        },
        user_message="Exact complaint response.",
    ))
    flow = SupportFlowService(sessions, tickets, orders)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(
            orders=status,
            agent_sessions=sessions,
            tickets=tickets,
            support_flow=flow,
        ),
    )

    with request_context(AgentRequestContext("user-1", "session-1")):
        tools.get_order_status("ORD-1")

    assert session_repository.data["session-1"] == {
        "PK": "CUSTOMER#user-1",
        "SK": "SESSION#session-1",
        "agent_session_id": "session-1",
        "customer_id": "user-1",
        "verified_order_id": "ORD-1",
        "verified_order_status": "delivered",
        "verified_order_at": "2026-07-24T10:00:00+00:00",
    }

    message = "My delivered order was missing two dips."
    with request_context(AgentRequestContext(
        "user-1",
        "session-1",
        request_id="request-1",
        current_message=message,
    )):
        result = tools.create_order_complaint(description=message)

    assert tickets.human_calls == []
    assert tickets.complaint_calls[0]["order_id"] == "ORD-1"
    assert tickets.complaint_calls[0]["description"] == message
    assert result["data"]["ticket"]["order_status_snapshot"] == "delivered"
    assert result["user_message"] == "Exact complaint response."


def test_support_tool_descriptions_distinguish_complaints_from_generic_help():
    complaint_description = tools.create_order_complaint.tool_spec[
        "description"
    ].lower()
    human_description = tools.request_human_support.tool_spec[
        "description"
    ].lower()

    assert "order complaint" in complaint_description
    assert "backend validates ownership" in complaint_description
    assert "human-support ticket" in human_description
    assert "non-order-problem assistance" in human_description


def test_human_assistance_tool_uses_trusted_context_and_records_write(monkeypatch):
    tickets = TicketStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )
    context = AgentRequestContext(
        "trusted-user",
        "trusted-session",
        customer_id="customer-1",
        customer_name="Ava",
        customer_phone="+1000000",
        channel="whatsapp",
        request_id="trusted-request",
    )

    with request_context(context):
        result = tools.create_human_assistance_ticket(
            description="  Please call me.\nToday.  "
        )

    assert tickets.human_calls == [{
        "user_id": "trusted-user",
        "session_id": "trusted-session",
        "description": "  Please call me.\nToday.  ",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+1000000",
        "source": "whatsapp",
        "idempotency_key": "trusted-request",
    }]
    assert result["user_message"] == "Authoritative ticket message."
    assert result["data"]["ticket"]["ticket_id"] == "TKT-20260724-A1B2C3"
    assert "trusted-request" not in repr(result)
    assert context.tool_calls[-1]["tool_name"] == "create_human_assistance_ticket"
    assert context.tool_calls[-1]["is_write"] is True


def test_create_order_complaint_uses_explicit_agent_supplied_details(monkeypatch):
    tickets = TicketStub()
    authoritative = ToolResponse.ok(
        data={
            "ticket": {
                "ticket_id": "TKT-20260724-C0FFEE",
                "ticket_type": "order_complaint",
                "order_id": "ORD-1",
            }
        },
        user_message="Exact complaint response.",
    )
    flow = SupportFlowStub(authoritative)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets, support_flow=flow),
    )

    with request_context(AgentRequestContext(
        "trusted-user",
        "trusted-session",
        request_id="trusted-request",
        current_message="My order was missing sauce and dip.",
    )):
        result = tools.create_order_complaint(
            order_id="ORD-1",
            description="My order was missing sauce and dip.",
        )

    assert tickets.human_calls == []
    assert flow.calls[0]["order_id"] == "ORD-1"
    assert flow.calls[0]["description"] == "My order was missing sauce and dip."
    assert flow.calls[0]["request_id"] == "trusted-request"
    assert result["user_message"] == "Exact complaint response."
    assert result["data"]["ticket"]["ticket_type"] == "order_complaint"


def test_customer_ticket_tool_results_and_recording_preserve_safe_boundary(
    monkeypatch,
    caplog,
):
    safe_ticket = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "ticket_type": "human_assistance",
        "status": "open",
        "status_label": "Open",
        "priority": "normal",
        "created_at": "2026-07-24T10:00:00+00:00",
        "updated_at": "2026-07-24T10:00:00+00:00",
        "next_action": "await_support_contact",
    }
    response = ToolResponse.ok(
        data={"ticket": safe_ticket},
        user_message="Authoritative ticket message.",
        next_action="await_support_contact",
        agent={"entity": "ticket", "ticket_status": "open"},
    )
    tickets = TicketStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )
    context = AgentRequestContext(
        "trusted-user",
        "trusted-session",
        request_id="trusted-request",
    )

    with caplog.at_level(logging.INFO), request_context(context):
        result = tools.create_human_assistance_ticket(
            description="private description"
        )

    assert result["data"]["ticket"] == safe_ticket
    assert result["user_message"] == "Authoritative ticket message."
    assert not {
        "description",
        "request_id",
        "idempotency_key",
        "idempotency_hash",
        "session_id",
        "version",
        "admin_notes",
        "status_history",
    } & result["data"]["ticket"].keys()
    assert context.tool_calls[-1]["result"] == result
    completed = next(
        record
        for record in caplog.records
        if record.getMessage() == "Agent tool call completed"
    )
    assert not hasattr(completed, "agent_session_id")


def test_human_assistance_missing_request_id_is_deterministic_and_recorded(
    monkeypatch,
):
    tickets = TicketStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )
    context = AgentRequestContext("trusted-user", "trusted-session")

    with request_context(context):
        result = tools.create_human_assistance_ticket()

    assert result["success"] is False
    assert result["error_code"] == "REQUEST_ID_REQUIRED"
    assert result["retryable"] is False
    assert tickets.human_calls == []
    assert context.tool_calls[-1]["error_code"] == "REQUEST_ID_REQUIRED"


@pytest.mark.parametrize(
    ("user_id", "session_id", "error_code"),
    [
        ("", "trusted-session", "USER_ID_REQUIRED"),
        ("trusted-user", "", "SESSION_ID_REQUIRED"),
    ],
)
def test_human_assistance_requires_trusted_identity(
    monkeypatch,
    user_id,
    session_id,
    error_code,
):
    tickets = TicketStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )
    context = AgentRequestContext(
        user_id,
        session_id,
        request_id="trusted-request",
    )

    with request_context(context):
        result = tools.create_human_assistance_ticket()

    assert result["error_code"] == error_code
    assert tickets.human_calls == []


def test_human_assistance_preserves_reuse_response_without_description(monkeypatch):
    response = ToolResponse.ok(
        data={"ticket": {"ticket_id": "TKT-20260724-A1B2C3", "status": "open"}},
        user_message="Your existing support request is still active.",
        agent={"entity": "ticket", "ticket_status": "open"},
    )
    tickets = TicketStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )
    context = AgentRequestContext(
        "trusted-user",
        "trusted-session",
        request_id="trusted-request",
    )

    with request_context(context):
        result = tools.create_human_assistance_ticket()

    assert tickets.human_calls[0]["description"] is None
    assert result["user_message"] == response.user_message


def test_complaint_tool_passes_only_context_and_customer_inputs(monkeypatch):
    flow = SupportFlowStub()
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(support_flow=flow),
    )
    context = AgentRequestContext(
        "trusted-user",
        "trusted-session",
        customer_id="customer-1",
        customer_name="Ava",
        customer_phone="+1000000",
        channel="web",
        request_id="trusted-request",
    )

    with request_context(context):
        result = tools.handle_order_complaint(
            order_id="ORD-1",
            description="  Cold food.\nMissing drink.  ",
            action="continue",
        )

    assert flow.calls == [{
        "user_id": "trusted-user",
        "agent_session_id": "trusted-session",
        "request_id": "trusted-request",
        "order_id": "ORD-1",
        "description": "  Cold food.\nMissing drink.  ",
        "action": "continue",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+1000000",
        "source": "web",
    }]
    assert result["user_message"] == "Please provide the Order ID."
    assert result["next_action"] == "request_order_id"
    assert "trusted-request" not in repr(result)
    assert context.tool_calls[-1]["tool_name"] == "handle_order_complaint"
    assert context.tool_calls[-1]["is_write"] is True


def test_complaint_tool_preserves_service_errors_and_cancellation(monkeypatch):
    response = ToolResponse.error(
        error_code="SUPPORT_STATE_CONFLICT",
        user_message="The complaint details changed while they were being saved.",
        retryable=True,
    )
    flow = SupportFlowStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(support_flow=flow),
    )
    context = AgentRequestContext(
        "trusted-user",
        "trusted-session",
        request_id=None,
    )

    with request_context(context):
        result = tools.handle_order_complaint(action="cancel")

    assert flow.calls[0]["request_id"] is None
    assert flow.calls[0]["action"] == "cancel"
    assert result["error_code"] == "SUPPORT_STATE_CONFLICT"
    assert result["user_message"] == response.user_message


def test_complaint_tool_preserves_missing_request_id_response(monkeypatch):
    response = ToolResponse.error(
        error_code="REQUEST_ID_REQUIRED",
        user_message="A trusted request ID is required.",
    )
    flow = SupportFlowStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(support_flow=flow),
    )
    context = AgentRequestContext(
        "trusted-user",
        "trusted-session",
        request_id=None,
    )

    with request_context(context):
        result = tools.handle_order_complaint()

    assert result["error_code"] == "REQUEST_ID_REQUIRED"
    assert result["user_message"] == "A trusted request ID is required."


def test_complaint_tool_drives_persisted_multi_turn_flow(monkeypatch):
    now = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)
    sessions_repository = MemoryAgentSessionRepository()
    sessions_repository.data["trusted-session"] = {
        "PK": "CUSTOMER#trusted-user",
        "SK": "SESSION#trusted-session",
        "agent_session_id": "trusted-session",
        "customer_id": "trusted-user",
        "unrelated": "preserved",
    }
    sessions = AgentSessionService(
        sessions_repository,
        SimpleNamespace(),
        SimpleNamespace(agent_session_ttl_hours=24),
        clock=lambda: now,
    )
    orders = MemoryOrderRepository()
    orders.data["ORD-1"] = {
        "order_id": "ORD-1",
        "user_id": "trusted-user",
        "status": "confirmed",
    }
    tickets = TicketStub()
    flow = SupportFlowService(sessions, tickets, orders)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(support_flow=flow),
    )

    with request_context(AgentRequestContext(
        "trusted-user", "trusted-session", request_id="request-1"
    )):
        first = tools.handle_order_complaint()
    with request_context(AgentRequestContext(
        "trusted-user", "trusted-session", request_id="request-2"
    )):
        second = tools.handle_order_complaint(order_id="ORD-1")
    with request_context(AgentRequestContext(
        "trusted-user", "trusted-session", request_id="request-3"
    )):
        third = tools.handle_order_complaint(
            description="  Cold food.\nMissing drink.  "
        )

    assert first["next_action"] == "request_order_id"
    assert second["next_action"] == "request_complaint_description"
    assert third["user_message"] == "Authoritative ticket message."
    assert tickets.complaint_calls == [{
        "user_id": "trusted-user",
        "order_id": "ORD-1",
        "description": "  Cold food.\nMissing drink.  ",
        "session_id": "trusted-session",
        "source": "web",
        "idempotency_key": "request-3",
    }]
    assert sessions_repository.data["trusted-session"]["unrelated"] == "preserved"
    assert not any(
        key.startswith("pending_")
        for key in sessions_repository.data["trusted-session"]
    )


def test_complaint_tool_result_excludes_description_and_internal_fields(
    monkeypatch,
):
    safe_ticket = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "ticket_type": "order_complaint",
        "status": "open",
        "status_label": "Open",
        "priority": "normal",
        "created_at": "2026-07-24T10:00:00+00:00",
        "updated_at": "2026-07-24T10:00:00+00:00",
        "next_action": "await_support_contact",
        "order_id": "ORD-1",
    }
    flow = SupportFlowStub(ToolResponse.ok(
        data={"ticket": safe_ticket},
        user_message="Authoritative complaint message.",
        next_action="await_support_contact",
        agent={"entity": "ticket", "ticket_status": "open"},
    ))
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(support_flow=flow),
    )

    with request_context(AgentRequestContext(
        "trusted-user",
        "trusted-session",
        request_id="trusted-request",
    )):
        result = tools.handle_order_complaint(
            order_id="ORD-1",
            description="private complaint",
        )

    assert result["data"]["ticket"] == safe_ticket
    assert "private complaint" not in repr(result)
    assert "trusted-request" not in repr(result)


def test_ticket_status_tool_result_excludes_internal_ticket_fields(monkeypatch):
    safe_ticket = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "ticket_type": "human_assistance",
        "status": "open",
        "status_label": "Open",
        "priority": "normal",
        "created_at": "2026-07-24T10:00:00+00:00",
        "updated_at": "2026-07-24T10:00:00+00:00",
        "next_action": "await_support_contact",
    }
    tickets = TicketStub(ToolResponse.ok(
        data={"ticket": safe_ticket},
        user_message="Exact ticket status message.",
        agent={"entity": "ticket", "tracking_state": "specific_ticket"},
    ))
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )

    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.get_support_ticket_status(
            ticket_id="TKT-20260724-A1B2C3"
        )

    assert result["data"]["ticket"] == safe_ticket
    assert result["user_message"] == "Exact ticket status message."


@pytest.mark.parametrize(
    ("tracking_state", "ticket_id"),
    [
        ("specific_ticket", "TKT-20260724-A1B2C3"),
        ("single_active_ticket", None),
        ("multiple_active_tickets", None),
        ("no_active_tickets", None),
    ],
)
def test_ticket_status_tool_preserves_tracking_response(
    monkeypatch,
    tracking_state,
    ticket_id,
):
    response = ToolResponse.ok(
        data={"tickets": []},
        user_message=f"Exact {tracking_state} message.",
        next_action="provide_ticket_id",
        agent={"entity": "tickets", "tracking_state": tracking_state},
    )
    tickets = TicketStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )
    context = AgentRequestContext("trusted-user", "trusted-session")

    with request_context(context):
        result = tools.get_support_ticket_status(ticket_id=ticket_id)

    assert tickets.status_calls == [("trusted-user", ticket_id)]
    assert result["agent"]["tracking_state"] == tracking_state
    assert result["user_message"] == response.user_message
    assert context.tool_calls[-1]["is_write"] is False


def test_ticket_status_tool_preserves_not_found(monkeypatch):
    response = ToolResponse.error(
        error_code="TICKET_NOT_FOUND",
        user_message="I couldn't find that support ticket.",
    )
    tickets = TicketStub(response)
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )

    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.get_support_ticket_status(
            ticket_id="TKT-20260724-FFFFFF"
        )

    assert result["error_code"] == "TICKET_NOT_FOUND"
    assert result["user_message"] == response.user_message


def test_menu_link_injects_trusted_context(monkeypatch):
    container = SimpleNamespace(menu=MenuStub(), menu_sessions=SessionStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.create_menu_session_link(item_id="dynamic-item")
    assert result["data"] == {
        "user_id": "trusted-user", "session_id": "trusted-session",
        "item_id": "dynamic-item", "customer_id": "trusted-user"
    }


def test_search_menu_tool_caps_agent_requested_result_limit(monkeypatch):
    container = SimpleNamespace(menu=MenuStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    result = tools.search_menu(query="recommend", max_results=12)

    assert result["data"]["limit"] == 5


def test_search_menu_tool_uses_configured_customer_limit(monkeypatch):
    menu = MenuStub()
    menu.customer_result_limit = 3
    monkeypatch.setattr(tools, "get_services", lambda: SimpleNamespace(menu=menu))

    result = tools.search_menu(query="recommend", max_results=12)

    assert result["data"]["limit"] == 3


def test_get_active_cart_uses_trusted_user_and_session(monkeypatch):
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.get_active_cart()
    assert result["data"]["cart"] == {
        "user_id": "trusted-user",
        "session_id": "trusted-session",
    }


def test_customer_tools_use_trusted_customer_context(monkeypatch):
    container = SimpleNamespace(customers=CustomerStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext(
        "trusted-user", "trusted-session", customer_id="customer-1", channel="web"
    )):
        profile = tools.get_customer_profile()
        updated = tools.update_customer_profile(display_name="Ava", phone_number="+923001234567")
        address = tools.save_customer_address(
            address_text="House 1, Street 2", label="Home", make_default=True
        )
    assert profile["data"]["customer"]["customer_id"] == "customer-1"
    assert updated["data"]["customer"]["display_name"] == "Ava"
    assert address["data"]["address"]["address_text"] == "House 1, Street 2"
    assert address["data"]["address"]["channel"] == "web"


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

def test_save_customization_choice_fetches_upsells_after_final_required_choice(
    monkeypatch,
):
    calls = []
    upsell_prompt = (
        "Would you like to add anything?\n"
        "\n"
        "1. Ranch Dip - PKR 100\n"
        "2. Lava Cake - 1 Pc - PKR 450\n"
        "\n"
        "You can choose one add-on or proceed to checkout."
    )
    upsell_items = [
        {
            "product_id": "ranch-dip",
            "name": "Ranch Dip",
            "display_label": "Ranch Dip - PKR 100",
        },
        {
            "product_id": "lava-cake",
            "name": "Lava Cake - 1 Pc",
            "display_label": "Lava Cake - 1 Pc - PKR 450",
        },
    ]

    class CartStub:
        def save_choice(
            self,
            user_id,
            cart_item_id,
            field_name,
            selected_option_id,
        ):
            calls.append(
                (
                    "save_choice",
                    user_id,
                    cart_item_id,
                    field_name,
                    selected_option_id,
                )
            )
            return ToolResponse.ok(
                data={
                    "cart_id": "CART-1",
                    "status": "item_ready",
                },
                user_message=(
                    "All required item choices are complete."
                ),
                next_action="offer_upsell",
                agent={
                    "entity": "cart",
                    "cart_id": "CART-1",
                    "cart_status": "item_ready",
                    "next_action": "offer_upsell",
                },
            )

        def handle_upsell(
            self,
            user_id,
            cart_id,
            action,
            item_id=None,
            quantity=1,
        ):
            calls.append(
                (
                    "handle_upsell",
                    user_id,
                    cart_id,
                    action,
                    item_id,
                    quantity,
                )
            )
            return ToolResponse.ok(
                data={
                    "cart_id": cart_id,
                    "status": "awaiting_upsell_decision",
                    "upsell_items": upsell_items,
                    "upsell_prompt": upsell_prompt,
                },
                user_message=upsell_prompt,
                next_action="choose_upsell",
                agent={
                    "entity": "cart",
                    "cart_id": cart_id,
                    "cart_status": "awaiting_upsell_decision",
                    "next_action": "choose_upsell",
                    "upsell_items": upsell_items,
                    "upsell_prompt": upsell_prompt,
                },
            )

    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: container,
    )

    with request_context(
        AgentRequestContext(
            "trusted-user",
            "trusted-session",
        )
    ):
        result = tools.save_customization_choice(
            cart_item_id="CARTITEM-1",
            field_name="pizza-crust",
            selected_option_id="regular",
        )

    assert calls == [
        (
            "save_choice",
            "trusted-user",
            "CARTITEM-1",
            "pizza-crust",
            "regular",
        ),
        (
            "handle_upsell",
            "trusted-user",
            "CART-1",
            "get_options",
            None,
            1,
        ),
    ]
    assert result["success"] is True
    assert result["next_action"] == "choose_upsell"
    assert result["user_message"] == upsell_prompt
    assert result["data"]["upsell_prompt"] == upsell_prompt
    assert result["agent"]["upsell_prompt"] == upsell_prompt
    assert result["agent"]["upsell_items"] == upsell_items


def test_save_customization_choice_preserves_non_upsell_response(
    monkeypatch,
):
    class CartStub:
        def save_choice(
            self,
            user_id,
            cart_item_id,
            field_name,
            selected_option_id,
        ):
            return ToolResponse.ok(
                data={
                    "cart_id": "CART-1",
                    "cart_item_id": cart_item_id,
                    "field_name": "pizza-crust",
                },
                user_message="Choose a crust.",
                next_action="ask_customization_choice",
                agent={
                    "entity": "cart",
                    "cart_id": "CART-1",
                    "next_action": "ask_customization_choice",
                    "active_choice": {
                        "field_name": "pizza-crust",
                    },
                },
            )

        def handle_upsell(self, *args, **kwargs):
            raise AssertionError(
                "Upsells must not be fetched before "
                "required choices are complete."
            )

    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(
        tools,
        "get_services",
        lambda: container,
    )

    with request_context(
        AgentRequestContext(
            "trusted-user",
            "trusted-session",
        )
    ):
        result = tools.save_customization_choice(
            cart_item_id="CARTITEM-1",
            field_name="pizza-size",
            selected_option_id="medium",
        )

    assert result["success"] is True
    assert result["next_action"] == "ask_customization_choice"
    assert result["user_message"] == "Choose a crust."
    assert (
        result["agent"]["active_choice"]["field_name"]
        == "pizza-crust"
    )
