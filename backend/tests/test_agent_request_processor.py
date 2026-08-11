from types import SimpleNamespace

from src.services.agent_request_processor import (
    AgentRequestProcessor,
    PreparedAgentRequest,
    build_response_builder,
)
from src.services.whatsapp_conversation_service import whatsapp_reply_from_response


class ForbiddenCall:
    def __call__(self, *_args, **_kwargs):
        raise AssertionError("completed AgentRequest must not invoke dependencies")


class DurableRequests:
    def __init__(self):
        self.record = {
            "request_id": "request-safe",
            "status": "processing",
            "invocation_state": "not_started",
        }
        self.claim_calls = 0

    def claim_invocation(self, _request_id):
        self.claim_calls += 1
        if self.record["invocation_state"] != "not_started":
            return False
        self.record["invocation_state"] = "invoking"
        return True

    def get(self, _request_id):
        return self.record

    def mark_invocation_ambiguous(self, _request_id):
        self.record["invocation_state"] = "ambiguous"
        return True

    def complete(self, _request_id, response):
        self.record.update(
            status="completed",
            invocation_state="completed",
            response=response,
        )
        return self.record

    def fail(self, _request_id, *, error_code, message):
        self.record.update(
            status="failed",
            invocation_state="failed",
            error_code=error_code,
            failure_message=message,
        )
        return self.record


def prepared_request(record):
    return PreparedAgentRequest(
        payload=SimpleNamespace(message="safe", branch_id=None),
        record=record,
        context=SimpleNamespace(
            channel="whatsapp",
            user_id="customer-safe",
            customer_id="customer-safe",
            agent_session_id="session-safe",
            customer_name=None,
            customer_phone=None,
        ),
        identity_state={"customer": {}},
    )


def test_completed_request_returns_persisted_response_without_agentcore():
    response = {"text": "Persisted reply", "tool_calls": []}
    record = {
        "request_id": "request-safe",
        "status": "completed",
        "response": response,
    }
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_requests=object()),
        agent_client_provider=ForbiddenCall(),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )

    result = processor.invoke_prepared(
        PreparedAgentRequest(SimpleNamespace(), record, SimpleNamespace(), {})
    )

    assert result.outcome == "completed"
    assert result.record["response"] is response


def test_agentcore_failure_retains_ambiguous_replay_protection(caplog):
    requests = DurableRequests()
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_requests=requests),
        agent_client_provider=lambda: SimpleNamespace(
            invoke=lambda _request: (_ for _ in ()).throw(
                RuntimeError("private runtime detail")
            )
        ),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )

    with caplog.at_level("ERROR"):
        result = processor.invoke_prepared(
            prepared_request(requests.record),
            ambiguous_on_invocation_failure=True,
        )

    assert result.outcome == "ambiguous"
    assert requests.record["invocation_state"] == "ambiguous"
    assert "private runtime detail" not in caplog.text


def test_agentcore_failure_is_recorded_when_replay_is_not_ambiguous():
    requests = DurableRequests()
    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_requests=requests),
        agent_client_provider=lambda: SimpleNamespace(
            invoke=lambda _request: (_ for _ in ()).throw(RuntimeError("boom"))
        ),
        identity_resolver=ForbiddenCall(),
        response_builder=ForbiddenCall(),
    )

    result = processor.invoke_prepared(prepared_request(requests.record))

    assert result.outcome == "failed"
    assert requests.record["error_code"] == "AGENT_INVOCATION_FAILED"


def test_successful_invocation_uses_only_conversation_request_surface():
    requests = DurableRequests()
    captured = {}

    def invoke(agent_request):
        captured["request"] = agent_request
        return SimpleNamespace(raw_result={}, text="safe response")

    processor = AgentRequestProcessor(
        services_provider=lambda: SimpleNamespace(agent_requests=requests),
        agent_client_provider=lambda: SimpleNamespace(invoke=invoke),
        identity_resolver=ForbiddenCall(),
        response_builder=lambda *_args: SimpleNamespace(
            model_dump=lambda **_kwargs: {"text": "safe response"}
        ),
    )

    result = processor.invoke_prepared(prepared_request(requests.record))

    assert result.outcome == "completed"
    assert requests.record["status"] == "completed"
    assert captured["request"].message == "safe"
    assert set(captured["request"].__dict__) == {
        "message",
        "user_id",
        "agent_session_id",
        "branch_id",
        "customer_id",
        "customer_name",
        "customer_phone",
        "channel",
        "request_id",
    }


