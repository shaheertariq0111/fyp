from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.whatsapp_conversation_service import (
    UNGROUNDED_ORDER_SUBMISSION_ERROR_CODE,
    UNGROUNDED_ORDER_SUBMISSION_FALLBACK,
    WHATSAPP_MENU_LINK_FORBIDDEN_ERROR_CODE,
    WHATSAPP_MENU_LINK_FORBIDDEN_FALLBACK,
    PreparedWhatsAppConversation,
    WhatsAppConversationReply,
    WhatsAppConversationService,
    authoritative_order_submission_from_response,
    build_whatsapp_identity,
    submitted_order_id_from_response,
    whatsapp_reply_from_response,
)
from src.models.tool_responses import ToolResponse
from src.api.whatsapp import WhatsAppInboundMessage


ORDER_ID = "ORD-STRUCTURED-123"
SUBMISSION_CONFIRMATION = (
    "Your order has been confirmed and sent to the restaurant.\n"
    "\n"
    f"Order ID: {ORDER_ID}\n"
    "Status: Submitted to restaurant\n"
    "\n"
    "Please keep this Order ID for tracking."
)
STATUS_MESSAGE = (
    f"Order ID: {ORDER_ID}\n"
    "Status: Submitted to restaurant"
)


class IdentityCustomers:
    def __init__(self, canonical_customer_id):
        self.canonical_customer_id = canonical_customer_id

    def update_profile(self, customer_id, **_kwargs):
        return ToolResponse.ok(
            data={"customer": {"customer_id": self.canonical_customer_id}},
            user_message="saved",
        )


def identity_inbound():
    return WhatsAppInboundMessage(
        text="hello",
        customer_number="+15550123456",
        customer_name="Customer",
        sender_id="sender-safe",
        message_id="message-safe",
    )


def test_shared_whatsapp_identity_uses_canonical_customer_and_stable_session():
    services = SimpleNamespace(customers=IdentityCustomers("cust-canonical"))

    customer_id, session_id = build_whatsapp_identity(
        identity_inbound(), lambda: services
    )

    assert customer_id == "cust-canonical"
    assert session_id.startswith("whatsapp-")
    assert len(session_id) == len("whatsapp-") + 32


def test_shared_whatsapp_identity_keeps_synthetic_customer_for_new_phone():
    services = SimpleNamespace(customers=IdentityCustomers(None))
    services.customers.update_profile = lambda customer_id, **_kwargs: ToolResponse.ok(
        data={"customer": {"customer_id": customer_id}},
        user_message="saved",
    )

    customer_id, session_id = build_whatsapp_identity(
        identity_inbound(), lambda: services
    )

    assert customer_id == session_id
    assert customer_id.startswith("whatsapp-")


def test_text_and_shared_whatsapp_identity_use_same_resolution(monkeypatch):
    from src.api import main

    services = SimpleNamespace(customers=IdentityCustomers("cust-canonical"))
    monkeypatch.setattr(main, "get_services", lambda: services)

    assert main._whatsapp_identity(identity_inbound()) == build_whatsapp_identity(
        identity_inbound(), lambda: services
    )


def tool_call(
    tool_name="confirm_order",
    *,
    success=True,
    status="submitted_to_restaurant",
    order_id=ORDER_ID,
    is_write=True,
    result_success=None,
    include_agent=True,
    agent_order_id=None,
    confirmation_text=None,
):
    nested_success = success if result_success is None else result_success
    result = {
        "success": nested_success,
        "data": {"status": status, "order_id": order_id},
    }
    if include_agent:
        result["agent"] = {
            "submitted_order_id": (
                order_id if agent_order_id is None else agent_order_id
            ),
            "submission_confirmation": (
                SUBMISSION_CONFIRMATION
                if confirmation_text is None
                else confirmation_text
            ),
        }
    return {
        "tool_name": tool_name,
        "success": success,
        "is_write": is_write,
        "result": result,
        "error_code": None,
    }


