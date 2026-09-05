import inspect
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fakes import MemoryAgentSessionRepository, MemoryOrderRepository
from src.agent import tools
from src.agent.context import AgentRequestContext, request_context
from src.agent.response_grounding import ground_agent_response
from src.models.tool_responses import ToolResponse
from src.services.agent_session_service import AgentSessionService
from src.services.support_flow_service import SupportFlowService


class MenuStub:
    customer_result_limit = 5

    def search_menu(self, **kwargs):
        return ToolResponse.ok(data=kwargs, user_message="ok")

    def get_menu_item(self, item_id):
        return ToolResponse.ok(data={"item_id": item_id}, user_message="ok")

    def list_menu_categories(self, limit=None):
        return ToolResponse.ok(data={"limit": limit}, user_message="ok")


class SessionStub:
    def create_link(self, user_id, session_id, item_id, customer_id=None):
        return ToolResponse.ok(data={"user_id": user_id, "session_id": session_id,
                                     "item_id": item_id, "customer_id": customer_id}, user_message="ok")


class CartStub:
    def __init__(self):
        self.checkout_calls = []
        self.discard_calls = []

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

    def discard_active_cart(self, user_id, session_id):
        self.discard_calls.append((user_id, session_id))
        return ToolResponse.ok(
            data={"discarded": True},
            user_message="discarded",
            grounding={"transactional_effects": ["cart_cancelled"]},
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
    assert len(tools.MVP_TOOLS) == 32
    assert tools.list_menu_categories in tools.MVP_TOOLS
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
    assert tools.discard_active_cart in tools.MVP_TOOLS
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
    assert "discard_active_cart" in tools.WRITE_TOOLS
    assert "create_human_assistance_ticket" in tools.WRITE_TOOLS
    assert "handle_order_complaint" in tools.WRITE_TOOLS
    assert "request_human_support" in tools.WRITE_TOOLS
    assert "create_order_complaint" in tools.WRITE_TOOLS
    assert "cancel_support_request" in tools.WRITE_TOOLS
    assert "get_support_ticket_status" not in tools.WRITE_TOOLS
    assert "get_support_ticket" not in tools.WRITE_TOOLS


def test_get_active_cart_description_preserves_status_reads_without_rediscovery():
    description = tools.get_active_cart.tool_spec["description"]

    assert "cart contents or status" in description
    assert "current authoritative continuation" in description
    assert "pending transactional choice" in description


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


def test_list_menu_categories_tool_uses_configured_customer_limit(monkeypatch):
    menu = MenuStub()
    menu.customer_result_limit = 4
    monkeypatch.setattr(tools, "get_services", lambda: SimpleNamespace(menu=menu))

    result = tools.list_menu_categories(max_results=12)

    assert result["data"]["limit"] == 4


def test_search_menu_tool_forwards_exclusions_and_records_read_call(
    monkeypatch,
    caplog,
):
    menu = MenuStub()
    monkeypatch.setattr(tools, "get_services", lambda: SimpleNamespace(menu=menu))
    context = AgentRequestContext(
        "trusted-user",
        "trusted-session",
        channel="whatsapp",
    )

    with caplog.at_level("INFO"), request_context(context):
        result = tools.search_menu(
            query="recommend",
            exclude_product_ids=["item-1", "item-2"],
        )

    assert result["data"]["exclude_product_ids"] == ["item-1", "item-2"]
    assert context.tool_calls[-1] == {
        "tool_name": "search_menu",
        "success": True,
        "is_write": False,
        "result": result,
        "error_code": None,
    }
    completed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "agent_tool_completed"
        and getattr(record, "tool_name", None) == "search_menu"
    )
    assert completed.tool_success is True
    assert completed.is_write is False


def test_get_active_cart_uses_trusted_user_and_session(monkeypatch):
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.get_active_cart()
    assert result["data"]["cart"] == {
        "user_id": "trusted-user",
        "session_id": "trusted-session",
    }


def test_discard_active_cart_uses_trusted_user_and_session(monkeypatch):
    carts = CartStub()
    container = SimpleNamespace(carts=carts)
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with request_context(AgentRequestContext("trusted-user", "trusted-session")):
        result = tools.discard_active_cart()

    assert result["data"]["discarded"] is True
    assert carts.discard_calls == [("trusted-user", "trusted-session")]


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


def test_repeated_identical_read_is_bounded_without_fabricating_progress(monkeypatch):
    """Regression: production looped get_active_cart 36 times until timeout."""
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    context = AgentRequestContext("trusted-user", "trusted-session")

    results = []
    with request_context(context):
        for _ in range(6):
            results.append(tools.get_active_cart())

    successes = [item for item in results if item.get("success")]
    guarded = [
        item
        for item in results
        if item.get("error_code") == tools.REPEATED_READ_ERROR_CODE
    ]

    assert len(successes) == tools.REPEATED_READ_LIMIT
    assert guarded, "identical repeated reads must eventually be stopped"
    assert all(item["success"] is True for item in results[:tools.REPEATED_READ_LIMIT])
    assert results[tools.REPEATED_READ_LIMIT]["success"] is False
    assert (
        results[tools.REPEATED_READ_LIMIT]["error_code"]
        == tools.REPEATED_READ_ERROR_CODE
    )
    # The guard reports lack of progress; it never invents a successful effect.
    for item in guarded:
        assert item["success"] is False
        assert item.get("data", {}) == {}
        assert not item.get("grounding", {}).get("transactional_effects")
        assert tools.REPEATED_READ_MESSAGE not in item["user_message"]
        assert item["agent"]["instruction"] == tools.REPEATED_READ_MESSAGE


