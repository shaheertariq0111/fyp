import base64
import hashlib
import hmac
import json
import logging
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.agent import tools as agent_tools
from src.agent.context import AgentRequestContext, request_context
from src.agent_client import AgentInvocationResult
from src.api import main
from src.api.schemas import ChatResponse, ToolCallResult
from src.infrastructure.logging import JsonFormatter
from src.models.tool_responses import ToolResponse
from src.services.cart_service import CartService
from src.services.customer_service import CustomerService
from src.services.menu_service import MenuService
from src.services.order_service import OrderService
from src.services.ticket_service import AdminTicketError
from fakes import MemoryCartRepository, MemoryMenuRepository, MemoryOrderRepository
from test_config import make_test_settings
from src.services.whatsapp_voice_job_service import VoiceJobSubmission
from src.services.whatsapp_conversation_service import (
    WhatsAppConversationReply,
    WhatsAppDeliveryOutcome,
)
from src.services.whatsapp_receipt_activation_service import (
    WhatsAppReceiptActivationResult,
)


CUSTOMER_TICKET_KEYS = {
    "ticket_id",
    "ticket_type",
    "status",
    "status_label",
    "priority",
    "created_at",
    "updated_at",
    "next_action",
}


def customer_ticket(**overrides):
    ticket = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "ticket_type": "human_assistance",
        "status": "open",
        "status_label": "Open",
        "priority": "normal",
        "created_at": "2026-07-24T10:00:00+00:00",
        "updated_at": "2026-07-24T10:00:00+00:00",
        "next_action": "await_support_contact",
    }
    ticket.update(overrides)
    return ticket


class MemoryAgentRequestService:
    def __init__(self):
        self.requests = {}
        self.next_id = 1
        self.agentflo_message_ids = set()
        self.agentflo_claims = []
        self.agentflo_markers = {}
        self.agentflo_marker_gets = []
        self.receipt_events = []

    def claim_agentflo_whatsapp_message(self, message_id):
        self.agentflo_claims.append(message_id)
        if message_id in self.agentflo_message_ids:
            return False
        self.agentflo_message_ids.add(message_id)
        self.agentflo_markers[message_id] = {"delivery_state": "processing"}
        return True

    def get_agentflo_whatsapp_message(self, message_id):
        self.agentflo_marker_gets.append(message_id)
        marker = self.agentflo_markers.get(message_id)
        return deepcopy(marker) if marker else None

    def cache_agentflo_whatsapp_response(
        self,
        message_id,
        *,
        request_id,
        session_id,
        customer_id,
        reply,
        submitted_order_id=None,
    ):
        marker = {
            "delivery_state": "response_ready",
            "request_id": request_id,
            "session_id": session_id,
            "customer_id": customer_id,
            "reply": reply,
        }
        if submitted_order_id is not None:
            marker["submitted_order_id"] = submitted_order_id
        self.agentflo_markers[message_id] = marker
        self.receipt_events.append("cache")

    def complete_agentflo_whatsapp_message(self, message_id):
        marker = self.agentflo_markers.get(message_id)
        if marker is None or marker.get("delivery_state") != "outbound_sending":
            return False
        marker["delivery_state"] = "completed"
        self.receipt_events.append("complete")
        return True

    def complete_agentflo_whatsapp_with_receipt_pending(self, message_id):
        marker = self.agentflo_markers.get(message_id)
        if (
            marker is None
            or marker.get("delivery_state") != "outbound_sending"
            or "submitted_order_id" not in marker
        ):
            return False
        marker["delivery_state"] = "completed"
        marker["receipt_activation_state"] = "pending"
        self.receipt_events.append("receipt_pending")
        return True

    def complete_agentflo_whatsapp_receipt_activation(self, message_id):
        marker = self.agentflo_markers.get(message_id)
        if (
            marker is None
            or marker.get("delivery_state") != "completed"
            or marker.get("receipt_activation_state") != "pending"
        ):
            return False
        marker["receipt_activation_state"] = "completed"
        return True

    def mark_agentflo_whatsapp_receipt_manual_review(self, message_id):
        marker = self.agentflo_markers.get(message_id)
        if (
            marker is None
            or marker.get("delivery_state") != "completed"
            or marker.get("receipt_activation_state") != "pending"
        ):
            return False
        marker["receipt_activation_state"] = "manual_review"
        return True

    def claim_agentflo_whatsapp_outbound(self, message_id):
        marker = self.agentflo_markers.get(message_id)
        if marker is None or marker.get("delivery_state") != "response_ready":
            return False
        marker["delivery_state"] = "outbound_sending"
        return True

    def retry_agentflo_whatsapp_outbound(self, message_id):
        marker = self.agentflo_markers.get(message_id)
        if marker is None or marker.get("delivery_state") != "outbound_sending":
            return False
        marker["delivery_state"] = "response_ready"
        return True

    def release_agentflo_whatsapp_message(self, message_id):
        self.agentflo_message_ids.discard(message_id)
        self.agentflo_markers.pop(message_id, None)

    def start_processing(self, **kwargs):
        request_id = f"req-{self.next_id}"
        self.next_id += 1
        record = {
            "request_id": request_id,
            "status": "processing",
            "actor_id": kwargs["actor_id"],
            "session_id": kwargs["session_id"],
            "message": kwargs["message"],
            "channel": kwargs["channel"],
            "request": kwargs["request_payload"],
        }
        self.requests[request_id] = record
        return record

    def complete(self, request_id, response):
        self.requests[request_id] = {
            **self.requests[request_id],
            "status": "completed",
            "response": response,
        }
        return self.requests[request_id]

    def fail(self, request_id, *, error_code, message):
        self.requests[request_id] = {
            **self.requests[request_id],
            "status": "failed",
            "error_code": error_code,
            "failure_message": message,
        }
        return self.requests[request_id]

    def get(self, request_id):
        return self.requests.get(request_id)


class MemoryConversationHistoryService:
    def __init__(self):
        self.records = []
        self.fail_writes = False
        self.admin_conversations_result = {"conversations": []}
        self.admin_messages_result = {
            "conversation_id": "conv-1",
            "channel": "whatsapp",
            "messages": [],
        }
        self.admin_list_calls = []
        self.admin_message_calls = []

    def store_inbound_whatsapp_message(self, **kwargs):
        if self.fail_writes:
            raise RuntimeError("private history failure")
        record = {
            **kwargs,
            "direction": "inbound",
            "sender_type": "customer",
        }
        self.records.append(record)
        return record

    def admin_list_conversations(self, *, limit=50):
        self.admin_list_calls.append({"limit": limit})
        return deepcopy(self.admin_conversations_result)

    def admin_list_messages(self, conversation_id):
        self.admin_message_calls.append(conversation_id)
        result = deepcopy(self.admin_messages_result)
        result["conversation_id"] = conversation_id
        return result

    def store_outbound_whatsapp_message(self, **kwargs):
        if self.fail_writes:
            raise RuntimeError("private history failure")
        record = {
            **kwargs,
            "direction": "outbound",
            "sender_type": "agent",
            "outbound_status": (kwargs.get("outbound") or {}).get("status"),
        }
        self.records.append(record)
        return record


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
        self.whatsapp_order_state = {}
        self.agent_sessions = SimpleNamespace(
            resolve=self.resolve,
            get_whatsapp_order_state=lambda customer_id, session_id: self.whatsapp_order_state,
            save_whatsapp_order_state=self.save_whatsapp_order_state,
            clear_whatsapp_order_state=lambda customer_id, session_id: self.whatsapp_order_state.clear(),
        )
        self.agent_requests = MemoryAgentRequestService()
        self.conversation_history = MemoryConversationHistoryService()
        self.whatsapp_receipt_activation = None

    def save_whatsapp_order_state(self, customer_id, session_id, **kwargs):
        self.whatsapp_order_state = {
            "offered_menu_items": kwargs["offered_menu_items"],
        }

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


class WhatsAppIdentityServices(IdentityServices):
    def __init__(self):
        super().__init__()
        self.profiles = {}
        self.profile_update_count = 0
        self.resolve_count = 0
        self.customers = self
        self.repository = SimpleNamespace(
            get=lambda customer_id: deepcopy(self.profiles.get(customer_id))
        )

    def update_profile(
        self,
        customer_id,
        *,
        display_name=None,
        whatsapp_profile_name=None,
        phone_number=None,
        channel,
        phone_verified,
        name_source="customer_provided",
    ):
        self.profile_update_count += 1
        profile = deepcopy(self.profiles.get(customer_id)) or {
            "customer_id": customer_id,
            "display_name": None,
            "name_confirmed": False,
            "name_source": None,
            "whatsapp_profile_name": None,
            "phone_e164": None,
            "phone_verified": False,
        }
        if display_name is not None:
            profile["display_name"] = display_name
            profile["name_confirmed"] = True
            profile["name_source"] = name_source
        if whatsapp_profile_name is not None:
            profile["whatsapp_profile_name"] = whatsapp_profile_name
        if phone_number is not None:
            profile["phone_e164"] = phone_number
            profile["phone_verified"] = phone_verified
        self.profiles[customer_id] = profile
        return ToolResponse.ok(
            data={"customer": profile},
            user_message="Customer details were saved.",
        )

    clean_customer_name = staticmethod(CustomerService.clean_customer_name)
    confirmed_name = staticmethod(CustomerService.confirmed_name)
    suggested_whatsapp_name = staticmethod(
        CustomerService.suggested_whatsapp_name
    )

    def confirm_customer_name(
        self,
        customer_id,
        display_name,
        *,
        source,
        channel,
    ):
        return self.update_profile(
            customer_id,
            display_name=display_name,
            channel=channel,
            phone_verified=False,
            name_source=source,
        )

    def resolve(self, **kwargs):
        self.resolve_count += 1
        customer_id = kwargs.get("customer_id") or "anonymous"
        session_id = kwargs.get("requested_session_id") or "session"
        customer = self.profiles.get(customer_id, {
            "customer_id": customer_id,
            "display_name": None,
            "phone_e164": None,
            "phone_verified": False,
        })
        return {
            "session": {
                "agent_session_id": session_id,
                "customer_id": customer_id,
                "channel": kwargs.get("channel", "web"),
                "expires_at": 123,
            },
            "customer": customer,
            "rotated": False,
        }


class StubAgentfloGateway:
    def __init__(self, *, configured, result=None):
        self.configured = configured
        self.result = result or {
            "sent": True,
            "status": "accepted",
            "providerMessageId": "provider-synthetic-1",
        }
        self.calls = []

    def send_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def support_tool_call(result, *, name="handle_order_complaint", is_write=True):
    return ToolCallResult(
        tool_name=name,
        success=result.get("success", True),
        is_write=is_write,
        result=result,
        error_code=result.get("error_code"),
    )


@pytest.mark.parametrize(
    ("entity", "tool_name"),
    [
        ("ticket", "create_human_assistance_ticket"),
        ("support_ticket", "handle_order_complaint"),
    ],
)
def test_state_extraction_includes_customer_safe_support_ticket(
    entity,
    tool_name,
):
    ticket = customer_ticket()
    state = main._state_from_tool_calls([
        support_tool_call(
            {
                "success": True,
                "data": {"ticket": ticket},
                "agent": {"entity": entity, "ticket_id": ticket["ticket_id"]},
            },
            name=tool_name,
        )
    ])

    assert state["support_ticket"] == ticket


def test_state_extraction_defensively_projects_support_ticket():
    ticket = customer_ticket(
        ticket_type="order_complaint",
        order_id="ORD-1",
        description="private complaint",
        order_status_snapshot="preparing",
        admin_notes=[{"text": "private", "actor": "admin-1"}],
        status_history=[{"actor": "admin-1"}],
        session_id="private-session",
        request_id="private-request",
        version=3,
        PK="TICKET#TKT-20260724-A1B2C3",
    )

    state = main._state_from_tool_calls([
        support_tool_call({
            "success": True,
            "data": {"ticket": ticket},
            "agent": {"entity": "ticket"},
        })
    ])

    assert state["support_ticket"] == {
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


@pytest.mark.parametrize(
    ("tracking_state", "tickets"),
    [
        ("multiple_active_tickets", [customer_ticket()]),
        ("no_active_tickets", []),
    ],
)
def test_state_extraction_includes_support_ticket_tracking(
    tracking_state,
    tickets,
):
    state = main._state_from_tool_calls([
        support_tool_call(
            {
                "success": True,
                "data": {"tickets": tickets},
                "agent": {
                    "entity": "tickets",
                    "tracking_state": tracking_state,
                    "required_input": "ticket_id",
                },
            },
            name="get_support_ticket_status",
            is_write=False,
        )
    ])

    assert state["support_tickets"] == tickets
    assert state["support_tracking"] == {
        "tracking_state": tracking_state,
        "required_input": "ticket_id",
    }


def test_state_extraction_filters_every_support_ticket_list_item():
    state = main._state_from_tool_calls([
        support_tool_call(
            {
                "success": True,
                "data": {
                    "tickets": [
                        {
                            **customer_ticket(),
                            "session_id": "private-session",
                            "admin_notes": [{"actor": "admin-1"}],
                        },
                        "malformed",
                        {
                            **customer_ticket(
                                ticket_id="TKT-20260724-D4E5F6",
                                status="in_review",
                                status_label="In review",
                            ),
                            "version": 2,
                        },
                    ]
                },
                "agent": {
                    "entity": "tickets",
                    "tracking_state": "multiple_active_tickets",
                },
            },
            name="get_support_ticket_status",
            is_write=False,
        )
    ])

    assert state["support_tickets"] == [
        {
            **customer_ticket(),
        },
        {
            **customer_ticket(
                ticket_id="TKT-20260724-D4E5F6",
                status="in_review",
                status_label="In review",
            ),
        },
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "pending_manager"},
        {"priority": "internal_only"},
        {"created_at": "not-a-timestamp"},
    ],
)
def test_state_extraction_ignores_malformed_single_support_ticket(overrides):
    state = main._state_from_tool_calls([
        support_tool_call({
            "success": True,
            "data": {"ticket": customer_ticket(**overrides)},
            "agent": {"entity": "support_ticket"},
        })
    ])

    assert "support_ticket" not in state


def test_state_extraction_recomputes_forged_derived_ticket_fields():
    state = main._state_from_tool_calls([
        support_tool_call({
            "success": True,
            "data": {
                "ticket": customer_ticket(
                    status="waiting_for_customer",
                    status_label="Escalated internally",
                    next_action="reveal_internal_workflow",
                )
            },
            "agent": {"entity": "ticket"},
        })
    ])

    assert state["support_ticket"]["status_label"] == "Waiting for customer"
    assert state["support_ticket"]["next_action"] == "respond_to_support"


def test_state_extraction_filters_invalid_ticket_list_values_and_future_fields():
    valid = customer_ticket(
        future_customer_field="drop me",
        status_label="forged",
        next_action="forged",
    )
    state = main._state_from_tool_calls([
        support_tool_call(
            {
                "success": True,
                "data": {
                    "tickets": [
                        valid,
                        customer_ticket(status="pending_manager"),
                        customer_ticket(priority="internal_only"),
                    ]
                },
                "agent": {"entity": "support_tickets"},
            },
            name="get_support_ticket_status",
            is_write=False,
        )
    ])

    assert len(state["support_tickets"]) == 1
    assert set(state["support_tickets"][0]) == CUSTOMER_TICKET_KEYS
    assert state["support_tickets"][0]["status_label"] == "Open"
    assert state["support_tickets"][0]["next_action"] == (
        "await_support_contact"
    )


def test_state_extraction_ignores_ticket_list_when_every_item_is_invalid():
    state = main._state_from_tool_calls([
        support_tool_call(
            {
                "success": True,
                "data": {
                    "tickets": [
                        customer_ticket(status="pending_manager"),
                        "malformed",
                    ]
                },
                "agent": {"entity": "tickets"},
            },
            name="get_support_ticket_status",
            is_write=False,
        )
    ])

    assert "support_tickets" not in state


