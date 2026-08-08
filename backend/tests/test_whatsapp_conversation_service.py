from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.services.whatsapp_conversation_service import (
    PreparedWhatsAppConversation,
    WhatsAppConversationReply,
    WhatsAppConversationService,
    submitted_order_id_from_response,
)


ORDER_ID = "ORD-STRUCTURED-123"


def tool_call(
    tool_name="confirm_order",
    *,
    success=True,
    status="submitted_to_restaurant",
    order_id=ORDER_ID,
    is_write=True,
):
    return {
        "tool_name": tool_name,
        "success": success,
        "is_write": is_write,
        "result": {
            "success": success,
            "data": {"status": status, "order_id": order_id},
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


def conversation_result(persisted_response, *, outcome="completed"):
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
    )
    prepared = PreparedWhatsAppConversation(
        inbound=SimpleNamespace(),
        prepared_agent_request=SimpleNamespace(),
    )
    return service.invoke_prepared(prepared), processor


@pytest.mark.parametrize("name", ["confirm_order", "update_order_flow"])
def test_authoritative_submission_tools_return_structured_order_id(name):
    reply, _ = conversation_result(response(tool_call(name)))
    assert reply.submitted_order_id == ORDER_ID


def test_repricing_pending_confirmation_is_not_submission():
    reply, _ = conversation_result(response(tool_call(status="pending_confirmation")))
    assert reply.submitted_order_id is None


def test_failed_confirm_order_is_not_submission():
    reply, _ = conversation_result(response(tool_call(success=False)))
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
    assert submitted_order_id_from_response(response(call)) is None


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


def test_distinct_submitted_order_proofs_are_ambiguous():
    persisted = response(
        tool_call(order_id="ORD-FIRST"),
        tool_call("update_order_flow", order_id="ORD-SECOND"),
    )
    assert submitted_order_id_from_response(persisted) is None


def test_extraction_does_not_mutate_persisted_response():
    persisted = response(tool_call())
    original = deepcopy(persisted)
    assert submitted_order_id_from_response(persisted) == ORDER_ID
    assert persisted == original


def test_completed_resumed_request_recovers_signal_without_other_services():
    persisted = response(tool_call())
    reply, processor = conversation_result(persisted, outcome="completed")

    assert reply.submitted_order_id == ORDER_ID
    assert reply.resumed is True
    assert len(processor.calls) == 1


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
