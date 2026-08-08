from types import SimpleNamespace

from src.services.agent_request_processor import (
    AgentRequestProcessor,
    PreparedAgentRequest,
)


class ForbiddenCall:
    def __call__(self, *_args, **_kwargs):
        raise AssertionError("completed AgentRequest must not invoke dependencies")


def test_completed_request_returns_persisted_structured_response_without_agentcore():
    response = {
        "text": "Persisted reply",
        "tool_calls": [{
            "tool_name": "confirm_order",
            "success": True,
            "is_write": True,
            "result": {
                "success": True,
                "data": {
                    "status": "submitted_to_restaurant",
                    "order_id": "ORD-PERSISTED-123",
                },
            },
        }],
    }
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
    prepared = PreparedAgentRequest(
        payload=SimpleNamespace(),
        record=record,
        context=SimpleNamespace(),
        identity_state={},
    )

    result = processor.invoke_prepared(prepared)

    assert result.outcome == "completed"
    assert result.record is record
    assert result.record["response"] is response