@pytest.mark.parametrize(
    ("next_action", "agent", "expected"),
    [
        (
            "request_order_id",
            {
                "entity": "pending_support",
                "pending_support_intent": "order_complaint",
                "required_input": "order_id",
            },
            {
                "pending_support_intent": "order_complaint",
                "required_input": "order_id",
                "next_action": "request_order_id",
            },
        ),
        (
            "request_complaint_description",
            {
                "entity": "pending_support",
                "pending_support_intent": "order_complaint",
                "order_id": "ORD-1",
                "required_input": "complaint_description",
                "pending_complaint_description": "must stay private",
            },
            {
                "pending_support_intent": "order_complaint",
                "order_id": "ORD-1",
                "required_input": "complaint_description",
                "next_action": "request_complaint_description",
            },
        ),
        (
            "support_cancelled",
            {
                "entity": "pending_support",
                "pending_support_intent": None,
            },
            {
                "pending_support_intent": None,
                "next_action": "support_cancelled",
            },
        ),
    ],
)
def test_state_extraction_projects_pending_support_safely(
    next_action,
    agent,
    expected,
):
    state = main._state_from_tool_calls([
        support_tool_call({
            "success": True,
            "next_action": next_action,
            "agent": agent,
        })
    ])

    assert state["pending_support"] == expected
    assert "pending_complaint_description" not in state["pending_support"]


def test_state_extraction_ignores_malformed_support_and_preserves_cart_order():
    calls = [
        support_tool_call({
            "success": True,
            "data": "not-a-map",
            "agent": "not-a-map",
        }),
        support_tool_call(
            {
                "success": True,
                "data": {"cart": {"cart_id": "CART-1"}},
                "agent": {"entity": "cart"},
            },
            name="get_active_cart",
            is_write=False,
        ),
        support_tool_call(
            {
                "success": True,
                "data": {"order": {"order_id": "ORD-1"}},
                "agent": {},
            },
            name="get_order_status",
            is_write=False,
        ),
    ]

    state = main._state_from_tool_calls(calls)

    assert state["cart"]["cart_id"] == "CART-1"
    assert state["order"]["order_id"] == "ORD-1"
    assert "support_ticket" not in state


def client():
    return TestClient(main.app)


def stub_agent_client(monkeypatch, raw_result, text: str | None = None, captured: dict | None = None):
    default_assessment = {
        "claims_transactional_progression": False,
        "claimed_actions": [],
    }
    if isinstance(raw_result, dict):
        raw_result.setdefault("claim_assessment", default_assessment)
        raw_result.setdefault("no_write_authorized", True)
    else:
        if not hasattr(raw_result, "claim_assessment"):
            setattr(raw_result, "claim_assessment", default_assessment)
        if not hasattr(raw_result, "no_write_authorized"):
            setattr(raw_result, "no_write_authorized", True)

    class FakeAgentRuntimeClient:
        def invoke(self, request):
            if captured is not None:
                captured.update(request.__dict__)
            return AgentInvocationResult(
                text=text if text is not None else str(raw_result),
                raw_result=raw_result,
            )

    monkeypatch.setattr(main, "get_agent_runtime_client", lambda: FakeAgentRuntimeClient())


@pytest.fixture(autouse=True)
def default_identity_services(monkeypatch):
    services = IdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(main, "get_settings", make_test_settings)


def test_health_route():
    response = client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"].startswith("http-")


def test_cors_exposes_request_id_headers():
    response = client().get("/health", headers={"Origin": "http://localhost:3000"})

    exposed_headers = response.headers["access-control-expose-headers"]
    assert "X-Request-ID" in exposed_headers
    assert "X-Agent-Request-ID" in exposed_headers


def test_http_request_id_header_is_propagated_and_route_template_logged(caplog):
    with caplog.at_level("INFO", logger="src.api.main"):
        response = client().get("/api/chat/missing-request", headers={"X-Request-ID": "external-request-1"})

    assert response.status_code == 404
    assert response.headers["x-request-id"] == "external-request-1"
    route_logs = [record for record in caplog.records if getattr(record, "event", None) == "http_request_completed"]
    assert route_logs
    assert route_logs[-1].http_request_id == "external-request-1"
    assert route_logs[-1].route == "/api/chat/{request_id}"


def test_chat_response_includes_http_and_agent_request_headers(monkeypatch):
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(message={"content": [{"text": "Hello!"}]}),
        text="Hello!",
    )

    response = client().post(
        "/api/chat",
        json={"message": "hi", "session_id": "session", "user_id": "user"},
        headers={"X-Request-ID": "http-chat-1"},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "http-chat-1"
    assert response.headers["x-agent-request-id"] == response.json()["request_id"]


def completed_chat_response(test_client, request_id):
    status_response = test_client.get(f"/api/chat/{request_id}")
    assert status_response.status_code == 200
    return status_response.json()


def test_chat_route_invokes_agent_and_sanitizes(monkeypatch):
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(message={"content": [{"text": "<thinking>hidden</thinking>Hello!"}]}),
        text="Hello!",
    )

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "hi", "session_id": "session", "user_id": "user"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    payload = completed_chat_response(test_client, response.json()["request_id"])
    assert payload["response"] == "Hello!"
    assert payload["text"] == "Hello!"
    assert payload["tool_calls"] == []
    assert payload["write_succeeded"] is False


def test_agentflo_whatsapp_meta_payload_invokes_existing_agent_flow(
    monkeypatch,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    captured = {}
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(message={"content": [{"text": "Welcome!"}]}),
        text="Welcome!",
        captured=captured,
    )
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "metadata": {"phone_number_id": "sender-100"},
                    "contacts": [{
                        "wa_id": "10000000000",
                        "profile": {"name": "Synthetic Customer"},
                    }],
                    "messages": [{
                        "from": "10000000000",
                        "id": "wamid.synthetic-1",
                        "type": "text",
                        "text": {"body": "Hello from WhatsApp"},
                    }],
                }
            }]
        }]
    }

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "success": True,
        "reply": "Welcome!",
        "text": "Welcome!",
        "request_id": "req-1",
        "session_id": body["session_id"],
        "outbound": {
            "sent": False,
            "skipped": True,
            "reason": "gateway_not_configured",
        },
    }
    assert body["session_id"].startswith("whatsapp-")
    assert "10000000000" not in body["session_id"]
    assert captured["message"] == "Hello from WhatsApp"
    assert captured["channel"] == "whatsapp"
    assert captured["customer_name"] is None
    profile = next(iter(services.profiles.values()))
    assert profile["display_name"] is None
    assert profile["name_confirmed"] is False
    assert profile["whatsapp_profile_name"] == "Synthetic Customer"
    assert captured["customer_phone"] == "+10000000000"
    assert captured["user_id"].startswith("whatsapp-")
    assert captured["agent_session_id"] == body["session_id"]
    assert services.agent_requests.agentflo_claims == ["wamid.synthetic-1"]
    assert len(services.conversation_history.records) == 2
    inbound_record, outbound_record = services.conversation_history.records
    assert inbound_record["direction"] == "inbound"
    assert inbound_record["conversation_id"] == body["session_id"]
    assert inbound_record["customer_id"].startswith("whatsapp-")
    assert inbound_record["message_text"] == "Hello from WhatsApp"
    assert inbound_record["inbound_message_id"] == "wamid.synthetic-1"
    assert inbound_record["customer_number"] == "10000000000"
    assert outbound_record["direction"] == "outbound"
    assert outbound_record["conversation_id"] == body["session_id"]
    assert outbound_record["request_id"] == "req-1"
    assert outbound_record["message_text"] == "Welcome!"
    assert outbound_record["inbound_message_id"] == "wamid.synthetic-1"


def test_agentflo_whatsapp_duplicate_is_ignored_before_side_effects(
    monkeypatch,
    caplog,
):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(configured=True)
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    invocations = []

    class CountingAgentRuntimeClient:
        def invoke(self, request):
            invocations.append(request)
            return AgentInvocationResult(
                text="Single synthetic reply.",
                raw_result=SimpleNamespace(),
            )

    monkeypatch.setattr(
        main,
        "get_agent_runtime_client",
        lambda: CountingAgentRuntimeClient(),
    )
    private_message = "Private duplicate synthetic message"
    full_phone = "+10000000000"
    payload = {
        "message": private_message,
        "from": full_phone,
        "sender_id": "sender-synthetic-duplicate",
        "message_id": "wamid.synthetic-duplicate",
    }

    first = client().post(
        "/api/channels/agentflo/whatsapp",
        json=payload,
    )
    with caplog.at_level(logging.INFO, logger="src.api.main"):
        duplicate = client().post(
            "/api/channels/agentflo/whatsapp",
            json=payload,
        )

    assert first.status_code == 200
    assert first.json()["success"] is True
    assert duplicate.status_code == 200
    assert duplicate.json() == {
        "success": True,
        "ignored": True,
        "duplicate": True,
        "reason": "duplicate_message",
    }
    assert len(invocations) == 1
    assert len(gateway.calls) == 1
    assert len(services.agent_requests.requests) == 1
    assert services.profile_update_count == 1
    assert services.resolve_count == 1
    assert len(services.conversation_history.records) == 2
    assert services.agent_requests.agentflo_claims == [
        "wamid.synthetic-duplicate",
        "wamid.synthetic-duplicate",
    ]
    assert private_message not in caplog.text
    assert full_phone not in caplog.text
    duplicate_log = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "agentflo_whatsapp_duplicate"
    )
    assert duplicate_log.idempotency_status == "duplicate"


@pytest.mark.parametrize(
    "delivery_state",
    ["processing", "outbound_sending", "completed"],
)
def test_agentflo_whatsapp_duplicate_in_non_sendable_state_is_accepted_without_send(
    monkeypatch,
    delivery_state,
):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(configured=True)
    message_id = f"wamid.duplicate-{delivery_state}"
    services.agent_requests.agentflo_message_ids.add(message_id)
    services.agent_requests.agentflo_markers[message_id] = {
        "delivery_state": delivery_state,
        "request_id": "req-existing",
        "session_id": "session-existing",
        "customer_id": "customer-existing",
        "reply": "Cached reply.",
    }
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Duplicate inbound message",
            "from": "+10000000000",
            "sender_id": "sender-duplicate-state",
            "message_id": message_id,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "ignored": True,
        "duplicate": True,
        "reason": "duplicate_message",
    }
    assert gateway.calls == []
    assert services.agent_requests.requests == {}
    assert services.profile_update_count == 0
    assert services.agent_requests.agentflo_markers[message_id][
        "delivery_state"
    ] == delivery_state


def test_agentflo_whatsapp_response_ready_duplicate_claims_and_sends_once(
    monkeypatch,
):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(configured=True)
    message_id = "wamid.duplicate-response-ready"
    services.agent_requests.agentflo_message_ids.add(message_id)
    services.agent_requests.agentflo_markers[message_id] = {
        "delivery_state": "response_ready",
        "request_id": "req-existing",
        "session_id": "session-existing",
        "customer_id": "customer-existing",
        "reply": "Cached authoritative reply.",
    }
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Duplicate inbound message",
            "from": "+10000000000",
            "sender_id": "sender-response-ready",
            "message_id": message_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["reply"] == "Cached authoritative reply."
    assert len(gateway.calls) == 1
    assert services.agent_requests.requests == {}
    assert services.profile_update_count == 0
    assert services.agent_requests.agentflo_markers[message_id][
        "delivery_state"
    ] == "completed"


def test_agentflo_whatsapp_duplicate_confirm_does_not_create_duplicate_order(monkeypatch):
    menu_repository = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}],
        [],
    )
    order_repository = MemoryOrderRepository()
    order_service = OrderService(order_repository, menu_repository)
    services = WhatsAppIdentityServices()
    services.orders = order_service
    gateway = StubAgentfloGateway(configured=True)
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(agent_tools, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    invocations = []

    class ConfirmingAgentRuntimeClient:
        def invoke(self, request):
            invocations.append(request)
            pending = order_service.create_pending_from_cart({
                "user_id": request.user_id,
                "agent_session_id": request.agent_session_id,
                "restaurant_id": "restaurant",
                "branch_id": "branch",
                "cart_id": f"cart-{len(invocations)}",
                "subtotal": 10,
                "currency": "PKR",
                "items": [{
                    "item_id": "item",
                    "name": "Item",
                    "quantity": 1,
                    "selected_options": {},
                    "current_price": 10,
                }],
            })
            order_id = pending.data["order_id"]
            order_service.update_order_flow(request.user_id, order_id, "set_takeaway")
            confirmed = order_service.update_order_flow(
                request.user_id,
                order_id,
                "confirm",
                idempotency_key=request.request_id,
            ).model_dump(exclude_none=True)
            return AgentInvocationResult(
                text="Your order has been confirmed and sent to the restaurant.",
                raw_result={
                    "tool_calls": [{
                        "tool_name": "update_order_flow",
                        "success": True,
                        "is_write": True,
                        "result": confirmed,
                        "error_code": None,
                    }],
                },
            )

    monkeypatch.setattr(
        main,
        "get_agent_runtime_client",
        lambda: ConfirmingAgentRuntimeClient(),
    )
    payload = {
        "message": "confirm",
        "from": "+10000000000",
        "sender_id": "sender-confirm-duplicate",
        "message_id": "wamid.confirm-duplicate",
    }

    first = client().post("/api/channels/agentflo/whatsapp", json=payload)
    duplicate = client().post("/api/channels/agentflo/whatsapp", json=payload)

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert len(invocations) == 1
    assert len(order_repository.data) == 1
    assert next(iter(order_repository.data.values()))["status"] == "submitted_to_restaurant"
    assert len(gateway.calls) == 1


def test_agentflo_whatsapp_missing_message_id_processes_with_safe_warning(
    monkeypatch,
    caplog,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    captured = {}
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(),
        text="Processed without message ID.",
        captured=captured,
    )
    private_message = "Private message without identifier"
    full_phone = "+10000000000"

    with caplog.at_level(logging.WARNING, logger="src.api.main"):
        response = client().post(
            "/api/channels/agentflo/whatsapp",
            json={
                "message": private_message,
                "from": full_phone,
                "sender_id": "sender-synthetic-no-id",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Processed without message ID."
    assert captured["message"] == private_message
    assert services.agent_requests.agentflo_claims == []
    assert len(services.conversation_history.records) == 2
    assert services.conversation_history.records[0]["inbound_message_id"] is None
    assert services.conversation_history.records[1]["inbound_message_id"] is None
    assert private_message not in caplog.text
    assert full_phone not in caplog.text
    warning = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "agentflo_whatsapp_idempotency_skipped"
    )
    assert warning.reason == "missing_message_id"


def test_agentflo_whatsapp_sends_generated_reply_through_gateway(monkeypatch):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(configured=True)
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(),
        text="Synthetic outbound reply.",
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Synthetic inbound message",
            "from": "+10000000000",
            "sender_id": "sender-synthetic-1",
            "message_id": "message-synthetic-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["outbound"] == {
        "sent": True,
        "status": "accepted",
        "providerMessageId": "provider-synthetic-1",
    }
    assert gateway.calls == [{
        "customer_number": "+10000000000",
        "conversation_id": body["session_id"],
        "sender_id": "sender-synthetic-1",
        "text": "Synthetic outbound reply.",
        "request_id": "req-1",
    }]
    assert services.agent_requests.agentflo_markers["message-synthetic-1"][
        "delivery_state"
    ] == "completed"


def test_agentflo_whatsapp_missing_sender_returns_safe_outbound_failure(
    monkeypatch,
):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(configured=True)
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Generated reply.")

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Synthetic inbound message",
            "from": "10000000000",
            "message_id": "message-synthetic-2",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "error_code": "AGENTFLO_OUTBOUND_FAILED",
        "reply": "Generated reply.",
        "text": "Generated reply.",
        "request_id": "req-1",
        "session_id": response.json()["session_id"],
        "outbound": {
            "sent": False,
            "error_code": "AGENTFLO_OUTBOUND_FAILED",
        },
    }
    assert gateway.calls == []


def test_agentflo_whatsapp_gateway_not_configured_skips_delivery(monkeypatch):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(configured=False)
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Local reply.")

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Synthetic inbound message",
            "from": "10000000000",
            "sender_id": "sender-synthetic-2",
            "message_id": "message-synthetic-3",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["outbound"] == {
        "sent": False,
        "skipped": True,
        "reason": "gateway_not_configured",
    }
    assert gateway.calls == []