def response_services():
    empty_cart = SimpleNamespace(
        model_dump=lambda **_kwargs: {"data": {"cart": None}}
    )
    empty_orders = SimpleNamespace(
        model_dump=lambda **_kwargs: {"data": {"orders": []}}
    )
    return SimpleNamespace(
        carts=SimpleNamespace(get_active_cart=lambda *_args: empty_cart),
        orders=SimpleNamespace(get_order_status=lambda *_args: empty_orders),
    )


def response_context(channel="whatsapp"):
    return SimpleNamespace(
        channel=channel,
        user_id="customer-1",
        customer_id="customer-1",
        agent_session_id="session-1",
    )


def build_response(*, text, tool_calls, channel="whatsapp"):
    builder = build_response_builder(response_services)
    return builder(
        response_context(channel),
        {"customer": {}},
        SimpleNamespace(text=text, raw_result={"tool_calls": tool_calls}),
    )


def test_backend_whatsapp_menu_read_preserves_main_agent_response():
    response = build_response(
        text="Pepperoni Passion is available for MYR 29.90.",
        tool_calls=[
            {
                "tool_name": "search_menu",
                "success": True,
                "is_write": False,
                "result": {
                    "success": True,
                    "data": {
                        "items": [
                            {
                                "product_id": "pepperoni-passion",
                                "name": "Pepperoni Passion",
                                "currency": "MYR",
                                "price": "29.90",
                            }
                        ]
                    },
                    "grounding": {"authoritative_domains": ["menu"]},
                },
            }
        ],
    )

    assert response.text == "Pepperoni Passion is available for MYR 29.90."


def test_backend_whatsapp_failed_write_uses_authoritative_failure():
    response = build_response(
        text="Your customization was saved.",
        tool_calls=[
            {
                "tool_name": "update_cart_item_customization",
                "success": False,
                "is_write": True,
                "result": {
                    "success": False,
                    "user_message": "That customization is unavailable.",
                },
            }
        ],
    )

    assert response.text == "That customization is unavailable."
    assert response.write_succeeded is False


def test_backend_whatsapp_successful_write_uses_exact_customer_artifact():
    confirmation = "Order ORD-123 was submitted to the restaurant."
    response = build_response(
        text="Your order is all set.",
        tool_calls=[
            {
                "tool_name": "confirm_order",
                "success": True,
                "is_write": True,
                "result": {
                    "success": True,
                    "user_message": confirmation,
                    "data": {
                        "status": "submitted_to_restaurant",
                        "order_id": "ORD-123",
                    },
                    "agent": {
                        "submitted_order_id": "ORD-123",
                        "submission_confirmation": confirmation,
                    },
                    "grounding": {
                        "transactional_effects": ["order_submitted"],
                        "exact_customer_text": confirmation,
                    },
                },
            }
        ],
    )

    assert response.text == confirmation
    assert response.write_succeeded is True


def test_object_runtime_result_preserves_submission_proof_to_customer_boundary():
    order_id = "ORD-OBJECT-123"
    confirmation = (
        "Your order has been submitted to the restaurant. "
        f"Order ID: {order_id}"
    )
    call = SimpleNamespace(
        tool_name="confirm_order",
        success=True,
        is_write=True,
        result={
            "success": True,
            "user_message": "The order was submitted.",
            "data": {
                "status": "submitted_to_restaurant",
                "order_id": order_id,
            },
            "agent": {
                "submitted_order_id": order_id,
                "submission_confirmation": confirmation,
            },
            "grounding": {
                "transactional_effects": ["order_submitted"],
            },
        },
        error_code=None,
    )
    builder = build_response_builder(response_services)

    response = builder(
        response_context(),
        {"customer": {}},
        SimpleNamespace(
            text=confirmation,
            raw_result=SimpleNamespace(tool_calls=[call]),
        ),
    )
    delivered = whatsapp_reply_from_response(
        response.model_dump(),
        request_id="request-safe",
        session_id="session-1",
        customer_id="customer-1",
    )

    assert response.text == confirmation
    assert len(response.tool_calls) == 1
    assert delivered is not None
    assert delivered.reply == confirmation
    assert delivered.submitted_order_id == order_id