def order_status_tool_call(*, message=STATUS_MESSAGE):
    return {
        "tool_name": "get_order_status",
        "success": True,
        "is_write": False,
        "result": {
            "success": True,
            "data": {
                "order": {
                    "order_id": ORDER_ID,
                    "status": "submitted_to_restaurant",
                }
            },
            "user_message": message,
            "agent": {
                "selected_order_id": ORDER_ID,
                "status_message": message,
            },
        },
        "error_code": None,
    }


def response(*calls, **overrides):
    value = {
        "text": "Normal assistant reply",
        "tool_calls": list(calls),
        "write_succeeded": False,
        "state": {},
        "data": {},
    }
    value.update(overrides)
    return value


class FakeProcessor:
    def __init__(self, record, *, outcome="completed"):
        self.record = record
        self.outcome = outcome
        self.calls = []

    def invoke_prepared(self, prepared, **kwargs):
        self.calls.append((prepared, kwargs))
        return SimpleNamespace(
            record=self.record,
            context=SimpleNamespace(
                agent_session_id="session-safe",
                customer_id="customer-safe",
            ),
            outcome=self.outcome,
        )


class ForbiddenProvider:
    def __call__(self):
        raise AssertionError("submission extraction must not access services")


class RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message, *, extra):
        self.warnings.append((message, extra))


def conversation_result(persisted_response, *, outcome="completed", logger=None):
    record = {
        "request_id": "request-safe",
        "status": "completed",
        "response": persisted_response,
    }
    processor = FakeProcessor(record, outcome=outcome)
    service = WhatsAppConversationService(
        services_provider=ForbiddenProvider(),
        processor=processor,
        identity_builder=lambda _message: ("customer-safe", "session-safe"),
        gateway_provider=ForbiddenProvider(),
        logger=logger,
    )
    prepared = PreparedWhatsAppConversation(
        inbound=SimpleNamespace(),
        prepared_agent_request=SimpleNamespace(),
    )
    return service.invoke_prepared(prepared), processor


@pytest.mark.parametrize("name", ["confirm_order", "update_order_flow"])
def test_authoritative_submission_tools_replace_model_text_with_backend_confirmation(
    name,
):
    reply, _ = conversation_result(response(
        tool_call(name),
        text=(
            "Great! Order ID: ORD-FABRICATED, total Rs 9999, paid by card. "
            "Your order has been submitted."
        ),
    ))
    assert reply.submitted_order_id == ORDER_ID
    assert reply.reply == SUBMISSION_CONFIRMATION
    assert "ORD-FABRICATED" not in reply.reply
    assert "9999" not in reply.reply


def test_repricing_pending_confirmation_is_not_submission():
    reply, _ = conversation_result(response(
        tool_call(status="pending_confirmation"),
        text="Your order has been successfully submitted.",
    ))
    assert reply.submitted_order_id is None
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK


def test_failed_confirm_order_is_not_submission():
    reply, _ = conversation_result(response(
        tool_call(success=False),
        text="Your order was successfully submitted.",
    ))
    assert reply.submitted_order_id is None
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK


def test_nested_failed_result_is_not_submission():
    reply, _ = conversation_result(response(
        tool_call(success=True, result_success=False),
        text="Your order has been placed.",
    ))
    assert reply.submitted_order_id is None
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK


def test_production_incident_claim_is_blocked_and_safely_logged():
    fabricated_text = (
        "Your order has been successfully submitted!\n"
        "Order ID: ORD-123456789"
    )
    logger = RecordingLogger()
    reply, _ = conversation_result(
        response(text=fabricated_text, tool_calls=[]),
        logger=logger,
    )

    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK
    assert reply.submitted_order_id is None
    assert fabricated_text not in reply.reply
    assert logger.warnings == [(
        "Ungrounded order submission claim blocked",
        {
            "event": "ungrounded_order_submission_claim_blocked",
            "error_code": UNGROUNDED_ORDER_SUBMISSION_ERROR_CODE,
            "channel": "whatsapp",
            "request_id": "request-safe",
            "agent_session_id": "session-safe",
        },
    )]
    logged = repr(logger.warnings)
    assert "ORD-123456789" not in logged
    assert fabricated_text not in logged