def test_agentflo_whatsapp_gateway_failure_preserves_reply_safely(monkeypatch):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(
        configured=True,
        result={
            "sent": False,
            "error_code": "AGENTFLO_OUTBOUND_FAILED",
        },
    )
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Generated reply.")

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Synthetic inbound message",
            "from": "10000000000",
            "sender_id": "sender-synthetic-3",
            "message_id": "message-synthetic-4",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error_code"] == "AGENTFLO_OUTBOUND_FAILED"
    assert response.json()["reply"] == "Generated reply."
    assert response.json()["text"] == "Generated reply."
    assert response.json()["outbound"] == {
        "sent": False,
        "error_code": "AGENTFLO_OUTBOUND_FAILED",
    }
    assert len(services.conversation_history.records) == 2
    assert services.conversation_history.records[1]["message_text"] == "Generated reply."
    assert services.conversation_history.records[1]["outbound"] == {
        "sent": False,
        "error_code": "AGENTFLO_OUTBOUND_FAILED",
    }
    assert services.agent_requests.agentflo_markers["message-synthetic-4"][
        "delivery_state"
    ] == "response_ready"


def test_agentflo_whatsapp_retries_cached_reply_without_reprocessing(monkeypatch):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(
        configured=True,
        result={"sent": False, "error_code": "AGENTFLO_OUTBOUND_FAILED"},
    )
    invocations = []

    class CountingAgentRuntimeClient:
        def invoke(self, request):
            invocations.append(request)
            return AgentInvocationResult(
                text="Cached authoritative reply.",
                raw_result=SimpleNamespace(
                    claim_assessment={
                        "claims_transactional_progression": False,
                        "claimed_actions": [],
                    },
                    no_write_authorized=True,
                ),
            )

    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    monkeypatch.setattr(
        main,
        "get_agent_runtime_client",
        lambda: CountingAgentRuntimeClient(),
    )
    payload = {
        "message": "Synthetic retry message",
        "from": "+10000000000",
        "sender_id": "sender-retry",
        "message_id": "message-retry-1",
    }

    first = client().post("/api/channels/agentflo/whatsapp", json=payload)
    assert services.agent_requests.agentflo_markers["message-retry-1"][
        "delivery_state"
    ] == "response_ready"

    assert services.agent_requests.claim_agentflo_whatsapp_outbound(
        "message-retry-1"
    )
    concurrent_duplicate = client().post(
        "/api/channels/agentflo/whatsapp",
        json=payload,
    )
    assert concurrent_duplicate.json()["duplicate"] is True
    assert len(gateway.calls) == 1
    assert services.agent_requests.agentflo_markers["message-retry-1"][
        "delivery_state"
    ] == "outbound_sending"
    assert services.agent_requests.retry_agentflo_whatsapp_outbound(
        "message-retry-1"
    )

    gateway.result = {
        "sent": True,
        "status": "accepted",
        "providerMessageId": "provider-retry-1",
    }
    retried = client().post("/api/channels/agentflo/whatsapp", json=payload)

    assert first.json()["success"] is False
    assert retried.json()["success"] is True
    assert retried.json()["reply"] == "Cached authoritative reply."
    assert len(invocations) == 1
    assert len(services.agent_requests.requests) == 1
    assert services.profile_update_count == 1
    assert len(services.conversation_history.records) == 2
    assert len(gateway.calls) == 2
    assert services.agent_requests.agentflo_markers["message-retry-1"][
        "delivery_state"
    ] == "completed"


def test_agentflo_whatsapp_unexpected_gateway_error_is_sanitized(monkeypatch):
    services = WhatsAppIdentityServices()
    gateway = StubAgentfloGateway(configured=True)

    def fail_safely(**kwargs):
        raise RuntimeError("private gateway failure")

    gateway.send_text = fail_safely
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "AgentfloGatewayService",
        lambda **kwargs: gateway,
    )
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Generated reply.")

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Synthetic inbound message",
            "from": "10000000000",
            "sender_id": "sender-synthetic-4",
            "message_id": "message-synthetic-5",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["error_code"] == "AGENTFLO_OUTBOUND_FAILED"
    assert response.json()["reply"] == "Generated reply."
    assert "private gateway failure" not in response.text


class ReceiptConversationStub:
    def __init__(self, reply, delivery, events=None):
        self.reply = reply
        self.delivery = delivery
        self.events = events if events is not None else []
        self.process_calls = []
        self.deliver_calls = []

    def process_text(self, inbound, *, http_request_id=None):
        self.process_calls.append((inbound, http_request_id))
        self.events.append("process")
        return self.reply

    def deliver(self, inbound, reply):
        self.deliver_calls.append((inbound, reply))
        self.events.append("deliver")
        return self.delivery


class ReceiptActivationStub:
    def __init__(self, agent_requests, *, status="activated", retryable=False, events=None):
        self.agent_requests = agent_requests
        self.status = status
        self.retryable = retryable
        self.events = events if events is not None else []
        self.calls = []

    def activate_pending(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        self.events.append("activate")
        if self.status == "activated":
            self.agent_requests.complete_agentflo_whatsapp_receipt_activation(
                kwargs["message_id"]
            )
        elif self.status == "manual_review":
            self.agent_requests.mark_agentflo_whatsapp_receipt_manual_review(
                kwargs["message_id"]
            )
        return WhatsAppReceiptActivationResult(self.status, self.retryable)


def receipt_settings(enabled=True):
    return make_test_settings(
        receipt_activation_enabled=enabled,
        receipt_jobs_table_name="receipt-jobs-test" if enabled else "",
        receipt_job_queue_url=(
            "https://sqs.example.test/receipt" if enabled else ""
        ),
    )


def configure_receipt_text_flow(
    monkeypatch,
    *,
    submitted_order_id="ORD-private-123",
    delivery=None,
    activation_status="activated",
    activation_retryable=False,
    enabled=True,
):
    services = WhatsAppIdentityServices()
    events = []
    services.agent_requests.receipt_events = events
    reply = WhatsAppConversationReply(
        "Private confirmation reply.",
        "request-private-1",
        "session-private-1",
        "customer-private-1",
        submitted_order_id=submitted_order_id,
    )
    delivery = delivery or WhatsAppDeliveryOutcome(
        "sent",
        {
            "sent": True,
            "status": "accepted",
            "providerMessageId": "provider-private-1",
        },
    )
    conversation = ReceiptConversationStub(reply, delivery, events)
    activation = ReceiptActivationStub(
        services.agent_requests,
        status=activation_status,
        retryable=activation_retryable,
        events=events,
    )
    services.whatsapp_receipt_activation = activation
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(main, "get_settings", lambda: receipt_settings(enabled))
    monkeypatch.setattr(
        main,
        "_whatsapp_conversation_service",
        lambda: conversation,
    )
    return services, conversation, activation, events


def receipt_payload(message_id="wamid.receipt-live-1"):
    payload = {
        "message": "Private inbound confirmation request",
        "from": "+923001234567",
        "sender_id": "sender-private-1",
    }
    if message_id is not None:
        payload["message_id"] = message_id
    return payload


def test_receipt_disabled_preserves_old_marker_shape_and_completion(monkeypatch):
    services, conversation, activation, events = configure_receipt_text_flow(
        monkeypatch,
        enabled=False,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(),
    )

    marker = services.agent_requests.agentflo_markers["wamid.receipt-live-1"]
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert marker["delivery_state"] == "completed"
    assert "submitted_order_id" not in marker
    assert "receipt_activation_state" not in marker
    assert activation.calls == []
    assert events == ["process", "cache", "deliver", "complete"]
    assert len(conversation.deliver_calls) == 1


def test_definite_text_send_precedes_atomic_receipt_handoff(monkeypatch):
    services, conversation, activation, events = configure_receipt_text_flow(
        monkeypatch,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(),
    )

    marker = services.agent_requests.agentflo_markers["wamid.receipt-live-1"]
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert events == ["process", "cache", "deliver", "receipt_pending", "activate"]
    assert marker["submitted_order_id"] == "ORD-private-123"
    assert marker["delivery_state"] == "completed"
    assert marker["receipt_activation_state"] == "completed"
    assert len(conversation.process_calls) == 1
    assert len(conversation.deliver_calls) == 1
    assert len(activation.calls) == 1
    call = activation.calls[0]
    assert call["message_id"] == "wamid.receipt-live-1"
    assert call["customer_number"] == "+923001234567"
    assert call["sender_id"] == "sender-private-1"
    assert call["marker"]["submitted_order_id"] == "ORD-private-123"
    assert services.agent_requests.agentflo_marker_gets == [
        "wamid.receipt-live-1"
    ]


@pytest.mark.parametrize(
    "reply_kind",
    ["normal_chat", "menu", "status", "pending_confirmation", "failed_confirmation"],
)
def test_non_submission_replies_follow_old_completion_without_receipt(
    monkeypatch,
    reply_kind,
):
    services, _conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
        submitted_order_id=None,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={**receipt_payload(), "message": reply_kind},
    )

    marker = services.agent_requests.agentflo_markers["wamid.receipt-live-1"]
    assert response.json()["success"] is True
    assert marker["delivery_state"] == "completed"
    assert "submitted_order_id" not in marker
    assert "receipt_activation_state" not in marker
    assert activation.calls == []


@pytest.mark.parametrize(
    "delivery",
    [
        WhatsAppDeliveryOutcome("skipped", {"sent": False, "skipped": True}),
        WhatsAppDeliveryOutcome("sent", {"sent": False}),
        WhatsAppDeliveryOutcome("retryable_failure", {"sent": False}),
        WhatsAppDeliveryOutcome("permanent_failure", {"sent": False}),
        WhatsAppDeliveryOutcome("ambiguous", {"sent": False}),
        WhatsAppDeliveryOutcome("ambiguous", {"sent": True}),
        WhatsAppDeliveryOutcome("skipped", {"sent": True, "skipped": True}),
        WhatsAppDeliveryOutcome("sent", {}),
    ],
)
def test_receipt_never_activates_without_definite_send(monkeypatch, delivery):
    services, _conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
        delivery=delivery,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(),
    )

    marker = services.agent_requests.agentflo_markers["wamid.receipt-live-1"]
    assert response.status_code == 200
    assert "receipt_activation_state" not in marker
    assert activation.calls == []


@pytest.mark.parametrize(
    ("activation_state", "expected_calls"),
    [("pending", 1), ("completed", 0), ("manual_review", 0)],
)
def test_duplicate_completed_receipt_state_recovers_only_pending(
    monkeypatch,
    activation_state,
    expected_calls,
):
    services, conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
    )
    message_id = "wamid.receipt-duplicate"
    services.agent_requests.agentflo_message_ids.add(message_id)
    services.agent_requests.agentflo_markers[message_id] = {
        "delivery_state": "completed",
        "receipt_activation_state": activation_state,
        "submitted_order_id": "ORD-private-duplicate",
        "request_id": "request-private-duplicate",
        "session_id": "session-private-duplicate",
        "reply": "Private cached reply.",
    }

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(message_id),
    )

    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert len(activation.calls) == expected_calls
    assert conversation.process_calls == []
    assert conversation.deliver_calls == []
    if activation_state == "pending":
        assert services.agent_requests.agentflo_markers[message_id][
            "receipt_activation_state"
        ] == "completed"


def test_duplicate_outbound_sending_never_activates_even_with_order_signal(monkeypatch):
    services, conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
    )
    message_id = "wamid.receipt-ambiguous"
    services.agent_requests.agentflo_message_ids.add(message_id)
    services.agent_requests.agentflo_markers[message_id] = {
        "delivery_state": "outbound_sending",
        "submitted_order_id": "ORD-private-ambiguous",
        "request_id": "request-private-ambiguous",
        "session_id": "session-private-ambiguous",
        "reply": "Private cached reply.",
    }

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(message_id),
    )

    assert response.json()["duplicate"] is True
    assert activation.calls == []
    assert conversation.process_calls == []
    assert conversation.deliver_calls == []
    assert services.agent_requests.agentflo_markers[message_id][
        "delivery_state"
    ] == "outbound_sending"


@pytest.mark.parametrize("sent", [True, False])
def test_response_ready_duplicate_activates_only_after_definite_retry(
    monkeypatch,
    sent,
):
    services, conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
    )
    gateway = StubAgentfloGateway(
        configured=True,
        result={
            "sent": sent,
            "status": "accepted" if sent else "failed",
        },
    )
    monkeypatch.setattr(main, "AgentfloGatewayService", lambda **kwargs: gateway)
    message_id = "wamid.receipt-cached-retry"
    services.agent_requests.agentflo_message_ids.add(message_id)
    services.agent_requests.agentflo_markers[message_id] = {
        "delivery_state": "response_ready",
        "submitted_order_id": "ORD-private-retry",
        "request_id": "request-private-retry",
        "session_id": "session-private-retry",
        "customer_id": "customer-private-retry",
        "reply": "Private cached confirmation.",
    }

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(message_id),
    )

    marker = services.agent_requests.agentflo_markers[message_id]
    assert response.status_code == 200
    assert len(gateway.calls) == 1
    assert conversation.process_calls == []
    assert conversation.deliver_calls == []
    assert len(activation.calls) == (1 if sent else 0)
    assert marker["delivery_state"] == ("completed" if sent else "response_ready")
    if sent:
        assert marker["receipt_activation_state"] == "completed"
    else:
        assert "receipt_activation_state" not in marker


@pytest.mark.parametrize(
    ("reloaded_state", "activation_state", "expected_calls"),
    [
        ("completed", "pending", 1),
        ("completed", "completed", 0),
        ("completed", "manual_review", 0),
        ("outbound_sending", None, 0),
    ],
)
def test_receipt_checkpoint_conflict_uses_only_reloaded_durable_state(
    monkeypatch,
    reloaded_state,
    activation_state,
    expected_calls,
):
    services, _conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
    )
    message_id = "wamid.receipt-conflict"

    def conflict(_message_id):
        marker = services.agent_requests.agentflo_markers[message_id]
        marker["delivery_state"] = reloaded_state
        if activation_state is not None:
            marker["receipt_activation_state"] = activation_state
        return False

    services.agent_requests.complete_agentflo_whatsapp_with_receipt_pending = conflict

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(message_id),
    )

    assert response.json()["success"] is True
    assert len(activation.calls) == expected_calls
    marker = services.agent_requests.agentflo_markers[message_id]
    if reloaded_state == "outbound_sending":
        assert "receipt_activation_state" not in marker


@pytest.mark.parametrize(
    ("status", "retryable", "expected_state"),
    [
        ("retryable_failure", True, "pending"),
        ("manual_review", False, "manual_review"),
    ],
)
def test_activation_failure_never_changes_successful_text_response_or_resends(
    monkeypatch,
    status,
    retryable,
    expected_state,
):
    services, conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
        activation_status=status,
        activation_retryable=retryable,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(),
    )

    marker = services.agent_requests.agentflo_markers["wamid.receipt-live-1"]
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert marker["delivery_state"] == "completed"
    assert marker["receipt_activation_state"] == expected_state
    assert len(conversation.deliver_calls) == 1
    assert len(activation.calls) == 1


def test_missing_message_id_prefers_no_receipt_over_unsafe_activation(monkeypatch):
    services, conversation, activation, _events = configure_receipt_text_flow(
        monkeypatch,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=receipt_payload(None),
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert services.agent_requests.agentflo_markers == {}
    assert len(conversation.deliver_calls) == 1
    assert activation.calls == []


def test_receipt_specific_logs_exclude_private_values(monkeypatch, caplog):
    private_values = {
        "ORD-private-123",
        "wamid.receipt-live-1",
        "+923001234567",
        "sender-private-1",
        "request-private-1",
        "session-private-1",
        "Private confirmation reply.",
    }
    configure_receipt_text_flow(monkeypatch)

    with caplog.at_level(logging.INFO, logger="src.api.main"):
        response = client().post(
            "/api/channels/agentflo/whatsapp",
            json=receipt_payload(),
        )

    assert response.status_code == 200
    receipt_logs = [
        vars(record)
        for record in caplog.records
        if getattr(record, "event", None)
        in {
            "agentflo_whatsapp_receipt_activation",
            "agentflo_whatsapp_receipt_handoff",
            "agentflo_whatsapp_receipt_handoff_failed",
        }
    ]
    serialized = repr(receipt_logs)
    assert receipt_logs
    for private in private_values:
        assert private not in serialized


def test_agentflo_whatsapp_simple_payload_extracts_aliases(monkeypatch):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    captured = {}
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(),
        text="Simple reply.",
        captured=captured,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "content": "Simple inbound message",
            "user_number": "+10000000001",
            "profile_name": "Synthetic User",
            "sender_id": "sender-200",
            "message_id": "simple-message-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Simple reply."
    assert response.json()["text"] == "Simple reply."
    assert captured["message"] == "Simple inbound message"
    assert captured["customer_phone"] == "+10000000001"
    assert captured["customer_name"] is None
    profile = next(iter(services.profiles.values()))
    assert profile["whatsapp_profile_name"] == "Synthetic User"
    assert profile["name_confirmed"] is False
    assert captured["channel"] == "whatsapp"


def test_agentflo_whatsapp_configured_secret_allows_correct_header(
    monkeypatch,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: make_test_settings(
            agentflo_whatsapp_webhook_secret="configured-webhook-secret",
        ),
    )
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Authenticated.")

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Hello",
            "from": "10000000000",
            "id": "authenticated-message-1",
        },
        headers={
            "X-Agentflo-Webhook-Secret": "configured-webhook-secret",
        },
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Authenticated."