def test_repeated_read_control_instruction_cannot_become_customer_text(monkeypatch):
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)
    context = AgentRequestContext("trusted-user", "trusted-session")

    with request_context(context):
        for _ in range(tools.REPEATED_READ_LIMIT + 1):
            tools.get_active_cart()

    guarded_call = context.tool_calls[-1]
    grounded = ground_agent_response(
        text="Internal model draft.",
        tool_calls=[guarded_call],
    )

    assert guarded_call["error_code"] == tools.REPEATED_READ_ERROR_CODE
    assert tools.REPEATED_READ_MESSAGE not in guarded_call["result"]["user_message"]
    assert (
        guarded_call["result"]["agent"]["instruction"]
        == tools.REPEATED_READ_MESSAGE
    )
    assert tools.REPEATED_READ_MESSAGE not in grounded.text
    assert grounded.text == guarded_call["result"]["user_message"]
    assert grounded.source == "failed_read"


def test_repeated_read_guard_resets_when_authoritative_state_changes(monkeypatch):
    class ChangingCart:
        def __init__(self):
            self.calls = 0

        def get_active_cart(self, user_id, session_id):
            self.calls += 1
            return ToolResponse.ok(
                data={"cart": {"step": self.calls}},
                user_message="cart",
            )

    container = SimpleNamespace(carts=ChangingCart())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with request_context(AgentRequestContext("user-1", "session-1")):
        results = [tools.get_active_cart() for _ in range(6)]

    assert all(item["success"] for item in results)


def test_repeated_write_calls_are_not_blocked_by_the_read_guard(monkeypatch):
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with request_context(AgentRequestContext("user-1", "session-1")):
        results = [tools.begin_checkout("cart-1") for _ in range(6)]

    assert all(item["success"] for item in results)


def test_repeated_read_identity_is_scoped_by_arguments(monkeypatch):
    """Different arguments must not share a repetition counter."""

    class SameResultTickets:
        def get_ticket_status(self, user_id, ticket_id):
            # Deliberately identical payloads for different arguments.
            return ToolResponse.ok(data={}, user_message="same")

    container = SimpleNamespace(tickets=SameResultTickets())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with request_context(AgentRequestContext("user-1", "session-1")):
        results = [
            tools.get_support_ticket_status(f"ticket-{index}")
            for index in range(6)
        ]

    assert all(item["success"] for item in results)


def test_distinct_read_tools_do_not_share_a_counter(monkeypatch):
    class SameResultServices:
        def get_ticket_status(self, user_id, ticket_id):
            return ToolResponse.ok(data={}, user_message="same")

    container = SimpleNamespace(tickets=SameResultServices())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with request_context(AgentRequestContext("user-1", "session-1")):
        results = [
            tools.get_support_ticket_status("ticket-1"),
            tools.get_support_ticket("ticket-1"),
        ] * 3

    assert all(item["success"] for item in results)


def test_guard_keeps_blocking_and_never_returns_to_success(monkeypatch):
    """After the guard fires it must not let the loop resume until timeout."""
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with request_context(AgentRequestContext("user-1", "session-1")):
        results = [tools.get_active_cart() for _ in range(30)]

    tail = results[tools.REPEATED_READ_LIMIT:]
    assert tail, "expected calls beyond the limit"
    assert all(
        item["success"] is False
        and item["error_code"] == tools.REPEATED_READ_ERROR_CODE
        for item in tail
    )


def test_guard_state_is_per_invocation_not_global(monkeypatch):
    container = SimpleNamespace(carts=CartStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with request_context(AgentRequestContext("user-1", "session-1")):
        first = [tools.get_active_cart() for _ in range(6)]
    with request_context(AgentRequestContext("user-1", "session-1")):
        second = [tools.get_active_cart() for _ in range(3)]

    assert any(item["success"] is False for item in first)
    assert all(item["success"] for item in second)


class OfferingMenuStub:
    customer_result_limit = 5

    def search_menu(self, **kwargs):
        from src.models.tool_responses import GroundingEvidence, GroundingOption

        return ToolResponse.ok(
            data={"items": []},
            user_message="found",
            grounding=GroundingEvidence(
                authoritative_domains=["menu"],
                offered_options=[GroundingOption(id="product-a", label="A")],
            ),
        )


def test_offer_persistence_failure_keeps_the_search_successful(monkeypatch, caplog):
    class FailingSessions:
        def save_menu_offer_context(self, *args, **kwargs):
            raise RuntimeError("session store unavailable")

    container = SimpleNamespace(
        menu=OfferingMenuStub(),
        agent_sessions=FailingSessions(),
    )
    monkeypatch.setattr(tools, "get_services", lambda: container)

    with caplog.at_level(logging.ERROR), request_context(
        AgentRequestContext("user-1", "session-1")
    ):
        result = tools.search_menu(query="pizza")

    assert result["success"] is True
    assert result.get("error_code") is None
    # The failure must stay visible rather than being silently swallowed.
    assert any(
        record.__dict__.get("event") == "menu_offer_context_persistence_failed"
        for record in caplog.records
    )


def test_missing_request_context_does_not_fail_a_successful_search(monkeypatch):
    """Offer bookkeeping is not part of the customer-visible search contract."""
    container = SimpleNamespace(menu=OfferingMenuStub())
    monkeypatch.setattr(tools, "get_services", lambda: container)

    result = tools.search_menu(query="pizza")

    assert result["success"] is True
    assert result.get("error_code") is None
