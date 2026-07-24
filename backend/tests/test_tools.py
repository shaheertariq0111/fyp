import inspect
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fakes import MemoryAgentSessionRepository, MemoryOrderRepository
from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.models.tool_responses import ToolResponse
from src.services.agent_session_service import AgentSessionService
from src.services.support_flow_service import SupportFlowService


class MenuStub:
    def search_menu(self, **kwargs):
        return ToolResponse.ok(data=kwargs, user_message="ok")

    def get_menu_item(self, item_id):
        return ToolResponse.ok(data={"item_id": item_id}, user_message="ok")


class SessionStub:
    def create_link(self, user_id, session_id, item_id, customer_id=None):
        return ToolResponse.ok(data={"user_id": user_id, "session_id": session_id,
                                     "item_id": item_id, "customer_id": customer_id}, user_message="ok")


class CartStub:
    def get_active_cart(self, user_id, session_id):
        return ToolResponse.ok(
            data={"cart": {"user_id": user_id, "session_id": session_id}},
            user_message="cart",
        )


class CustomerStub:
    def get_profile(self, customer_id):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id}}, user_message="ok")

    def update_profile(self, customer_id, **kwargs):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id, **kwargs}},
                               user_message="ok")

    def save_address(self, customer_id, **kwargs):
        return ToolResponse.ok(data={"customer": {"customer_id": customer_id},
                                     "address": kwargs},
                               user_message="ok")


def test_mvp_tools_include_active_cart_lookup():
    assert len(tools.MVP_TOOLS) == 18
    assert tools.get_active_cart in tools.MVP_TOOLS
    assert tools.get_customer_profile in tools.MVP_TOOLS
    assert tools.update_customer_profile in tools.MVP_TOOLS
    assert tools.save_customer_address in tools.MVP_TOOLS
    assert tools.create_human_assistance_ticket in tools.MVP_TOOLS
    assert tools.handle_order_complaint in tools.MVP_TOOLS
    assert tools.get_support_ticket_status in tools.MVP_TOOLS
    assert "create_human_assistance_ticket" in tools.WRITE_TOOLS
    assert "handle_order_complaint" in tools.WRITE_TOOLS
    assert "get_support_ticket_status" not in tools.WRITE_TOOLS


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
    action_schema = tools.handle_order_complaint.tool_spec["inputSchema"]["json"][
        "properties"
    ]["action"]
    assert action_schema["enum"] == ["continue", "cancel"]


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
            cart_item_id,
            field_name,
            selected_option_id,
        ):
            calls.append(
                (
                    "save_choice",
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
            cart_id,
            action,
            item_id=None,
            quantity=1,
        ):
            calls.append(
                (
                    "handle_upsell",
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
            "CARTITEM-1",
            "pizza-crust",
            "regular",
        ),
        (
            "handle_upsell",
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