def test_agentflo_whatsapp_configured_secret_allows_correct_path_secret(
    monkeypatch,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: make_test_settings(
            agentflo_whatsapp_webhook_secret="configured-webhook-secret",
        ),
    )
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Path authenticated.")

    response = client().post(
        "/api/channels/agentflo/whatsapp/configured-webhook-secret",
        json={
            "message": "Hello",
            "from": "10000000000",
            "id": "path-authenticated-message-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Path authenticated."


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Agentflo-Webhook-Secret": "wrong-webhook-secret"},
    ],
)
def test_agentflo_whatsapp_configured_secret_rejects_missing_or_wrong_header(
    monkeypatch,
    headers,
):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: make_test_settings(
            agentflo_whatsapp_webhook_secret="configured-webhook-secret",
        ),
    )
    captured = {}
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(),
        text="Must not run.",
        captured=captured,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Hello",
            "from": "10000000000",
            "id": "unauthorized-message-1",
        },
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "error_code": "AGENTFLO_WEBHOOK_UNAUTHORIZED",
        "user_message": "Webhook authentication failed.",
    }
    assert captured == {}


def test_agentflo_whatsapp_authentication_logs_exclude_secrets(
    monkeypatch,
    caplog,
):
    configured_secret = "configured-private-webhook-secret"
    provided_secret = "provided-private-webhook-secret"
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: make_test_settings(
            agentflo_whatsapp_webhook_secret=configured_secret,
        ),
    )

    with caplog.at_level(logging.INFO):
        response = client().post(
            f"/api/channels/agentflo/whatsapp/{provided_secret}",
            json={"message": "Hello"},
        )

    assert response.status_code == 401
    assert configured_secret not in caplog.text
    assert provided_secret not in caplog.text

    request_log = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request_completed"
    )
    assert (
        request_log.route
        == "/api/channels/agentflo/whatsapp/{webhook_secret}"
    )


def test_agentflo_whatsapp_without_configured_secret_warns_and_remains_open(
    monkeypatch,
    caplog,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: make_test_settings(
            agentflo_whatsapp_webhook_secret="",
        ),
    )
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Local response.")

    with caplog.at_level(logging.WARNING):
        response = client().post(
            "/api/channels/agentflo/whatsapp",
            json={
                "message": "Hello",
                "from": "10000000000",
                "id": "local-message-1",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Local response."
    warning = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "agentflo_whatsapp_unauthenticated"
    )
    assert warning.channel == "whatsapp"


def test_agentflo_whatsapp_enabled_audio_acknowledges_durable_submission(monkeypatch):
    captured = {}
    configured = make_test_settings(
        whatsapp_voice_enabled=True,
        voice_media_bucket_name="voice-bucket",
        voice_job_queue_url="queue-url",
        whatsapp_voice_jobs_table_name="voice-jobs-test",
        voice_media_allowed_hosts="media.example.test",
        voice_transcription_language_code="en-US",
    )

    class FakeJobs:
        def __init__(self, *_args, **_kwargs):
            pass

        def submit_audio(self, audio):
            captured["audio"] = audio
            return VoiceJobSubmission("wv1_" + "a" * 64, True, False, False)

    monkeypatch.setattr(main, "get_settings", lambda: configured)
    monkeypatch.setattr(main, "get_dynamodb_resource", lambda _settings: object())
    monkeypatch.setattr(main, "create_sqs_client", lambda **_kwargs: object())
    monkeypatch.setattr(main, "WhatsAppVoiceJobRepository", lambda *_args: object())
    monkeypatch.setattr(main, "VoiceQueueService", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(main, "WhatsAppVoiceJobService", FakeJobs)

    response = client().post("/api/channels/agentflo/whatsapp", json={
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "sender-private"},
            "messages": [{
                "from": "+15550100000", "id": "provider-private",
                "type": "audio", "audio": {"url": "https://media.example.test/private", "id": "media-private"},
            }],
        }}]}],
    })

    assert response.status_code == 200
    assert response.json() == {
        "success": True, "accepted": True, "queued": False, "message_type": "audio",
    }
    assert captured["audio"].message_id == "provider-private"
    assert captured["audio"].audio_id == "media-private"
    assert captured["audio"].media_url == "https://media.example.test/private"


@pytest.mark.parametrize("audio_id", [None, "   ", "x" * 513])
def test_agentflo_whatsapp_enabled_audio_rejects_invalid_audio_id(
    monkeypatch,
    audio_id,
):
    configured = make_test_settings(
        whatsapp_voice_enabled=True,
        voice_media_bucket_name="voice-bucket",
        voice_job_queue_url="queue-url",
        whatsapp_voice_jobs_table_name="voice-jobs-test",
        voice_transcription_language_code="en-US",
    )
    monkeypatch.setattr(main, "get_settings", lambda: configured)
    audio = {"url": "https://media.example.test/private"}
    if audio_id is not None:
        audio["id"] = audio_id

    response = client().post("/api/channels/agentflo/whatsapp", json={
        "entry": [{"changes": [{"value": {
            "metadata": {"phone_number_id": "sender-private"},
            "messages": [{
                "from": "+15550100000",
                "id": "provider-private",
                "type": "audio",
                "audio": audio,
            }],
        }}]}],
    })

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "ignored": True,
        "reason": "incomplete_audio_message",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"statuses": [{"id": "wamid.status-1", "status": "delivered"}]},
        {"message": "   ", "from": "10000000000"},
        {
            "entry": [{
                "changes": [{
                    "value": {
                        "metadata": {"phone_number_id": "sender-100"},
                        "statuses": [{"id": "wamid.status-2"}],
                    }
                }]
            }]
        },
    ],
)
def test_agentflo_whatsapp_non_text_events_are_ignored(monkeypatch, payload):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    captured = {}
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(),
        text="Must not be invoked.",
        captured=captured,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=payload,
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "ignored": True,
        "reason": "no_text_message",
    }
    assert captured == {}
    assert services.conversation_history.records == []


def test_agentflo_whatsapp_text_message_does_not_emit_ignored_shape_log(
    monkeypatch,
    caplog,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Text reply.")

    with caplog.at_level(logging.INFO, logger="src.api.main"):
        response = client().post(
            "/api/channels/agentflo/whatsapp",
            json={
                "message": "Synthetic text message",
                "from": "+10000000000",
                "id": "text-message-1",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Text reply."
    assert not any(
        getattr(record, "event", None)
        == "agentflo_whatsapp_ignored_payload_shape"
        for record in caplog.records
    )


def test_agentflo_whatsapp_audio_payload_logs_only_safe_shape(
    monkeypatch,
    caplog,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    private_body = "Private voice-note caption"
    private_phone = "+10000000001"
    private_media_url = "https://private.example/media/audio-1"
    private_media_id = "private-media-id-123456789"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "contacts": [{"wa_id": private_phone}],
                    "messages": [{
                        "from": private_phone,
                        "id": "private-message-id-123456789",
                        "type": "audio",
                        "body": private_body,
                        "audio": {
                            "id": private_media_id,
                            "url": private_media_url,
                        },
                        "media": {
                            "id": private_media_id,
                            "link": private_media_url,
                        },
                        "voice": {},
                        "document": {},
                        "image": {},
                        "video": {},
                        "sticker": {},
                    }],
                }
            }]
        }],
    }

    with caplog.at_level(logging.INFO, logger="src.api.main"):
        response = client().post(
            "/api/channels/agentflo/whatsapp",
            json=payload,
        )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "ignored": True,
        "reason": "no_text_message",
    }
    shape_log = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "agentflo_whatsapp_ignored_payload_shape"
    )
    formatted_shape_log = JsonFormatter().format(shape_log)
    cloudwatch_log = json.loads(formatted_shape_log)
    payload_shape = cloudwatch_log["payload_shape"]
    assert payload_shape["top_level_payload_keys"] == ["entry", "object"]
    assert payload_shape["detected_message_type"] == "audio"
    assert payload_shape["messages_array_exists"] is True
    assert payload_shape["message_count"] == 1
    assert payload_shape["first_message_keys"] == [
        "audio",
        "body",
        "document",
        "from",
        "id",
        "image",
        "media",
        "sticker",
        "type",
        "video",
        "voice",
    ]
    assert payload_shape["first_message_type"] == "audio"
    assert payload_shape["first_message_has_audio"] is True
    assert payload_shape["first_message_has_voice"] is True
    assert payload_shape["first_message_has_media"] is True
    assert payload_shape["first_message_has_document"] is True
    assert payload_shape["first_message_has_image"] is True
    assert payload_shape["first_message_has_video"] is True
    assert payload_shape["first_message_has_sticker"] is True
    assert payload_shape["first_message_audio_has_id"] is True
    assert payload_shape["first_message_audio_has_url_or_link"] is True
    assert payload_shape["first_message_media_has_id"] is True
    assert payload_shape["first_message_media_has_url_or_link"] is True
    hostname_log = next(
        record
        for record in caplog.records
        if getattr(record, "event", None)
        == "agentflo_whatsapp_audio_media_hostname"
    )
    assert hostname_log.audio_media_hostname == "private.example"
    assert not hasattr(hostname_log, "audio_media_url")

    formatted_hostname_log = JsonFormatter().format(hostname_log)
    hostname_cloudwatch_log = json.loads(formatted_hostname_log)
    assert hostname_cloudwatch_log["audio_media_hostname"] == "private.example"
    assert private_media_url not in formatted_hostname_log
    assert private_media_id not in formatted_hostname_log

    serialized_log_records = repr([vars(record) for record in caplog.records])
    assert private_body not in formatted_shape_log
    assert private_phone not in formatted_shape_log
    assert private_media_url not in formatted_shape_log
    assert private_media_id not in formatted_shape_log
    assert private_body not in serialized_log_records
    assert private_phone not in serialized_log_records
    assert private_media_url not in serialized_log_records
    assert private_media_id not in serialized_log_records


def test_agentflo_whatsapp_rejects_non_object_payload():
    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json=["not", "an", "object"],
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "error_code": "INVALID_WEBHOOK_PAYLOAD",
        "user_message": "The webhook payload is invalid.",
    }


def test_agentflo_whatsapp_runtime_failure_is_sanitized(monkeypatch):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)

    class FailingAgentRuntimeClient:
        def invoke(self, request):
            raise RuntimeError("private provider failure")

    monkeypatch.setattr(
        main,
        "get_agent_runtime_client",
        lambda: FailingAgentRuntimeClient(),
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "Hello",
            "from": "10000000000",
            "id": "simple-message-2",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "error_code": "AGENT_INVOCATION_FAILED",
        "reply": "I couldn't complete that request right now.",
    }
    assert "private provider failure" not in response.text


def test_agentflo_whatsapp_logs_do_not_include_message_or_full_phone(
    monkeypatch,
    caplog,
):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Safe reply.")
    private_message = "My private synthetic message"
    full_phone = "+10000000000"

    with caplog.at_level(logging.INFO):
        response = client().post(
            "/api/channels/agentflo/whatsapp",
            json={
                "body": private_message,
                "phone_number": full_phone,
                "id": "simple-message-3",
            },
        )

    assert response.status_code == 200
    assert private_message not in caplog.text
    assert full_phone not in caplog.text


def test_agentflo_whatsapp_conversation_storage_failure_is_safe(
    monkeypatch,
    caplog,
):
    services = WhatsAppIdentityServices()
    services.conversation_history.fail_writes = True
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(monkeypatch, SimpleNamespace(), text="Safe reply.")
    private_message = "Private history message"
    full_phone = "+10000000000"

    with caplog.at_level(logging.WARNING, logger="src.api.main"):
        response = client().post(
            "/api/channels/agentflo/whatsapp",
            json={
                "message": private_message,
                "from": full_phone,
                "sender_id": "sender-history-failure",
                "message_id": "history-failure-message-1",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == "Safe reply."
    assert private_message not in caplog.text
    assert full_phone not in caplog.text
    assert "private history failure" not in caplog.text
    failures = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "conversation_history_write_failed"
    ]
    assert [record.direction for record in failures] == ["inbound", "outbound"]
    assert {record.error_code for record in failures} == {
        "CONVERSATION_HISTORY_WRITE_FAILED"
    }


def test_chat_dictionary_runtime_result_preserves_tool_calls(monkeypatch):
    answer = (
        "The restaurant currently accepts cash only. "
        "Delivery uses Cash on Delivery."
    )
    stub_agent_client(
        monkeypatch,
        {
            "text": answer,
            "tool_calls": [
                {
                    "tool_name": "retrieve_restaurant_knowledge",
                    "success": True,
                    "is_write": False,
                    "result": {
                        "success": True,
                        "data": {
                            "results": [
                                {
                                    "location": {
                                        "s3Location": {
                                            "uri": (
                                                "s3://knowledge-documents/"
                                                "approved/global/payments.md"
                                            )
                                        }
                                    }
                                }
                            ]
                        },
                        "user_message": (
                            "I found restaurant information from the "
                            "approved knowledge source."
                        ),
                    },
                    "error_code": None,
                }
            ],
        },
        text=answer,
    )

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={
            "message": "What payment methods are accepted?",
            "session_id": "session",
            "user_id": "user",
            "branch_id": "default",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"

    payload = completed_chat_response(
        test_client,
        response.json()["request_id"],
    )

    assert payload["text"] == answer
    assert payload["write_succeeded"] is False
    assert len(payload["tool_calls"]) == 1

    tool_call = payload["tool_calls"][0]
    assert tool_call["tool_name"] == "retrieve_restaurant_knowledge"
    assert tool_call["success"] is True
    assert tool_call["is_write"] is False
    assert tool_call["result"]["data"]["results"][0][
        "location"
    ]["s3Location"]["uri"].endswith(
        "/approved/global/payments.md"
    )


@pytest.mark.parametrize(
    (
        "tool_name",
        "success",
        "is_write",
        "authoritative_message",
        "model_text",
    ),
    [
        (
            "create_human_assistance_ticket",
            True,
            True,
            (
                "Ticket ID: TKT-20260725-271706\n"
                "Status: Open"
            ),
            (
                "Ticket ID: TKT-20260725-271706\n"
                "Status: Open\n\nI will keep an eye on this for you."
            ),
        ),
        (
            "get_support_ticket_status",
            True,
            False,
            (
                "Ticket ID: TKT-20260725-271706\n"
                "Status: In review"
            ),
            (
                "Ticket ID: TKT-20260725-271706\n"
                "Status: In review\n\nPlease contact us if you need more help."
            ),
        ),
        (
            "handle_order_complaint",
            True,
            True,
            "Please describe what went wrong with your order.",
            (
                "Please describe what went wrong with your order. "
                "I can also suggest menu items."
            ),
        ),
        (
            "get_support_ticket_status",
            False,
            False,
            "Ticket service is temporarily unavailable.",
            "I could not retrieve the ticket. Please try several other options.",
        ),
    ],
)
def test_chat_support_ticket_tool_message_is_metadata_not_response_override(
    monkeypatch,
    tool_name,
    success,
    is_write,
    authoritative_message,
    model_text,
):
    services = IdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[{
                "tool_name": tool_name,
                "success": success,
                "is_write": is_write,
                "result": {
                    "success": success,
                    "user_message": authoritative_message,
                },
                "error_code": None if success else "TICKET_BACKEND_UNAVAILABLE",
            }]
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={"message": "support", "session_id": "session", "user_id": "user"},
    )
    request_id = submitted.json()["request_id"]
    stored = services.agent_requests.requests[request_id]["response"]
    completed = completed_chat_response(test_client, request_id)

    assert stored["text"] == model_text
    assert completed["response"] == model_text
    assert completed["text"] == model_text
    assert completed["tool_calls"][0]["result"]["user_message"] == (
        authoritative_message
    )


def test_chat_preserves_agent_text_when_support_tools_return_messages(monkeypatch):
    first = "The first ticket message."
    last = "The final authoritative ticket message."
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[
                {
                    "tool_name": "create_human_assistance_ticket",
                    "success": True,
                    "is_write": True,
                    "result": {"success": True, "user_message": first},
                    "error_code": None,
                },
                {
                    "tool_name": "get_support_ticket_status",
                    "success": True,
                    "is_write": False,
                    "result": {"success": True, "user_message": last},
                    "error_code": None,
                },
            ]
        ),
        text="Model text from the agent.",
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={"message": "support", "session_id": "session", "user_id": "user"},
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == "Model text from the agent."


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"user_message": None},
        {"user_message": ""},
        {"user_message": " \n\t"},
        {"user_message": 123},
    ],
)
def test_chat_invalid_support_ticket_message_falls_back_to_invocation_text(
    monkeypatch,
    result,
):
    model_text = "The model response remains unchanged."
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[{
                "tool_name": "get_support_ticket_status",
                "success": False,
                "is_write": False,
                "result": result,
                "error_code": "TICKET_DATA_INVALID",
            }]
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={"message": "support", "session_id": "session", "user_id": "user"},
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == model_text