@pytest.mark.parametrize(
    "text",
    [
        "Order successfully submitted.",
        "Your order is now placed.",
        "Your order was submitted successfully.",
        "We have confirmed your order.",
    ],
)
def test_equivalent_clear_submission_success_claims_are_blocked(text):
    reply, _ = conversation_result(response(text=text, tool_calls=[]))
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK
    assert reply.submitted_order_id is None


@pytest.mark.parametrize(
    "persisted_response",
    [
        response(tool_call("get_order_status", is_write=False)),
        response(
            text="Your order was submitted",
            tool_calls=[],
        ),
        response(
            tool_call("save_order_address", status="pending_confirmation"),
            write_succeeded=True,
        ),
        response(
            state={
                "orders": [{
                    "order_id": ORDER_ID,
                    "status": "submitted_to_restaurant",
                }]
            },
        ),
        response(
            data={
                "orders": [{
                    "order_id": ORDER_ID,
                    "status": "submitted_to_restaurant",
                }]
            },
        ),
    ],
)
def test_noncausal_text_read_tools_and_projected_state_are_not_trusted(
    persisted_response,
):
    reply, _ = conversation_result(persisted_response)
    assert reply.submitted_order_id is None


@pytest.mark.parametrize(
    "call",
    [
        None,
        "call",
        {},
        {"tool_name": "confirm_order", "success": True, "result": None},
        {"tool_name": "confirm_order", "success": True, "result": {}},
        {
            "tool_name": "confirm_order",
            "success": True,
            "result": {"data": None},
        },
        tool_call(order_id=""),
        tool_call(order_id="   "),
        tool_call(order_id=" ORD-NONCANONICAL "),
        tool_call(order_id=123),
    ],
)
def test_malformed_or_noncanonical_tool_results_are_ignored(call):
    persisted = response(
        call,
        text="Your order has been successfully submitted.",
    )
    reply, _ = conversation_result(persisted)

    assert submitted_order_id_from_response(persisted) is None
    assert reply.submitted_order_id is None
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK


@pytest.mark.parametrize(
    "call",
    [
        tool_call(include_agent=False),
        tool_call(confirmation_text=""),
        tool_call(confirmation_text="   "),
        tool_call(confirmation_text=" confirmation "),
        tool_call(agent_order_id="ORD-DIFFERENT"),
        tool_call(agent_order_id=""),
    ],
)
def test_missing_or_conflicting_authoritative_agent_evidence_fails_closed(call):
    reply, _ = conversation_result(response(
        call,
        text="Your order is confirmed and sent to the restaurant.",
    ))

    assert authoritative_order_submission_from_response(response(call)) is None
    assert reply.submitted_order_id is None
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK


@pytest.mark.parametrize(
    "persisted_response",
    [None, [], "response", {}, {"tool_calls": None}, {"tool_calls": {}}],
)
def test_malformed_response_envelope_returns_none(persisted_response):
    assert submitted_order_id_from_response(persisted_response) is None


def test_duplicate_proof_for_same_order_returns_that_order():
    persisted = response(
        tool_call("confirm_order"),
        tool_call("update_order_flow"),
    )
    assert submitted_order_id_from_response(persisted) == ORDER_ID


def test_duplicate_proof_for_same_order_requires_matching_confirmation():
    persisted = response(
        tool_call("confirm_order"),
        tool_call(
            "update_order_flow",
            confirmation_text="A conflicting backend confirmation",
        ),
        text="Your order has been successfully submitted.",
    )
    reply, _ = conversation_result(persisted)

    assert submitted_order_id_from_response(persisted) is None
    assert reply.submitted_order_id is None
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK


def test_distinct_submitted_order_proofs_are_ambiguous():
    persisted = response(
        tool_call(order_id="ORD-FIRST"),
        tool_call("update_order_flow", order_id="ORD-SECOND"),
    )
    assert submitted_order_id_from_response(persisted) is None


def test_distinct_submitted_order_proofs_block_submission_claim():
    persisted = response(
        tool_call(
            order_id="ORD-FIRST",
            agent_order_id="ORD-FIRST",
            confirmation_text="First trusted confirmation",
        ),
        tool_call(
            "update_order_flow",
            order_id="ORD-SECOND",
            agent_order_id="ORD-SECOND",
            confirmation_text="Second trusted confirmation",
        ),
        text="Your order has been placed.",
    )
    reply, _ = conversation_result(persisted)

    assert reply.submitted_order_id is None
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK


def test_extraction_does_not_mutate_persisted_response():
    persisted = response(tool_call())
    original = deepcopy(persisted)
    assert submitted_order_id_from_response(persisted) == ORDER_ID
    assert persisted == original


def test_completed_resumed_request_recovers_signal_without_other_services():
    persisted = response(tool_call())
    reply, processor = conversation_result(persisted, outcome="completed")

    assert reply.submitted_order_id == ORDER_ID
    assert reply.reply == SUBMISSION_CONFIRMATION
    assert reply.resumed is True
    assert len(processor.calls) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Should I confirm this order?",
        "Please confirm your order.",
        "Would you like me to submit the order?",
        "Your order is ready for confirmation.",
    ],
)
def test_confirmation_prompts_are_not_blocked(text):
    reply, _ = conversation_result(response(text=text, tool_calls=[]))
    assert reply.reply == text
    assert reply.submitted_order_id is None


def test_authoritative_existing_submitted_order_status_is_allowed_as_read():
    persisted = response(
        order_status_tool_call(),
        text=STATUS_MESSAGE,
    )
    reply, _ = conversation_result(persisted)

    assert reply.reply == STATUS_MESSAGE
    assert reply.submitted_order_id is None


@pytest.mark.parametrize(
    "persisted",
    [
        response(text=STATUS_MESSAGE, tool_calls=[]),
        response(
            order_status_tool_call(),
            text=(
                f"Your order {ORDER_ID} has been submitted to the restaurant."
            ),
        ),
        response(
            order_status_tool_call(message="Different authoritative status"),
            text=STATUS_MESSAGE,
        ),
    ],
)
def test_fabricated_or_paraphrased_submitted_status_is_not_trusted(persisted):
    reply, _ = conversation_result(persisted)
    assert reply.reply == UNGROUNDED_ORDER_SUBMISSION_FALLBACK
    assert reply.submitted_order_id is None


def test_reply_constructor_remains_backward_compatible():
    positional = WhatsAppConversationReply(
        "reply",
        "request",
        "session",
        "customer",
    )
    existing_keyword = WhatsAppConversationReply(
        reply="reply",
        request_id="request",
        session_id="session",
        customer_id="customer",
        resumed=True,
    )

    assert positional.resumed is False
    assert positional.submitted_order_id is None
    assert existing_keyword.resumed is True
    assert existing_keyword.submitted_order_id is None


def test_whatsapp_blocks_menu_session_effect_and_configured_menu_site_only(caplog):
    response = {
        "text": "Open https://menu.example.test/session/token?item=1",
        "tool_calls": [{
            "tool_name": "mixed_version_capability",
            "success": True,
            "result": {
                "success": True,
                "grounding": {"transactional_effects": ["menu_session_created"]},
            },
        }],
    }
    with caplog.at_level("WARNING"):
        reply = whatsapp_reply_from_response(
            response,
            request_id="request-1",
            session_id="session-1",
            customer_id="customer-1",
            menu_site_base_url="https://menu.example.test/session",
        )
    assert reply.reply == WHATSAPP_MENU_LINK_FORBIDDEN_FALLBACK
    assert any(
        getattr(record, "error_code", None) == WHATSAPP_MENU_LINK_FORBIDDEN_ERROR_CODE
        for record in caplog.records
    )
    assert "token" not in caplog.text


def test_whatsapp_allows_unrelated_urls_but_blocks_configured_menu_path():
    unrelated = whatsapp_reply_from_response(
        {"text": "See https://status.example.test/help", "tool_calls": []},
        request_id="r", session_id="s", customer_id="c",
        menu_site_base_url="https://menu.example.test/menu",
    )
    forbidden = whatsapp_reply_from_response(
        {"text": "See https://menu.example.test/menu/item?id=1", "tool_calls": []},
        request_id="r", session_id="s", customer_id="c",
        menu_site_base_url="https://menu.example.test/menu",
    )
    assert unrelated.reply == "See https://status.example.test/help"
    assert forbidden.reply == WHATSAPP_MENU_LINK_FORBIDDEN_FALLBACK