def test_chat_menu_tool_without_menu_data_does_not_replace_invocation_text(monkeypatch):
    model_text = "Here are the matching menu items."
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "user_message": "Internal tool summary.",
                },
                "error_code": None,
            }]
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={"message": "menu", "session_id": "session", "user_id": "user"},
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == model_text


def test_chat_menu_response_is_grounded_in_search_tool_results(monkeypatch):
    model_text = (
        "Here are two current options:\n"
        "1. Classic Pepperoni Pizza - small PKR 899, large PKR 1599\n"
        "2. Pepperoni Feast - from PKR 1299\n"
        "Which item would you like?"
    )
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "product_id": "pepperoni-classic",
                                "name": "Classic Pepperoni Pizza",
                                "currency": "PKR",
                                "base_prices": {
                                    "small": 899,
                                    "large": 1599,
                                },
                            },
                            {
                                "product_id": "pepperoni-feast",
                                "name": "Pepperoni Feast",
                                "currency": "PKR",
                                "starting_price": 1299,
                            },
                        ],
                    },
                    "user_message": "I found current menu options.",
                    "next_action": "present_menu_results",
                    "grounding": {
                        "authoritative_domains": ["menu"],
                        "presentation": {"max_items": 5},
                    },
                },
                "error_code": None,
            }],
            claim_assessment={
                "claims_transactional_progression": False,
                "claimed_actions": [],
                "depends_on_authoritative_state": True,
                "authoritative_state_domains": ["menu"],
                "authoritative_claims_supported": True,
                "presented_authoritative_item_count": 2,
            },
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "hello I would like to order a pepperoni pizza",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )
    assert completed["text"] == model_text
    assert completed["tool_calls"][0]["tool_name"] == "search_menu"


def test_chat_menu_guard_removes_hallucinated_agent_item(monkeypatch):
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {
                        "items": [{
                            "product_id": "pepperoni-classic",
                            "name": "Classic Pepperoni Pizza",
                            "currency": "PKR",
                            "starting_price": 899,
                        }]
                    },
                    "user_message": "I found current menu options.",
                },
                "error_code": None,
            }]
        ),
        text=(
            "You can order Classic Pepperoni Pizza or BBQ Volcano Pizza. "
            "The BBQ Volcano is PKR 999."
        ),
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "show me pepperoni pizza",
            "session_id": "session",
            "user_id": "user",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert "Classic Pepperoni Pizza" in completed["text"]
    assert "BBQ Volcano" not in completed["text"]
    assert "999" not in completed["text"]


def test_web_pepperoni_request_still_uses_existing_agent_flow(monkeypatch):
    services = IdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    captured = {}
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {"items": [{
                        "product_id": "pepperoni-passion",
                        "name": "Pepperoni Passion",
                        "currency": "PKR",
                        "starting_price": 850,
                    }]},
                    "user_message": "I found current menu options.",
                },
            }]
        ),
        text="Pepperoni Passion is available.",
        captured=captured,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "hello I would like to order a pepperoni pizza",
            "session_id": "web-session",
            "user_id": "web-user",
            "channel": "web",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert captured["channel"] == "web"
    assert completed["text"] == (
        "Here are the current menu options I found:\n"
        "1. Pepperoni Passion - from PKR 850\n"
        "Which item would you like?"
    )
    assert completed["tool_calls"][0]["tool_name"] == "search_menu"


def test_chat_does_not_search_menu_outside_agent_tool_calls(monkeypatch):
    services = IdentityServices()
    captured_menu_calls = []

    def search_menu(**kwargs):
        captured_menu_calls.append(kwargs)
        return ToolResponse.ok(
            data={
                "items": [
                    {
                        "product_id": "mushroom-pepperoni",
                        "name": "Mushroom & Pepperoni",
                        "currency": "PKR",
                        "base_prices": {
                            "small": 850,
                            "medium": 1700,
                            "large": 2400,
                        },
                    },
                    {
                        "product_id": "pepperoni-hot",
                        "name": "Pepperoni Hot",
                        "currency": "PKR",
                        "base_prices": {
                            "small": 850,
                            "medium": 1700,
                            "large": 2400,
                        },
                    },
                    {
                        "product_id": "pepperoni-passion",
                        "name": "Pepperoni Passion",
                        "currency": "PKR",
                        "base_prices": {
                            "small": 850,
                            "medium": 1700,
                            "large": 2400,
                        },
                    },
                ],
            },
            user_message="I found current menu options.",
            next_action="present_menu_results",
        )

    services.menu = SimpleNamespace(search_menu=search_menu)
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(tool_calls=[]),
        text=(
            "Hello again! I see you're interested in ordering a pepperoni pizza. "
            "Let me check if there's an existing order or cart for you. Please give me "
            "a moment while I retrieve that information."
        ),
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "hello I would like to order a pepperoni pizza",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )
    assert captured_menu_calls == []
    assert completed["text"] == (
        "Hello again! I see you're interested in ordering a pepperoni pizza. "
        "Let me check if there's an existing order or cart for you. Please give me "
        "a moment while I retrieve that information."
    )


def test_chat_preserves_non_menu_waiting_text(monkeypatch):
    services = IdentityServices()
    captured_menu_calls = []
    services.menu = SimpleNamespace(
        search_menu=lambda **kwargs: captured_menu_calls.append(kwargs)
    )
    monkeypatch.setattr(main, "get_services", lambda: services)
    model_text = "Let me check that for you. Please give me a moment."
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[],
            claim_assessment={
                "claims_transactional_progression": False,
                "claimed_actions": [],
            },
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "hello how are you",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == model_text
    assert captured_menu_calls == []


def test_agentflo_whatsapp_cancel_phrase_uses_main_agent_not_order_coordinator(monkeypatch):
    services = WhatsAppIdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    captured = {}
    stub_agent_client(
        monkeypatch,
        {
            "tool_calls": [{
                "tool_name": "update_order_flow",
                "success": True,
                "is_write": True,
                "result": {
                    "success": True,
                    "user_message": "I've cancelled the pending order.",
                    "data": {
                        "order": {
                            "order_id": "ORD-CANCELLED",
                            "status": "cancelled",
                        }
                    },
                },
                "error_code": None,
            }]
        },
        text="I've cancelled the pending order.",
        captured=captured,
    )

    response = client().post(
        "/api/channels/agentflo/whatsapp",
        json={
            "message": "I don't want to checkout",
            "from": "+10000000000",
            "sender_id": "sender-agent-led-cancel",
            "message_id": "wamid.agent-led-cancel",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["text"] == "I've cancelled the pending order."
    assert captured["message"] == "I don't want to checkout"
    assert captured["channel"] == "whatsapp"


def test_chat_blocks_order_confirmation_text_without_backend_order_result(monkeypatch):
    model_text = "Your order is confirmed and will be delivered."
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[],
            claim_assessment={
                "claims_transactional_progression": True,
                "claimed_actions": ["order_submitted"],
            },
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "confirm",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == (
        "I couldn't verify that change, so I haven't treated it as completed. "
        "Please tell me what you'd like to do next, or ask me to check the current cart."
    )


def test_chat_blocks_llm_generated_whatsapp_cart_summary_without_backend_result(monkeypatch):
    model_text = (
        "Order summary: one large pepperoni pizza with extra cheese. "
        "Total: PKR 2,900. Reply confirm to place it."
    )
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[],
            claim_assessment={
                "claims_transactional_progression": True,
                "claimed_actions": ["cart_progressed"],
            },
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "summarize what I said",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == (
        "I couldn't verify that change, so I haven't treated it as completed. "
        "Please tell me what you'd like to do next, or ask me to check the current cart."
    )


def test_whatsapp_support_intent_uses_main_agent_not_support_coordinator(monkeypatch):
    services = IdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)
    captured = {}
    stub_agent_client(
        monkeypatch,
        {
            "tool_calls": [{
                "tool_name": "handle_order_complaint",
                "success": True,
                "is_write": True,
                "result": {
                    "success": True,
                    "user_message": (
                        "Please provide the Order ID for your complaint."
                    ),
                },
                "error_code": None,
            }]
        },
        text="Please provide the Order ID for your complaint.",
        captured=captured,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "I want to complain about my order",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == (
        "Please provide the Order ID for your complaint."
    )
    assert captured["message"] == "I want to complain about my order"
    assert captured["channel"] == "whatsapp"
    assert completed["tool_calls"][0]["tool_name"] == "handle_order_complaint"


def test_chat_preserves_whatsapp_ticket_text_without_backend_result(monkeypatch):
    model_text = "I've logged your complaint and opened a support ticket."
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[],
            claim_assessment={
                "claims_transactional_progression": False,
                "claimed_actions": [],
            },
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "tell me what happened",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == model_text


def test_chat_blocks_whatsapp_transaction_text_without_backend_result(monkeypatch):
    model_text = "I've added the invented pizza to your cart."
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            tool_calls=[],
            claim_assessment={
                "claims_transactional_progression": True,
                "claimed_actions": ["item_added"],
            },
        ),
        text=model_text,
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "do that",
            "session_id": "session",
            "user_id": "user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == (
        "I couldn't verify that change, so I haven't treated it as completed. "
        "Please tell me what you'd like to do next, or ask me to check the current cart."
    )


def test_old_runtime_missing_grounding_metadata_fails_closed(caplog):
    context = AgentRequestContext(
        user_id="user", agent_session_id="session",
        customer_id="user", channel="whatsapp",
    )
    response = main._chat_response_from_invocation(
        context,
        {
            "customer": {"customer_id": "user", "phone_verified": True},
            "session": {"session_id": "session", "channel": "whatsapp"},
        },
        AgentInvocationResult(
            text="Everything is locked in and checkout is ready.",
            raw_result={"tool_calls": []},
        ),
    )

    assert response.text == (
        "I couldn't verify that change, so I haven't treated it as completed. "
        "Please tell me what you'd like to do next, or ask me to check the current cart."
    )
    completed = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "backend_grounding_completed"
    )
    assert completed.assessment_origin == "boundary_missing_synthetic"
    assert completed.assessment_transport_status == "missing"
    assert completed.backend_grounding_rejection_reason == (
        "unsupported_transactional_effect"
    )
    public = response.model_dump()
    assert "grounding_rejection_reason" not in public
    assert "assessment_origin" not in public
    assert "semantic_classifier_status" not in public


def test_chat_preserves_authoritative_submitted_order_cancel_protection():
    context = AgentRequestContext(
        user_id="user",
        agent_session_id="session",
        customer_id="user",
        channel="whatsapp",
        current_message="cancel my order",
    )
    text = (
        "That order has already been submitted, so I haven't cancelled "
        "the restaurant order."
    )
    invocation = AgentInvocationResult(
        text=text,
        raw_result={
            "claim_assessment": {
                "claims_transactional_progression": False,
                "claimed_actions": [],
            },
            "no_write_authorized": True,
            "tool_calls": [{
                "tool_name": "discard_active_cart",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {"discarded": False},
                    "user_message": "There isn't an active cart to discard.",
                },
                "error_code": None,
            }]
        },
    )

    response = main._chat_response_from_invocation(
        context,
        {
            "customer": {
                "customer_id": "user",
                "display_name": None,
                "phone_e164": None,
                "phone_verified": False,
            },
            "session": {"agent_session_id": "session"},
        },
        invocation,
    )

    assert response.text == text


def test_whatsapp_order_status_uses_agent_response(monkeypatch):
    menu_repository = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}],
        [],
    )
    order_repository = MemoryOrderRepository()
    order_service = OrderService(order_repository, menu_repository)
    order_service.create_pending_from_cart({
        "user_id": "whatsapp-user",
        "agent_session_id": "whatsapp-session",
        "restaurant_id": "restaurant",
        "branch_id": "branch",
        "cart_id": "cart",
        "subtotal": 10,
        "currency": "PKR",
        "items": [{
            "item_id": "item",
            "name": "Item",
            "quantity": 1,
            "selected_options": {},
            "current_price": 10,
        }],
    })
    order_id = next(iter(order_repository.data))
    order_service.update_order_flow("whatsapp-user", order_id, "set_takeaway")
    order_service.update_order_flow("whatsapp-user", order_id, "confirm")
    services = IdentityServices(customer_id="whatsapp-user", session_id="whatsapp-session")
    services.orders = order_service
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(tool_calls=[]),
        text="Let me check and fetch the information.",
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "whats the status of my order",
            "session_id": "whatsapp-session",
            "user_id": "whatsapp-user",
            "customer_id": "whatsapp-user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert order_id
    assert completed["text"] == "Let me check and fetch the information."


def test_whatsapp_order_status_without_confirmed_order_uses_agent_response(monkeypatch):
    menu_repository = MemoryMenuRepository(
        [{"product_id": "item", "name": "Item", "available": True,
          "starting_price": 10, "customization_group_ids": []}],
        [],
    )
    order_repository = MemoryOrderRepository()
    order_service = OrderService(order_repository, menu_repository)
    order_service.create_pending_from_cart({
        "user_id": "whatsapp-user",
        "agent_session_id": "whatsapp-session",
        "restaurant_id": "restaurant",
        "branch_id": "branch",
        "cart_id": "cart",
        "subtotal": 10,
        "currency": "PKR",
        "items": [{
            "item_id": "item",
            "name": "Item",
            "quantity": 1,
            "selected_options": {},
            "current_price": 10,
        }],
    })
    services = IdentityServices(customer_id="whatsapp-user", session_id="whatsapp-session")
    services.orders = order_service
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(tool_calls=[]),
        text="Please wait while I retrieve that information.",
    )

    test_client = client()
    submitted = test_client.post(
        "/api/chat",
        json={
            "message": "whats the status of my order",
            "session_id": "whatsapp-session",
            "user_id": "whatsapp-user",
            "customer_id": "whatsapp-user",
            "channel": "whatsapp",
        },
    )
    completed = completed_chat_response(
        test_client,
        submitted.json()["request_id"],
    )

    assert completed["text"] == "Please wait while I retrieve that information."


def test_public_chat_and_status_serialization_remove_internal_grounding_metadata():
    call = ToolCallResult(
        tool_name="search_menu",
        success=True,
        is_write=False,
        result={
            "success": True,
            "data": {
                "items": [{"product_id": "item-1"}],
                "presentation": {"layout": "business-owned"},
                "offered_options": [{"id": "domain-option"}],
                "immutable_facts": [{"name": "domain-owned"}],
            },
            "grounding": {
                "authoritative_domains": ["menu"],
                "transactional_effects": [],
                "required_next_effect": "item_selected",
                "offered_options": [{"id": "item-1", "label": "First"}],
                "presentation": {"max_items": 5},
            },
        },
    )
    chat = ChatResponse(
        text="Safe",
        session_id="session-1",
        user_id="user-1",
        tool_calls=[call],
    ).model_dump()
    status = main._status_response_from_record({
        "request_id": "request-1",
        "status": "completed",
        "response": {
            "text": "Safe",
            "session_id": "session-1",
            "user_id": "user-1",
            "tool_calls": [{
                "tool_name": call.tool_name,
                "success": call.success,
                "is_write": call.is_write,
                "result": call.result,
                "error_code": call.error_code,
            }],
        },
    }).model_dump()

    for payload in (chat, status):
        serialized = str(payload["tool_calls"])
        assert "grounding" not in serialized
        assert "authoritative_domains" not in serialized
        assert "transactional_effects" not in serialized
        assert "required_next_effect" not in serialized
        assert payload["tool_calls"][0]["result"]["data"]["items"]
        assert payload["tool_calls"][0]["result"]["data"]["presentation"] == {
            "layout": "business-owned"
        }
        assert payload["tool_calls"][0]["result"]["data"]["offered_options"] == [
            {"id": "domain-option"}
        ]
        assert payload["tool_calls"][0]["result"]["data"]["immutable_facts"] == [
            {"name": "domain-owned"}
        ]

    assert call.result["grounding"]["required_next_effect"] == "item_selected"


def test_chat_route_delegates_cart_and_order_language_to_agent(monkeypatch):
    captured = {}

    stub_agent_client(
        monkeypatch,
        SimpleNamespace(message={"content": [{"text": "Agent handled it."}]}),
        text="Agent handled it.",
        captured=captured,
    )

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={
            "message": "whats in my cart and can I place order",
            "session_id": "session",
            "user_id": "user",
            "branch_id": "branch",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert completed_chat_response(test_client, response.json()["request_id"])["text"] == "Agent handled it."
    assert captured == {
        "message": "whats in my cart and can I place order",
        "user_id": "user",
        "agent_session_id": "session",
        "branch_id": "branch",
        "customer_id": "user",
        "customer_name": None,
        "customer_phone": None,
        "channel": "web",
        "request_id": "req-1",
            "expected_write_tool": None,
            "required_effect": None,
            "available_options": None,
    }


def test_successful_chat_logs_exclude_trusted_request_and_session_ids(
    monkeypatch,
    caplog,
):
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(message={"content": [{"text": "Handled."}]}),
        text="Handled.",
    )

    with caplog.at_level(logging.INFO):
        response = client().post(
            "/api/chat",
            json={
                "message": "I need support",
                "session_id": "private-session",
                "user_id": "user",
            },
        )

    assert response.status_code == 200
    lifecycle_records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") in {
            "agent_request_started",
            "agentcore_invocation_completed",
            "agent_request_completed",
        }
    ]
    assert len(lifecycle_records) == 3
    assert all(not hasattr(record, "request_id") for record in lifecycle_records)
    assert all(
        not hasattr(record, "agent_session_id")
        for record in lifecycle_records
    )


def test_chat_uses_persisted_request_id_and_ignores_frontend_value(monkeypatch):
    captured = {}
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(message={"content": [{"text": "Handled."}]}),
        text="Handled.",
        captured=captured,
    )

    response = client().post(
        "/api/chat",
        json={
            "message": "hello",
            "session_id": "session",
            "user_id": "user",
            "request_id": "req-frontend-controlled",
        },
    )

    assert response.status_code == 200
    assert response.json()["request_id"] == "req-1"
    assert captured["request_id"] == "req-1"
    assert captured["request_id"] != "req-frontend-controlled"


def test_chat_false_success_without_write_tool_keeps_text_but_reports_no_write(monkeypatch):
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            message={"content": [{"text": "I added Supreme Pizza to your order."}]},
            tool_calls=[],
        ),
        text="I added Supreme Pizza to your order.",
    )

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "add supreme", "session_id": "session", "user_id": "user"},
    )

    assert response.status_code == 200
    payload = completed_chat_response(test_client, response.json()["request_id"])
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
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            message={"content": [{"text": "I added Supreme Pizza to your order."}]},
            tool_calls=[{
                "tool_name": "start_cart_item_customization",
                "success": True,
                "is_write": True,
                "result": tool_result,
                "error_code": None,
            }],
        ),
        text="I added Supreme Pizza to your order.",
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

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "add supreme", "session_id": "session", "user_id": "user"},
    )

    payload = completed_chat_response(test_client, response.json()["request_id"])
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
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            message={"content": [{"text": "I updated your order."}]},
            tool_calls=[{
                "tool_name": "save_customization_choice",
                "success": False,
                "is_write": True,
                "result": tool_result,
                "error_code": "INVALID_OPTION",
            }],
        ),
        text="I updated your order.",
    )

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "wrong option", "session_id": "session", "user_id": "user"},
    )

    payload = completed_chat_response(test_client, response.json()["request_id"])
    assert payload["write_succeeded"] is False
    assert payload["text"] == "I updated your order."
    assert payload["tool_calls"][0]["error_code"] == "INVALID_OPTION"
    assert payload["tool_calls"][0]["result"]["user_message"] == "Please choose one of the available options."


def test_chat_empty_menu_result_uses_backend_menu_message(monkeypatch):
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            message={"content": [{"text": "Here are some spicy options."}]},
            tool_calls=[{
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {"success": True, "data": {"items": []}, "user_message": "ok"},
                "error_code": None,
            }],
        ),
        text="Here are some spicy options.",
    )

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "spicy", "session_id": "session", "user_id": "user"},
    )

    payload = completed_chat_response(test_client, response.json()["request_id"])
    assert payload["write_succeeded"] is False
    assert payload["text"] == "ok"


def test_chat_order_start_help_text_is_not_false_success(monkeypatch):
    text = "I can help you place an order. What would you like?"
    stub_agent_client(
        monkeypatch,
        SimpleNamespace(
            message={"content": [{"text": text}]},
            tool_calls=[{
                "tool_name": "get_order_status",
                "success": True,
                "is_write": False,
                "result": {"success": True, "data": {"orders": []}, "user_message": "ok"},
                "error_code": None,
            }],
        ),
        text=text,
    )

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "i want to order", "session_id": "session", "user_id": "user"},
    )

    payload = completed_chat_response(test_client, response.json()["request_id"])
    assert payload["write_succeeded"] is False
    assert payload["text"] == text


def test_chat_failed_agent_invocation_returns_failed_status(monkeypatch):
    class FailingAgentRuntimeClient:
        def invoke(self, request):
            raise RuntimeError("provider timeout with internal details")

    monkeypatch.setattr(main, "get_agent_runtime_client", lambda: FailingAgentRuntimeClient())

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "hi", "session_id": "session", "user_id": "user"},
    )
    status_response = test_client.get(f"/api/chat/{response.json()['request_id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["status"] == "failed"
    assert payload["error_code"] == "AGENT_INVOCATION_FAILED"
    assert payload["message"] == "The request could not be completed."
    assert "provider timeout" not in payload["message"]


def test_chat_status_unknown_request_returns_structured_404():
    response = client().get("/api/chat/req-missing")

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "AGENT_REQUEST_NOT_FOUND"


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
    services = IdentityServices(
        session_id="web-new", customer_id="cust-new", rotated=True
    )
    monkeypatch.setattr(main, "get_services", lambda: services)
    stub_agent_client(monkeypatch, SimpleNamespace(message={"content": [{"text": "hi"}]}), text="hi")

    test_client = client()
    response = test_client.post(
        "/api/chat",
        json={"message": "hi", "session_id": "old", "customer_id": "cust-new"},
    )

    payload = completed_chat_response(test_client, response.json()["request_id"])
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
    return make_test_settings(
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


def test_admin_cookie_is_cross_site_for_production(monkeypatch):
    test_client = client()
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: make_test_settings(
            environment="production",
            bedrock_model_id="us.amazon.nova-pro-v1:0",
            admin_username="admin",
            admin_password="secret",
            admin_session_secret="admin-secret-at-least-sixteen",
            frontend_cors_origins="https://main.example.amplifyapp.com",
        ),
    )

    response = test_client.post("/api/admin/login", json={"username": "admin", "password": "secret"})

    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=none" in cookie


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


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/conversations",
        "/api/admin/conversations/conv-1/messages",
    ],
)
def test_admin_conversation_routes_require_authentication(path):
    response = client().get(path)

    assert response.status_code == 401


def test_admin_conversation_list_route_returns_recent_conversations(monkeypatch):
    test_client = client()
    login_admin(test_client, monkeypatch)
    services = IdentityServices()
    services.conversation_history.admin_conversations_result = {
        "conversations": [{
            "conversation_id": "conv-1",
            "channel": "whatsapp",
            "latest_message_preview": "Your order is confirmed.",
            "latest_timestamp_utc": "2026-07-30T10:02:00+00:00",
            "message_count": 2,
            "customer_message_count": 1,
            "agent_message_count": 1,
            "masked_customer_phone": "****1234",
            "latest_delivery_status": "delivered",
        }]
    }
    monkeypatch.setattr(main, "get_services", lambda: services)

    response = test_client.get("/api/admin/conversations?limit=25")

    assert response.status_code == 200
    assert response.json() == services.conversation_history.admin_conversations_result
    assert services.conversation_history.admin_list_calls == [{"limit": 25}]
    assert "+10000001234" not in response.text


def test_admin_conversation_messages_route_returns_ordered_safe_transcript(
    monkeypatch,
):
    test_client = client()
    login_admin(test_client, monkeypatch)
    services = IdentityServices()
    services.conversation_history.admin_messages_result = {
        "conversation_id": "conv-1",
        "channel": "whatsapp",
        "messages": [
            {
                "timestamp_utc": "2026-07-30T10:01:00+00:00",
                "direction": "inbound",
                "sender_type": "customer",
                "message_text": "session_token=[REDACTED]",
                "masked_customer_phone": "****1234",
            },
            {
                "timestamp_utc": "2026-07-30T10:02:00+00:00",
                "direction": "outbound",
                "sender_type": "agent",
                "message_text": "Confirmed.",
                "outbound_status": "accepted",
                "delivery_status": "delivered",
            },
        ],
    }
    monkeypatch.setattr(main, "get_services", lambda: services)

    response = test_client.get("/api/admin/conversations/conv-1/messages")

    assert response.status_code == 200
    body = response.json()
    assert [message["timestamp_utc"] for message in body["messages"]] == [
        "2026-07-30T10:01:00+00:00",
        "2026-07-30T10:02:00+00:00",
    ]
    assert "session_token=abc123" not in response.text
    assert "+10000001234" not in response.text
    assert services.conversation_history.admin_message_calls == ["conv-1"]


def test_admin_conversation_routes_return_empty_results(monkeypatch):
    test_client = client()
    login_admin(test_client, monkeypatch)
    services = IdentityServices()
    monkeypatch.setattr(main, "get_services", lambda: services)

    conversations = test_client.get("/api/admin/conversations")
    messages = test_client.get("/api/admin/conversations/missing/messages")

    assert conversations.status_code == 200
    assert conversations.json() == {"conversations": []}
    assert messages.status_code == 200
    assert messages.json() == {
        "conversation_id": "missing",
        "channel": "whatsapp",
        "messages": [],
    }


ADMIN_TICKET_LIST_KEYS = {
    "ticket_id",
    "user_id",
    "customer_id",
    "customer_name",
    "customer_phone",
    "ticket_type",
    "category",
    "priority",
    "status",
    "order_id",
    "source",
    "created_at",
    "updated_at",
    "version",
}
ADMIN_TICKET_DETAIL_KEYS = ADMIN_TICKET_LIST_KEYS | {
    "description",
    "order_status_snapshot",
    "status_history",
    "priority_history",
    "admin_notes",
    "linked_order",
}


def admin_ticket_list_item(**overrides):
    item = {
        "ticket_id": "TKT-20260724-A1B2C3",
        "user_id": "user-1",
        "customer_id": "customer-1",
        "customer_name": "Ava",
        "customer_phone": "+920000000000",
        "ticket_type": "order_complaint",
        "category": "order_problem",
        "priority": "high",
        "status": "in_review",
        "order_id": "ORD-1",
        "source": "web",
        "created_at": "2026-07-24T10:00:00+00:00",
        "updated_at": "2026-07-24T10:30:00+00:00",
        "version": 2,
    }
    item.update(overrides)
    return item


def admin_ticket_detail(**overrides):
    ticket = {
        **admin_ticket_list_item(),
        "description": "The order arrived incomplete.",
        "order_status_snapshot": "delivered",
        "status_history": [{
            "previous_status": "open",
            "new_status": "in_review",
            "timestamp": "2026-07-24T10:30:00+00:00",
            "actor": "admin-1",
            "reason": "Review started",
        }],
        "priority_history": [{
            "previous_priority": "normal",
            "new_priority": "high",
            "timestamp": "2026-07-24T10:20:00+00:00",
            "actor": "admin-1",
            "reason": None,
        }],
        "admin_notes": [{
            "note_id": "NOTE-20260724-A1B2C3D4",
            "actor": "admin-1",
            "timestamp": "2026-07-24T10:25:00+00:00",
            "text": "Customer contacted.",
        }],
        "linked_order": {
            "order_id": "ORD-1",
            "status": "delivered",
            "fulfillment_method": "delivery",
            "total": 2499,
            "currency": "PKR",
            "created_at": "2026-07-24T09:00:00+00:00",
            "updated_at": "2026-07-24T09:45:00+00:00",
        },
    }
    ticket.update(overrides)
    return ticket


def admin_ticket_cursor_state(
    *,
    statuses=None,
    ticket_type=None,
    priority=None,
):
    selected = statuses or ["open"]
    return {
        "v": 1,
        "kind": "admin_ticket_list",
        "filters": {
            "statuses": selected,
            "ticket_type": ticket_type,
            "priority": priority,
        },
        "positions": {
            status: {
                "after": None,
                "exhausted": False,
            }
            for status in selected
        },
    }


class AdminTicketApiService:
    def __init__(self):
        self.list_result = {
            "tickets": [admin_ticket_list_item()],
            "next_cursor_state": None,
        }
        self.detail_result = {"ticket": admin_ticket_detail()}
        self.list_error = None
        self.detail_error = None
        self.mutation_error = None
        self.list_calls = []
        self.detail_calls = []
        self.mutation_calls = []
        self.cursor_validation_calls = []

    def list_admin_tickets(self, **kwargs):
        self.list_calls.append(deepcopy(kwargs))
        if self.list_error:
            raise self.list_error
        cursor_state = kwargs["cursor_state"]
        if cursor_state is not None:
            statuses = (
                [kwargs["status"]]
                if kwargs["status"] is not None
                else [
                    "open",
                    "in_review",
                    "waiting_for_customer",
                    "resolved",
                    "closed",
                ]
            )
            expected_filters = {
                "statuses": statuses,
                "ticket_type": kwargs["ticket_type"],
                "priority": kwargs["priority"],
            }
            if (
                not isinstance(cursor_state, dict)
                or cursor_state.get("filters") != expected_filters
            ):
                raise AdminTicketError(
                    "INVALID_CURSOR",
                    "The ticket list cursor is invalid.",
                )
        if (
            isinstance(kwargs["limit"], bool)
            or not isinstance(kwargs["limit"], int)
            or not 1 <= kwargs["limit"] <= 100
        ):
            raise AdminTicketError(
                "INVALID_TICKET_LIMIT",
                "The ticket list limit is invalid.",
            )
        return deepcopy(self.list_result)

    def validate_admin_cursor_state(
        self,
        cursor_state,
        *,
        status=None,
        ticket_type=None,
        priority=None,
    ):
        self.cursor_validation_calls.append({
            "cursor_state": deepcopy(cursor_state),
            "status": status,
            "ticket_type": ticket_type,
            "priority": priority,
        })
        statuses = (
            [status]
            if status is not None
            else [
                "open",
                "in_review",
                "waiting_for_customer",
                "resolved",
                "closed",
            ]
        )
        expected_filters = {
            "statuses": statuses,
            "ticket_type": ticket_type,
            "priority": priority,
        }
        if (
            not isinstance(cursor_state, dict)
            or set(cursor_state)
            != {"v", "kind", "filters", "positions"}
            or isinstance(cursor_state.get("v"), bool)
            or cursor_state.get("v") != 1
            or cursor_state.get("kind") != "admin_ticket_list"
            or cursor_state.get("filters") != expected_filters
            or set(cursor_state.get("positions", {})) != set(statuses)
        ):
            raise AdminTicketError(
                "INVALID_CURSOR",
                "The ticket list cursor is invalid.",
            )
        return deepcopy(cursor_state)

    def get_admin_ticket(self, ticket_id):
        self.detail_calls.append(ticket_id)
        if self.detail_error:
            raise self.detail_error
        return deepcopy(self.detail_result)

    def _mutation_result(self, operation, **values):
        self.mutation_calls.append({
            "operation": operation,
            **deepcopy(values),
        })
        if self.mutation_error:
            raise self.mutation_error
        return deepcopy(self.detail_result)

    def update_admin_status(
        self,
        ticket_id,
        status,
        *,
        expected_version,
        actor,
        reason=None,
    ):
        return self._mutation_result(
            "status",
            ticket_id=ticket_id,
            status=status,
            expected_version=expected_version,
            actor=actor,
            reason=reason,
        )

    def update_admin_priority(
        self,
        ticket_id,
        priority,
        *,
        expected_version,
        actor,
        reason=None,
    ):
        return self._mutation_result(
            "priority",
            ticket_id=ticket_id,
            priority=priority,
            expected_version=expected_version,
            actor=actor,
            reason=reason,
        )

    def add_admin_note(
        self,
        ticket_id,
        text,
        *,
        expected_version,
        actor,
    ):
        return self._mutation_result(
            "note",
            ticket_id=ticket_id,
            text=text,
            expected_version=expected_version,
            actor=actor,
        )

    def reopen_admin_ticket(
        self,
        ticket_id,
        *,
        target_status,
        reason,
        expected_version,
        actor,
    ):
        return self._mutation_result(
            "reopen",
            ticket_id=ticket_id,
            target_status=target_status,
            reason=reason,
            expected_version=expected_version,
            actor=actor,
        )


def authenticated_ticket_client(monkeypatch, ticket_service=None):
    test_client = client()
    login_admin(test_client, monkeypatch)
    tickets = ticket_service or AdminTicketApiService()
    monkeypatch.setattr(
        main,
        "get_services",
        lambda: SimpleNamespace(tickets=tickets),
    )
    return test_client, tickets


def signed_ticket_cursor_payload(payload):
    payload_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return signed_ticket_cursor_bytes(payload_bytes)


def signed_ticket_cursor_bytes(payload_bytes):
    encoded = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=")
    signature = hmac.new(
        admin_settings().admin_session_secret.encode("utf-8"),
        b"admin-ticket-http-cursor-v1." + encoded,
        hashlib.sha256,
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return f"{encoded.decode('ascii')}.{encoded_signature.decode('ascii')}"


def valid_ticket_cursor_payload(**overrides):
    payload = {
        "v": 1,
        "kind": "admin_ticket_http_cursor",
        "iat": 2_000_000_000,
        "exp": 2_000_003_600,
        "state": admin_ticket_cursor_state(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    "path",
    [
        "/api/admin/tickets",
        "/api/admin/tickets/TKT-20260724-A1B2C3",
    ],
)
def test_admin_ticket_routes_require_authentication(path):
    response = client().get(path)

    assert response.status_code == 401
    assert response.json()["detail"] == "Admin login required"


@pytest.mark.parametrize("token", ["invalid", None])
def test_admin_ticket_routes_reject_invalid_or_expired_session(
    monkeypatch,
    token,
):
    test_client = client()
    monkeypatch.setattr(main, "get_settings", admin_settings)
    if token is None:
        token = main._sign_admin_payload({"sub": "admin", "exp": 1})
    test_client.cookies.set(main.ADMIN_COOKIE_NAME, token)

    response = test_client.get("/api/admin/tickets")

    assert response.status_code == 401
    assert response.json()["detail"] == "Admin login required"


def test_admin_ticket_list_route_passes_filters_and_returns_exact_schema(
    monkeypatch,
):
    test_client, tickets = authenticated_ticket_client(monkeypatch)

    response = test_client.get(
        "/api/admin/tickets",
        params={
            "status": "in_review",
            "ticket_type": "order_complaint",
            "priority": "high",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {"tickets", "next_cursor"}
    assert set(response.json()["tickets"][0]) == ADMIN_TICKET_LIST_KEYS
    assert response.json()["next_cursor"] is None
    assert tickets.list_calls == [{
        "status": "in_review",
        "ticket_type": "order_complaint",
        "priority": "high",
        "limit": 1,
        "cursor_state": None,
    }]


def test_admin_ticket_list_uses_default_limit(monkeypatch):
    test_client, tickets = authenticated_ticket_client(monkeypatch)

    response = test_client.get("/api/admin/tickets")

    assert response.status_code == 200
    assert tickets.list_calls[0]["limit"] == 25


@pytest.mark.parametrize("limit", [1, 100])
def test_admin_ticket_list_accepts_limit_boundaries(monkeypatch, limit):
    test_client, tickets = authenticated_ticket_client(monkeypatch)
    tickets.list_result = {"tickets": [], "next_cursor_state": None}

    response = test_client.get(
        "/api/admin/tickets",
        params={"limit": limit},
    )

    assert response.status_code == 200
    assert response.json() == {"tickets": [], "next_cursor": None}
    assert tickets.list_calls[0]["limit"] == limit


@pytest.mark.parametrize("limit", [0, 101])
def test_admin_ticket_list_maps_invalid_limits(monkeypatch, limit):
    test_client, _ = authenticated_ticket_client(monkeypatch)

    response = test_client.get(
        "/api/admin/tickets",
        params={"limit": limit},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == (
        "INVALID_TICKET_LIMIT"
    )


def test_admin_ticket_detail_returns_full_admin_projection(monkeypatch):
    test_client, tickets = authenticated_ticket_client(monkeypatch)

    response = test_client.get(
        "/api/admin/tickets/TKT-20260724-A1B2C3"
    )

    assert response.status_code == 200
    ticket = response.json()["ticket"]
    assert set(ticket) == ADMIN_TICKET_DETAIL_KEYS
    assert set(ticket["status_history"][0]) == {
        "previous_status",
        "new_status",
        "timestamp",
        "actor",
        "reason",
    }
    assert set(ticket["priority_history"][0]) == {
        "previous_priority",
        "new_priority",
        "timestamp",
        "actor",
        "reason",
    }
    assert set(ticket["admin_notes"][0]) == {
        "note_id",
        "actor",
        "timestamp",
        "text",
    }
    assert set(ticket["linked_order"]) == {
        "order_id",
        "status",
        "fulfillment_method",
        "total",
        "currency",
        "created_at",
        "updated_at",
    }
    assert not {"PK", "SK", "GSI1PK", "GSI2PK"} & ticket.keys()
    assert tickets.detail_calls == ["TKT-20260724-A1B2C3"]


@pytest.mark.parametrize("linked_order", [None, admin_ticket_detail()["linked_order"]])
def test_admin_ticket_detail_allows_nullable_linked_order(
    monkeypatch,
    linked_order,
):
    service = AdminTicketApiService()
    service.detail_result["ticket"]["linked_order"] = linked_order
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = test_client.get(
        "/api/admin/tickets/TKT-20260724-A1B2C3"
    )

    assert response.status_code == 200
    assert response.json()["ticket"]["linked_order"] == linked_order


def test_admin_ticket_cursor_round_trip_is_stable_and_unpadded(monkeypatch):
    monkeypatch.setattr(main, "get_settings", admin_settings)
    monkeypatch.setattr(main, "_admin_ticket_cursor_now", lambda: 2_000_000_000)
    state = admin_ticket_cursor_state()

    first = main._encode_admin_ticket_cursor(state)
    second = main._encode_admin_ticket_cursor(state)

    assert first == second
    assert first.count(".") == 1
    assert "=" not in first
    assert main._decode_admin_ticket_cursor(first) == state


def test_admin_ticket_list_returns_opaque_cursor_and_continues(monkeypatch):
    test_client, tickets = authenticated_ticket_client(monkeypatch)
    state = admin_ticket_cursor_state(
        statuses=["open"],
        ticket_type="human_assistance",
        priority="normal",
    )
    tickets.list_result = {
        "tickets": [admin_ticket_list_item(
            ticket_type="human_assistance",
            priority="normal",
            status="open",
            order_id=None,
        )],
        "next_cursor_state": state,
    }
    first = test_client.get(
        "/api/admin/tickets",
        params={
            "status": "open",
            "ticket_type": "human_assistance",
            "priority": "normal",
            "limit": 1,
        },
    )
    public_cursor = first.json()["next_cursor"]
    tickets.list_result = {"tickets": [], "next_cursor_state": None}

    second = test_client.get(
        "/api/admin/tickets",
        params={
            "status": "open",
            "ticket_type": "human_assistance",
            "priority": "normal",
            "limit": 100,
            "cursor": public_cursor,
        },
    )

    assert first.status_code == 200
    assert isinstance(public_cursor, str)
    assert "next_cursor_state" not in first.text
    assert "admin_ticket_list" not in public_cursor
    assert second.status_code == 200
    assert tickets.list_calls[-1]["cursor_state"] == state
    assert tickets.list_calls[-1]["limit"] == 100
    assert tickets.cursor_validation_calls == [{
        "cursor_state": state,
        "status": "open",
        "ticket_type": "human_assistance",
        "priority": "normal",
    }]


def test_admin_ticket_list_does_not_sign_invalid_service_cursor_state(
    monkeypatch,
):
    service = AdminTicketApiService()
    service.list_result = {
        "tickets": [admin_ticket_list_item()],
        "next_cursor_state": {
            "invalid": "TICKET#private-cursor-key",
        },
    }
    test_client, _ = authenticated_ticket_client(monkeypatch, service)
    encoder_calls = []

    def capture_encoder(state):
        encoder_calls.append(state)
        raise AssertionError("invalid state must not be signed")

    monkeypatch.setattr(
        main,
        "_encode_admin_ticket_cursor",
        capture_encoder,
    )

    response = test_client.get("/api/admin/tickets")

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "error_code": "TICKET_INTERNAL_ERROR",
            "user_message": (
                "Ticket service returned an invalid response."
            ),
        }
    }
    assert encoder_calls == []
    assert "next_cursor_state" not in response.text
    assert "TICKET#private-cursor-key" not in response.text
    assert service.cursor_validation_calls == [{
        "cursor_state": {
            "invalid": "TICKET#private-cursor-key",
        },
        "status": None,
        "ticket_type": None,
        "priority": None,
    }]


@pytest.mark.parametrize(
    "changed",
    [
        {"status": "closed"},
        {"ticket_type": "order_complaint"},
        {"priority": "urgent"},
    ],
)
def test_admin_ticket_cursor_rejects_changed_filters(monkeypatch, changed):
    test_client, _ = authenticated_ticket_client(monkeypatch)
    state = admin_ticket_cursor_state(
        statuses=["open"],
        ticket_type="human_assistance",
        priority="normal",
    )
    cursor = main._encode_admin_ticket_cursor(state)
    params = {
        "status": "open",
        "ticket_type": "human_assistance",
        "priority": "normal",
        "cursor": cursor,
    }
    params.update(changed)

    response = test_client.get("/api/admin/tickets", params=params)

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "error_code": "INVALID_CURSOR",
        "user_message": "The ticket cursor is invalid.",
    }


def cursor_payload_cases():
    return [
        valid_ticket_cursor_payload(v=2),
        valid_ticket_cursor_payload(v=True),
        valid_ticket_cursor_payload(kind="admin_session"),
        valid_ticket_cursor_payload(iat=True),
        valid_ticket_cursor_payload(exp=True),
        valid_ticket_cursor_payload(iat=2_000_000_000, exp=2_000_000_000),
        valid_ticket_cursor_payload(exp=2_000_003_601),
        valid_ticket_cursor_payload(exp=1_999_999_999),
        valid_ticket_cursor_payload(iat=2_000_000_061),
        {
            key: value
            for key, value in valid_ticket_cursor_payload().items()
            if key != "state"
        },
        {**valid_ticket_cursor_payload(), "extra": True},
        valid_ticket_cursor_payload(state={"invalid": True}),
    ]


@pytest.mark.parametrize("payload", cursor_payload_cases())
def test_admin_ticket_cursor_payload_failures_are_sanitized(
    monkeypatch,
    payload,
):
    test_client, _ = authenticated_ticket_client(monkeypatch)
    monkeypatch.setattr(main, "_admin_ticket_cursor_now", lambda: 2_000_000_000)
    cursor = signed_ticket_cursor_payload(payload)

    response = test_client.get(
        "/api/admin/tickets",
        params={"status": "open", "cursor": cursor},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "error_code": "INVALID_CURSOR",
        "user_message": "The ticket cursor is invalid.",
    }
    assert "TICKET#" not in response.text


@pytest.mark.parametrize(
    "cursor_factory",
    [
        lambda valid: f"x{valid[1:]}",
        lambda valid: f"{valid[:-1]}x",
        lambda valid: valid.split(".", 1)[0][:-1] + "." + valid.split(".", 1)[1],
        lambda valid: valid.split(".", 1)[0] + "." + valid.split(".", 1)[1][:-1],
        lambda valid: valid + ".extra",
        lambda _valid: "***.***",
        lambda _valid: "+/.+/",
        lambda _valid: "eA.invalid",
        lambda _valid: "e30.invalid",
        lambda _valid: "x" * (16 * 1024 + 1),
    ],
)
def test_admin_ticket_cursor_encoding_failures_are_sanitized(
    monkeypatch,
    cursor_factory,
):
    test_client, _ = authenticated_ticket_client(monkeypatch)
    monkeypatch.setattr(main, "_admin_ticket_cursor_now", lambda: 2_000_000_000)
    valid = main._encode_admin_ticket_cursor(admin_ticket_cursor_state())

    response = test_client.get(
        "/api/admin/tickets",
        params={"status": "open", "cursor": cursor_factory(valid)},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "error_code": "INVALID_CURSOR",
        "user_message": "The ticket cursor is invalid.",
    }


@pytest.mark.parametrize("payload_bytes", [b"\xff", b"{"])
def test_admin_ticket_cursor_rejects_signed_invalid_utf8_or_json(
    monkeypatch,
    payload_bytes,
):
    test_client, _ = authenticated_ticket_client(monkeypatch)
    cursor = signed_ticket_cursor_bytes(payload_bytes)

    response = test_client.get(
        "/api/admin/tickets",
        params={"cursor": cursor},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "error_code": "INVALID_CURSOR",
        "user_message": "The ticket cursor is invalid.",
    }


def duplicate_key_cursor_json_cases():
    position = '{"after":null,"exhausted":false}'
    filters = (
        '{"statuses":["open"],"ticket_type":null,"priority":null}'
    )
    positions = f'{{"open":{position}}}'
    state = (
        '{"v":1,"kind":"admin_ticket_list",'
        f'"filters":{filters},"positions":{positions}}}'
    )
    payload_fields = (
        '"kind":"admin_ticket_http_cursor",'
        '"iat":2000000000,"exp":2000003600,'
        f'"state":{state}'
    )
    return [
        f'{{"v":1,"v":1,{payload_fields}}}',
        (
            '{"v":1,"kind":"admin_ticket_http_cursor",'
            '"iat":2000000000,"exp":2000003600,'
            '"state":{"v":1,"v":1,"kind":"admin_ticket_list",'
            f'"filters":{filters},"positions":{positions}}}}}'
        ),
        (
            '{"v":1,"kind":"admin_ticket_http_cursor",'
            '"iat":2000000000,"exp":2000003600,'
            '"state":{"v":1,"kind":"admin_ticket_list",'
            '"filters":{"statuses":["open"],'
            '"statuses":["open"],"ticket_type":null,'
            f'"priority":null}},"positions":{positions}}}}}'
        ),
        (
            '{"v":1,"kind":"admin_ticket_http_cursor",'
            '"iat":2000000000,"exp":2000003600,'
            '"state":{"v":1,"kind":"admin_ticket_list",'
            f'"filters":{filters},"positions":{{'
            f'"open":{position},"open":{position}}}}}}}'
        ),
        (
            '{"v":1,"kind":"admin_ticket_http_cursor",'
            '"iat":2000000000,"exp":2000003600,'
            '"state":{"v":1,"kind":"admin_ticket_list",'
            f'"filters":{filters},"positions":{{'
            '"open":{"after":null,"after":null,'
            '"exhausted":false}}}}}'
        ),
    ]


@pytest.mark.parametrize("payload_json", duplicate_key_cursor_json_cases())
def test_admin_ticket_cursor_rejects_duplicate_json_keys(
    monkeypatch,
    payload_json,
):
    test_client, tickets = authenticated_ticket_client(monkeypatch)
    monkeypatch.setattr(
        main,
        "_admin_ticket_cursor_now",
        lambda: 2_000_000_000,
    )
    cursor = signed_ticket_cursor_bytes(payload_json.encode("utf-8"))

    response = test_client.get(
        "/api/admin/tickets",
        params={"status": "open", "cursor": cursor},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "error_code": "INVALID_CURSOR",
            "user_message": "The ticket cursor is invalid.",
        }
    }
    assert tickets.list_calls == []


def test_admin_session_and_ticket_cursor_are_not_interchangeable(monkeypatch):
    test_client, _ = authenticated_ticket_client(monkeypatch)
    session_token = test_client.cookies.get(main.ADMIN_COOKIE_NAME)
    ticket_cursor = main._encode_admin_ticket_cursor(
        admin_ticket_cursor_state()
    )

    cursor_response = test_client.get(
        "/api/admin/tickets",
        params={"status": "open", "cursor": session_token},
    )
    test_client.cookies.clear()
    test_client.cookies.set(main.ADMIN_COOKIE_NAME, ticket_cursor)
    auth_response = test_client.get("/api/admin/tickets")

    assert cursor_response.status_code == 400
    assert cursor_response.json()["detail"]["error_code"] == "INVALID_CURSOR"
    assert auth_response.status_code == 401


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (
            AdminTicketError(
                "INVALID_TICKET_STATUS",
                "The ticket status is invalid.",
            ),
            400,
        ),
        (
            AdminTicketError(
                "INVALID_TICKET_TYPE",
                "The ticket type is invalid.",
            ),
            400,
        ),
        (
            AdminTicketError(
                "INVALID_TICKET_PRIORITY",
                "The ticket priority is invalid.",
            ),
            400,
        ),
        (
            AdminTicketError(
                "INVALID_TICKET_LIMIT",
                "The ticket list limit is invalid.",
            ),
            400,
        ),
        (
            AdminTicketError(
                "INVALID_CURSOR",
                "The ticket list cursor is invalid.",
            ),
            400,
        ),
        (
            AdminTicketError(
                "TICKET_PAGINATION_STALLED",
                "Ticket pagination could not make progress. Please retry.",
                retryable=True,
            ),
            409,
        ),
        (
            AdminTicketError(
                "TICKET_DATA_INVALID",
                "The ticket record is invalid.",
            ),
            409,
        ),
    ],
)
def test_admin_ticket_list_domain_error_mapping(
    monkeypatch,
    error,
    status_code,
):
    service = AdminTicketApiService()
    service.list_error = error
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = test_client.get("/api/admin/tickets")

    assert response.status_code == status_code
    assert response.json()["detail"] == {
        "error_code": error.error_code,
        "user_message": (
            "The ticket cursor is invalid."
            if error.error_code == "INVALID_CURSOR"
            else error.user_message
        ),
    }


def test_admin_ticket_detail_not_found_mapping_is_identical_for_ids(
    monkeypatch,
):
    service = AdminTicketApiService()
    service.detail_error = AdminTicketError(
        "TICKET_NOT_FOUND",
        "The ticket could not be found.",
    )
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    valid = test_client.get(
        "/api/admin/tickets/TKT-20260724-A1B2C3"
    )
    malformed = test_client.get("/api/admin/tickets/not-a-ticket")

    assert valid.status_code == malformed.status_code == 404
    assert valid.json() == malformed.json() == {
        "detail": {
            "error_code": "TICKET_NOT_FOUND",
            "user_message": "The ticket could not be found.",
        }
    }


def test_admin_ticket_backend_failure_is_sanitized(monkeypatch):
    service = AdminTicketApiService()
    service.list_error = RuntimeError(
        "ProvisionedThroughputExceededException: private-key"
    )
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = test_client.get("/api/admin/tickets")

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error_code": "TICKET_BACKEND_UNAVAILABLE",
        "user_message": "Ticket service is temporarily unavailable.",
    }
    assert "private-key" not in response.text


@pytest.mark.parametrize("operation", ["list", "detail"])
def test_admin_ticket_invalid_service_output_is_sanitized(
    monkeypatch,
    operation,
):
    service = AdminTicketApiService()
    if operation == "list":
        service.list_result["tickets"][0]["unexpected"] = "private"
    else:
        service.detail_result["ticket"]["unexpected"] = "private"
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = test_client.get(
        "/api/admin/tickets"
        if operation == "list"
        else "/api/admin/tickets/TKT-20260724-A1B2C3"
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "error_code": "TICKET_INTERNAL_ERROR",
        "user_message": "Ticket service returned an invalid response.",
    }
    assert "unexpected" not in response.text


ADMIN_TICKET_MUTATION_CASES = [
    {
        "operation": "status",
        "method": "patch",
        "path": "/api/admin/tickets/TKT-20260724-A1B2C3/status",
        "payload": {
            "status": "in_review",
            "reason": None,
            "expected_version": 2,
        },
    },
    {
        "operation": "priority",
        "method": "patch",
        "path": "/api/admin/tickets/TKT-20260724-A1B2C3/priority",
        "payload": {
            "priority": "urgent",
            "reason": "Customer impact",
            "expected_version": 2,
        },
    },
    {
        "operation": "note",
        "method": "post",
        "path": "/api/admin/tickets/TKT-20260724-A1B2C3/notes",
        "payload": {
            "text": "  Preserve this note exactly.\n",
            "expected_version": 2,
        },
    },
    {
        "operation": "reopen",
        "method": "post",
        "path": "/api/admin/tickets/TKT-20260724-A1B2C3/reopen",
        "payload": {
            "target_status": "open",
            "reason": "Customer replied",
            "expected_version": 2,
        },
    },
]


def request_admin_ticket_mutation(test_client, case, payload=None):
    return getattr(test_client, case["method"])(
        case["path"],
        json=case["payload"] if payload is None else payload,
    )


@pytest.mark.parametrize("case", ADMIN_TICKET_MUTATION_CASES)
def test_admin_ticket_mutation_routes_require_authentication(case):
    response = request_admin_ticket_mutation(client(), case)

    assert response.status_code == 401
    assert response.json()["detail"] == "Admin login required"


@pytest.mark.parametrize("case", ADMIN_TICKET_MUTATION_CASES)
@pytest.mark.parametrize("token_kind", ["invalid", "expired", "cursor"])
def test_admin_ticket_mutations_reject_invalid_authentication(
    monkeypatch,
    case,
    token_kind,
):
    test_client = client()
    service = AdminTicketApiService()
    monkeypatch.setattr(main, "get_settings", admin_settings)
    monkeypatch.setattr(
        main,
        "get_services",
        lambda: SimpleNamespace(tickets=service),
    )
    if token_kind == "invalid":
        token = "invalid"
    elif token_kind == "expired":
        token = main._sign_admin_payload({"sub": "admin", "exp": 1})
    else:
        token = main._encode_admin_ticket_cursor(
            admin_ticket_cursor_state()
        )
    test_client.cookies.set(main.ADMIN_COOKIE_NAME, token)

    response = request_admin_ticket_mutation(test_client, case)

    assert response.status_code == 401
    assert service.mutation_calls == []


@pytest.mark.parametrize("case", ADMIN_TICKET_MUTATION_CASES)
def test_admin_ticket_mutation_routes_pass_trusted_actor(
    monkeypatch,
    case,
):
    test_client, service = authenticated_ticket_client(monkeypatch)

    response = request_admin_ticket_mutation(test_client, case)

    assert response.status_code == 200
    assert set(response.json()) == {"ticket"}
    assert set(response.json()["ticket"]) == ADMIN_TICKET_DETAIL_KEYS
    call = service.mutation_calls[0]
    assert call["operation"] == case["operation"]
    assert call["ticket_id"] == "TKT-20260724-A1B2C3"
    assert call["expected_version"] == 2
    assert call["actor"] == "admin"
    assert "sub" not in call
    if case["operation"] == "note":
        assert call["text"] == "  Preserve this note exactly.\n"


@pytest.mark.parametrize("actor", [None, "", 1, "a" * 201])
def test_admin_ticket_mutation_rejects_invalid_authenticated_actor(
    monkeypatch,
    actor,
):
    test_client = client()
    service = AdminTicketApiService()
    monkeypatch.setattr(main, "get_settings", admin_settings)
    monkeypatch.setattr(
        main,
        "get_services",
        lambda: SimpleNamespace(tickets=service),
    )
    token = main._sign_admin_payload({
        "sub": actor,
        "exp": 2_000_003_600,
    })
    monkeypatch.setattr(main.time, "time", lambda: 2_000_000_000)
    test_client.cookies.set(main.ADMIN_COOKIE_NAME, token)

    response = request_admin_ticket_mutation(
        test_client,
        ADMIN_TICKET_MUTATION_CASES[0],
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Admin login required"
    assert service.mutation_calls == []


@pytest.mark.parametrize("case", ADMIN_TICKET_MUTATION_CASES)
@pytest.mark.parametrize(
    "invalid_version",
    [None, True, False, 0, -1, "2", 2.0],
)
def test_admin_ticket_mutation_expected_version_is_strict(
    monkeypatch,
    case,
    invalid_version,
):
    test_client, service = authenticated_ticket_client(monkeypatch)
    payload = deepcopy(case["payload"])
    if invalid_version is None:
        payload.pop("expected_version")
    else:
        payload["expected_version"] = invalid_version

    response = request_admin_ticket_mutation(
        test_client,
        case,
        payload,
    )

    assert response.status_code == 422
    assert service.mutation_calls == []


@pytest.mark.parametrize("case", ADMIN_TICKET_MUTATION_CASES)
@pytest.mark.parametrize(
    "extra_field",
    [
        "actor",
        "admin",
        "username",
        "user_id",
        "customer_id",
        "note_id",
        "timestamp",
        "version",
        "status_history",
        "priority_history",
        "admin_notes",
        "linked_order",
        "PK",
        "SK",
    ],
)
def test_admin_ticket_mutation_request_rejects_extra_fields(
    monkeypatch,
    case,
    extra_field,
):
    test_client, service = authenticated_ticket_client(monkeypatch)
    payload = {**case["payload"], extra_field: "forged"}

    response = request_admin_ticket_mutation(
        test_client,
        case,
        payload,
    )

    assert response.status_code == 422
    assert service.mutation_calls == []


@pytest.mark.parametrize(
    ("case_index", "field", "value"),
    [
        (0, "status", 1),
        (0, "reason", 1),
        (1, "priority", 1),
        (1, "reason", 1),
        (2, "text", 1),
        (3, "target_status", 1),
        (3, "reason", None),
        (3, "reason", 1),
    ],
)
def test_admin_ticket_mutation_request_types_are_strict(
    monkeypatch,
    case_index,
    field,
    value,
):
    case = ADMIN_TICKET_MUTATION_CASES[case_index]
    test_client, service = authenticated_ticket_client(monkeypatch)
    payload = {**case["payload"], field: value}

    response = request_admin_ticket_mutation(
        test_client,
        case,
        payload,
    )

    assert response.status_code == 422
    assert service.mutation_calls == []


@pytest.mark.parametrize(
    ("case_index", "error", "status_code"),
    [
        (
            0,
            AdminTicketError(
                "INVALID_TICKET_STATUS",
                "The ticket status is invalid.",
            ),
            400,
        ),
        (
            1,
            AdminTicketError(
                "INVALID_TICKET_PRIORITY",
                "The ticket priority is invalid.",
            ),
            400,
        ),
        (
            0,
            AdminTicketError(
                "INVALID_TICKET_TRANSITION",
                "The requested ticket status transition is not allowed.",
            ),
            400,
        ),
        (
            3,
            AdminTicketError(
                "INVALID_REOPEN_TARGET",
                "A reopened ticket must be open or in review.",
            ),
            400,
        ),
        (
            0,
            AdminTicketError(
                "INVALID_TICKET_VERSION",
                "The expected ticket version is invalid.",
            ),
            400,
        ),
        (
            2,
            AdminTicketError(
                "NOTE_REQUIRED",
                "An administrator note is required.",
            ),
            400,
        ),
        (
            2,
            AdminTicketError(
                "NOTE_TOO_LONG",
                "A note cannot exceed 2000 characters.",
            ),
            400,
        ),
        (
            3,
            AdminTicketError(
                "REOPEN_REASON_REQUIRED",
                "A reason is required to reopen a ticket.",
            ),
            400,
        ),
        (
            0,
            AdminTicketError(
                "TICKET_NOT_FOUND",
                "The ticket could not be found.",
            ),
            404,
        ),
        (
            0,
            AdminTicketError(
                "TICKET_VERSION_CONFLICT",
                "The ticket changed before this update could be saved.",
            ),
            409,
        ),
        (
            2,
            AdminTicketError(
                "TICKET_ITEM_TOO_LARGE",
                "The ticket has reached its maximum stored size.",
            ),
            409,
        ),
        (
            1,
            AdminTicketError(
                "TICKET_DATA_INVALID",
                "The ticket record is invalid.",
            ),
            409,
        ),
        (
            2,
            AdminTicketError(
                "NOTE_ID_GENERATION_FAILED",
                "Unable to create a unique note ID. Please retry.",
                retryable=True,
            ),
            503,
        ),
        (
            2,
            AdminTicketError(
                "ADMIN_NOTE_LIMIT_REACHED",
                "The ticket cannot accept more admin notes.",
            ),
            409,
        ),
        (
            0,
            AdminTicketError(
                "STATUS_HISTORY_LIMIT_REACHED",
                "The ticket cannot accept more status history entries.",
            ),
            409,
        ),
        (
            1,
            AdminTicketError(
                "PRIORITY_HISTORY_LIMIT_REACHED",
                "The ticket cannot accept more priority history entries.",
            ),
            409,
        ),
    ],
)
def test_admin_ticket_mutation_domain_error_mapping(
    monkeypatch,
    case_index,
    error,
    status_code,
):
    service = AdminTicketApiService()
    service.mutation_error = error
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = request_admin_ticket_mutation(
        test_client,
        ADMIN_TICKET_MUTATION_CASES[case_index],
    )

    assert response.status_code == status_code
    assert response.json() == {
        "detail": {
            "error_code": error.error_code,
            "user_message": error.user_message,
        }
    }
    assert "retryable" not in response.text


@pytest.mark.parametrize("case", ADMIN_TICKET_MUTATION_CASES)
def test_admin_ticket_mutation_backend_failure_is_sanitized(
    monkeypatch,
    case,
):
    service = AdminTicketApiService()
    service.mutation_error = RuntimeError("private backend failure")
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = request_admin_ticket_mutation(test_client, case)

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == (
        "TICKET_BACKEND_UNAVAILABLE"
    )
    assert "private backend failure" not in response.text


def test_admin_ticket_mutation_preserves_http_exception(monkeypatch):
    service = AdminTicketApiService()
    service.mutation_error = HTTPException(
        status_code=418,
        detail="controlled",
    )
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = request_admin_ticket_mutation(
        test_client,
        ADMIN_TICKET_MUTATION_CASES[0],
    )

    assert response.status_code == 418
    assert response.json() == {"detail": "controlled"}


@pytest.mark.parametrize("case", ADMIN_TICKET_MUTATION_CASES)
@pytest.mark.parametrize("invalid_shape", ["extra", "missing", "version"])
def test_admin_ticket_mutation_invalid_response_is_sanitized(
    monkeypatch,
    case,
    invalid_shape,
):
    service = AdminTicketApiService()
    if invalid_shape == "extra":
        service.detail_result["ticket"]["PK"] = "TICKET#private"
    elif invalid_shape == "missing":
        service.detail_result["ticket"].pop("status")
    else:
        service.detail_result["ticket"]["version"] = "3"
    test_client, _ = authenticated_ticket_client(monkeypatch, service)

    response = request_admin_ticket_mutation(test_client, case)

    assert response.status_code == 500
    assert response.json()["detail"]["error_code"] == (
        "TICKET_INTERNAL_ERROR"
    )
    assert "TICKET#private" not in response.text
